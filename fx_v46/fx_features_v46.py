"""
Agentic Trader FX v4.6 — Feature Computation
--------------------------------------------
Builds signal features from MT5 bars:
EMA, RSI, ATR%, blended confidence & trust,
and dynamic lot hinting.
"""

from __future__ import annotations
import numpy as np
import pandas as pd
import MetaTrader5 as mt5  # type: ignore
from datetime import datetime

from fx_v46.app.fx_env_v46 import ENV
from fx_v46.util.fx_mt5_bars import get_bars
from fx_v46.util.fx_indicators import ema as compute_ema, rsi as compute_rsi
from fx_v46.trust.trust_engine_v46 import get_trust_level
from fx_v46.trust.trust_engine_v46 import adjusted_confidence
from fx_v46.util.logger import setup_logger

# Unified FX logging
_FX_LOG_DIR = "logs/fx_v4.6"
_FX_LOG_LEVEL = str(ENV.get("FX_LOG_LEVEL", "INFO")).upper()
_FX_LOG_NAME = f"fx_v46_{datetime.now():%Y-%m-%d}"
log = setup_logger(_FX_LOG_NAME, log_dir=_FX_LOG_DIR, level=_FX_LOG_LEVEL)

# ------------------------------------------------------------------
def compute_features(symbol: str, params: dict, env) -> dict | None:
    """Compute EMA/RSI/ATR features and build confidence signal."""
    try:
        df = get_bars(symbol, env.timeframe if hasattr(env, "timeframe") else "M15", 240)
        if df is None or len(df) < max(int(params.get("ema_slow", 50)), 50):
            log.warning("[DATA] Insufficient bars for %s", symbol)
            return None

        close = df["close"].to_numpy()
        price = close[-1]

        # --- Indicators (scalar-safe) ---
        ema_fast = compute_ema(close, int(params["ema_fast"]))
        ema_slow = compute_ema(close, int(params["ema_slow"]))
        rsi_arr = compute_rsi(close, int(params["rsi_period"]))

        # Always ensure iterable arrays (avoid scalar indexing)
        if np.isscalar(ema_fast):
            ema_fast = np.array([ema_fast])
        if np.isscalar(ema_slow):
            ema_slow = np.array([ema_slow])
        if np.isscalar(rsi_arr):
            rsi_arr = np.array([rsi_arr])

        ema_fast_val = float(ema_fast[-1])
        ema_slow_val = float(ema_slow[-1])
        ema_gap_val  = ema_fast_val - ema_slow_val   # 🧩 add gap back
        rsi_val = float(rsi_arr[-1])
        # NOTE: Use true ATR% (not EMA gap proxy) for volatility gating
        high = df["high"].to_numpy(dtype=float)
        low = df["low"].to_numpy(dtype=float)
        close_f = df["close"].to_numpy(dtype=float)
        # True Range
        prev_close = np.roll(close_f, 1)
        prev_close[0] = close_f[0]
        tr = np.maximum(high - low, np.maximum(np.abs(high - prev_close), np.abs(low - prev_close)))
        period_atr = int(env.get("FX_ATR_PERIOD", 14))
        if len(tr) >= period_atr + 1:
            atr = pd.Series(tr).rolling(period_atr).mean().to_numpy()
            atr_last = float(atr[-1]) if not np.isnan(atr[-1]) else float(np.nanmean(atr[-period_atr:]))
        else:
            atr_last = float(np.nan)
        atr_pct = float(atr_last / price) if price else float("nan")
        
        # ------------------------------------------------------------
        # H1 Trend Confluence (EMA fast/slow) — parity with IDX/XAU
        # ------------------------------------------------------------
        # ------------------------------------------------------------
        # H1 Trend Confluence (EMA fast/slow) — parity with IDX/XAU
        # ------------------------------------------------------------
        df_h1 = None
        ema_fast_h1 = None
        ema_slow_h1 = None
        trend_h1 = "UNKNOWN"

        try:
            # 1) First attempt
            df_h1 = get_bars(symbol, timeframe="H1", limit=240)

            # 2) If empty, force MT5 to load history and retry
            if df_h1 is None or len(df_h1) == 0:
                log.warning("[H1] %s H1 bars empty — forcing history_select and retry", symbol)
                try:
                    # ensure symbol is selected/visible
                    mt5.symbol_select(symbol, True)
                except Exception:
                    pass
                try:
                    # load some history into MT5 terminal
                    import datetime as dt
                    end = dt.datetime.now()
                    start = end - dt.timedelta(days=30)
                    mt5.history_select(start, end)
                except Exception:
                    pass

                df_h1 = get_bars(symbol, timeframe="H1", limit=240)

            # 3) Compute H1 trend if sufficient bars
            if df_h1 is not None and len(df_h1) > int(params.get("ema_slow", 50)):
                close_h1 = df_h1["close"].to_numpy(dtype=float)

                ema_fast_h1 = compute_ema(close_h1, int(params["ema_fast"]))
                ema_slow_h1 = compute_ema(close_h1, int(params["ema_slow"]))

                if np.isscalar(ema_fast_h1):
                    ema_fast_h1 = np.array([ema_fast_h1])
                if np.isscalar(ema_slow_h1):
                    ema_slow_h1 = np.array([ema_slow_h1])

                fast_last = float(ema_fast_h1[-1])
                slow_last = float(ema_slow_h1[-1])

                if fast_last > slow_last:
                    trend_h1 = "BULL"
                elif fast_last < slow_last:
                    trend_h1 = "BEAR"
                else:
                    trend_h1 = "UNKNOWN"
            else:
                trend_h1 = "UNKNOWN"

        except Exception as e:
            log.warning("[H1] %s H1 trend calc failed: %s", symbol, e)
            trend_h1 = "UNKNOWN"


        # Expose H1 debug details for watcher/diagnostics
        h1_len = int(len(df_h1)) if 'df_h1' in locals() and df_h1 is not None else 0
        h1_ema_fast_val = float(ema_fast_h1[-1]) if 'ema_fast_h1' in locals() and ema_fast_h1 is not None and len(np.atleast_1d(ema_fast_h1)) else float("nan")
        h1_ema_slow_val = float(ema_slow_h1[-1]) if 'ema_slow_h1' in locals() and ema_slow_h1 is not None and len(np.atleast_1d(ema_slow_h1)) else float("nan")



        # --- Trust & Confidence ---
        trust = get_trust_level(symbol)
        conf_raw = 0.0
        why = []

        if ema_fast_val > ema_slow_val and rsi_val > params["rsi_long_th"]:
            conf_raw = 0.6
            why.append("ema_rsi_bull")
        elif ema_fast_val < ema_slow_val and rsi_val < params["rsi_short_th"]:
            conf_raw = 0.6
            why.append("ema_rsi_bear")
        else:
            conf_raw = 0.3
            why.append("neutral")

        conf_adj = adjusted_confidence(conf_raw, symbol)
        conf_adj = float(np.clip(conf_adj, 0.0, 1.0))

        # --- Derived side ---
        side = None
        if conf_adj >= float(env.get("AGENT_MIN_CONFIDENCE", 0.55)):

            if ema_fast_val > ema_slow_val and rsi_val > 50:
                side = "LONG"
            elif ema_fast_val < ema_slow_val and rsi_val < 50:
                side = "SHORT"

        lot_hint = float(params.get("lots", 0.0) or 0.0)
        log.info(
            "[DEBUG] %s EMA_FAST=%.5f EMA_SLOW=%.5f GAP=%.5f RSI=%.2f ATR%%=%.5f | H1=%s(len=%d efast=%.5f eslow=%.5f) CONF=%.2f TRUST=%.2f LOT_HINT=%.2f WHY=%s",
            symbol, ema_fast_val, ema_slow_val, ema_gap_val, rsi_val, float(atr_pct),
            str(trend_h1), int(h1_len), float(h1_ema_fast_val), float(h1_ema_slow_val),
            conf_adj, trust, lot_hint, why
        )

        return {
            "symbol": symbol,
            "price": price,
            "ema_fast": ema_fast_val,
            "ema_slow": ema_slow_val,
            "ema_gap": ema_gap_val,        # 🧩 required for fx_decider_v46
            "rsi": rsi_val,
            "atr_pct": atr_pct,
            "trend_h1": trend_h1,
            "h1_len": h1_len,
            "h1_ema_fast": h1_ema_fast_val,
            "h1_ema_slow": h1_ema_slow_val,
            "trust": trust,
            "confidence": conf_adj,
            "side": side,
            "why": why,
        }

    except Exception as e:
        log.exception("[ERROR] compute_features failed for %s: %s", symbol, e)
        return None
