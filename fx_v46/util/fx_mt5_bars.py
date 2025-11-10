# fx_v4/util/fx_mt5_bars.py
"""
Agentic Trader FX v4 — Reliable MT5 OHLC Fetcher
------------------------------------------------
- Safe MetaTrader5 initialization & reconnection
- Timeframe resolver (e.g., "M1","M5","M15","H1","H4","D1")
- Symbol visibility handling (symbol_select)
- Bounded retries with short backoff
- Returns tidy pandas.DataFrame with datetime index

Usage:
    from fx_v4.util.fx_mt5_bars import get_bars
    df = get_bars("EURUSD-ECNc", "M15", 300)
"""

from __future__ import annotations

import time
import logging
from typing import Optional, Tuple

import pandas as pd
import MetaTrader5 as mt5  # type: ignore
from fx_v4.app.fx_env import resolve_symbol

log = logging.getLogger("fx.mt5bars")

# -----------------------------
# MT5 lifecycle helpers
# -----------------------------
def _mt5_ready() -> bool:
    try:
        info = mt5.terminal_info()
        ver = mt5.version()
        return bool(info and ver)
    except Exception:
        return False

def _mt5_ensure_initialized() -> bool:
    if _mt5_ready():
        return True
    try:
        mt5.shutdown()
    except Exception:
        pass
    ok = mt5.initialize()
    if not ok:
        log.warning("[MT5] initialize() failed: %s", mt5.last_error())
    return _mt5_ready()

# -----------------------------
# Timeframe resolution
# -----------------------------
_TF_MAP = {
    "M1": mt5.TIMEFRAME_M1,
    "M2": mt5.TIMEFRAME_M2,
    "M3": mt5.TIMEFRAME_M3,
    "M4": mt5.TIMEFRAME_M4,
    "M5": mt5.TIMEFRAME_M5,
    "M6": mt5.TIMEFRAME_M6,
    "M10": mt5.TIMEFRAME_M10,
    "M12": mt5.TIMEFRAME_M12,
    "M15": mt5.TIMEFRAME_M15,
    "M20": mt5.TIMEFRAME_M20,
    "M30": mt5.TIMEFRAME_M30,
    "H1": mt5.TIMEFRAME_H1,
    "H2": mt5.TIMEFRAME_H2,
    "H3": mt5.TIMEFRAME_H3,
    "H4": mt5.TIMEFRAME_H4,
    "H6": mt5.TIMEFRAME_H6,
    "H8": mt5.TIMEFRAME_H8,
    "H12": mt5.TIMEFRAME_H12,
    "D1": mt5.TIMEFRAME_D1,
    "W1": mt5.TIMEFRAME_W1,
    "MN1": mt5.TIMEFRAME_MN1,
}

def _normalize_tf(tf: str) -> str:
    s = tf.strip().upper().replace("TF_", "").replace("TIMEFRAME_", "")
    return s

def _resolve_tf(tf: str) -> Optional[int]:
    norm = _normalize_tf(tf)
    return _TF_MAP.get(norm)

# -----------------------------
# Symbol helpers
# -----------------------------
def _resolve_symbol(symbol: str) -> str:
    """Basic resolver: try exact, then strip broker suffix after '-' if not found."""
    if mt5.symbol_info(symbol):
        return symbol
    base = symbol.split("-", 1)[0]
    return base if mt5.symbol_info(base) else symbol

def _ensure_symbol_visible(symbol: str) -> bool:
    info = mt5.symbol_info(symbol)
    if info and getattr(info, "visible", True):
        return True
    return bool(mt5.symbol_select(symbol, True))

# -----------------------------
# Core fetch with retries
# -----------------------------
def _fetch_rates(symbol: str, tf_const: int, count: int) -> Optional[pd.DataFrame]:
    
    rates = mt5.copy_rates_from_pos(symbol, tf_const, 0, count)
    if rates is None:
        return None
    df = pd.DataFrame(rates)
    if df.empty:
        return None
    # Normalize time column
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df.set_index("time", inplace=True)
    # Keep standard columns if present
    keep = [c for c in ["open", "high", "low", "close", "tick_volume", "spread", "real_volume"] if c in df.columns]
    return df[keep]

def get_bars(symbol: str, timeframe: str, count: int, retries: int = 3, sleep_sec: float = 0.4) -> Optional[pd.DataFrame]:
    """
    Retrieve OHLC bars for `symbol` at `timeframe` with safe retries.
    - symbol: broker symbol (e.g., "EURUSD-ECNc", "GBPUSD", etc.)
    - timeframe: "M1","M5","M15","H1","H4","D1",...
    - count: number of bars (e.g., 240)
    Returns: pandas.DataFrame indexed by datetime or None on failure.
    """
    if count <= 0:
        raise ValueError("count must be > 0")
   
    # --- Alias resolution ---
    symbol = resolve_symbol(symbol)
    
    if not _mt5_ensure_initialized():
        log.error("[MT5] Not initialized; cannot fetch bars.")
        return None

    tf_const = _resolve_tf(timeframe)
    if tf_const is None:
        log.error("[MT5] Unknown timeframe '%s'", timeframe)
        return None

    resolved = _resolve_symbol(symbol)
    if not _ensure_symbol_visible(resolved):
        log.error("[MT5] Symbol not visible/unknown: %s", resolved)
        return None

    last_err: Optional[Tuple[int, str]] = None

    for attempt in range(1, retries + 1):
        try:
            df = _fetch_rates(resolved, tf_const, count)
            if df is not None and not df.empty:
                if attempt > 1:
                    log.info("[MT5] Fetched bars for %s (%s) after %d attempt(s).", resolved, timeframe, attempt)
                return df
            # Empty/None -> retry after short sleep + re-init
            last_err = mt5.last_error()
            log.debug("[MT5] Empty bars on attempt %d for %s %s; last_error=%s", attempt, resolved, timeframe, last_err)
        except Exception as e:
            log.debug("[MT5] Exception on attempt %d for %s %s: %s", attempt, resolved, timeframe, e)

        time.sleep(sleep_sec)
        _mt5_ensure_initialized()
        _ensure_symbol_visible(resolved)

    log.warning("[MT5] Failed to fetch bars for %s %s after %d attempts. last_error=%s", resolved, timeframe, retries, last_err)
    return None
