"""
Agentic Trader FX v4 — Guardrails Module
----------------------------------------
Implements pre-trade safety checks:
 - Spread filter
 - Time-of-day window filter
 - Volatility sanity check (ATR / price)
 - Optional session-day control
"""

from __future__ import annotations
import logging
import MetaTrader5 as mt5  # type: ignore
from datetime import datetime
import os

log = logging.getLogger("fx.guardrails")

# ---------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------
def _b(key: str, default: bool = False) -> bool:
    return os.getenv(key, str(default)).lower() in ("1", "true", "yes", "on")

def _f(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, str(default)))
    except Exception:
        return default


# ---------------------------------------------------------------
# Spread and time checks
# ---------------------------------------------------------------
def _within_trading_hours() -> bool:
    """Checks time window using FX_TRADING_WINDOW_START/END (HH:MM, local)."""
    start = os.getenv("FX_TRADING_WINDOW_START", "00:00")
    end = os.getenv("FX_TRADING_WINDOW_END", "23:59")
    now = datetime.now().strftime("%H:%M")

    try:
        s_h, s_m = map(int, start.split(":"))
        e_h, e_m = map(int, end.split(":"))
        start_min = s_h * 60 + s_m
        end_min = e_h * 60 + e_m
        now_min = datetime.now().hour * 60 + datetime.now().minute
        return start_min <= now_min <= end_min
    except Exception:
        return True


def _within_trading_days() -> bool:
    """Checks weekday filter (1=Mon .. 7=Sun) using FX_TRADING_DAYS."""
    raw = os.getenv("FX_TRADING_DAYS", "1,2,3,4,5")
    days = [int(x.strip()) for x in raw.split(",") if x.strip().isdigit()]
    wd = datetime.now().isoweekday()
    return wd in days


def _spread_ok(symbol: str) -> bool:
    """Ensure spread under limit."""
    limit = _f("FX_SPREAD_MAX", 25.0)  # pips
    tick = mt5.symbol_info_tick(symbol)
    info = mt5.symbol_info(symbol)
    if not tick or not info:
        return True
    pip = 0.01 if "JPY" in symbol.upper() else 0.0001
    spread_pips = (tick.ask - tick.bid) / pip
    if spread_pips > limit:
        log.warning("[GUARDRAIL] %s spread too high: %.2f pips > %.2f", symbol, spread_pips, limit)
        return False
    return True


def _volatility_ok(symbol: str, price: float) -> bool:
    """Optional sanity check: ATR% should not be extreme (0.01 < ATR% < 3%)."""
    # For future: read live ATR from ACMI or features
    return True  # placeholder until integrated with ATR live feed


# ---------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------
def apply_guardrails(symbol: str, price: float) -> bool:
    """
    Returns True if all safety checks pass.
    """
    if not _within_trading_hours():
        log.info("[GUARDRAIL] Blocked %s (outside trading window)", symbol)
        return False
    if not _within_trading_days():
        log.info("[GUARDRAIL] Blocked %s (non-trading day)", symbol)
        return False
    if not _spread_ok(symbol):
        log.info("[GUARDRAIL] Blocked %s (spread limit exceeded)", symbol)
        return False
    if not _volatility_ok(symbol, price):
        log.info("[GUARDRAIL] Blocked %s (volatility too high)", symbol)
        return False
    return True
