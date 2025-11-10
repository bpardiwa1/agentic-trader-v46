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

from fx_v46.util.fx_mt5_bars import get_bars
from fx_v46.util.fx_indicators import ema as compute_ema, rsi as compute_rsi
from fx_v46.trust.trust_engine_v46 import get_trust_level
from fx_v46.trust.trust_engine_v46 import adjusted_confidence
from fx_v46.util.logger import setup_logger

log = setup_logger("fx_features_v46", level="INFO")

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
        atr_pct = abs(ema_fast_val - ema_slow_val) / price

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

        log.info(
            "[DEBUG] %s EMA_FAST=%.5f EMA_SLOW=%.5f RSI=%.2f ATR%%=%.4f | CONF=%.2f TRUST=%.2f LOT=%.2f WHY=%s",
            symbol, ema_fast_val, ema_slow_val, rsi_val, atr_pct,
            conf_adj, trust, 0.0, why
        )

        return {
            "symbol": symbol,
            "price": price,
            "ema_fast": ema_fast_val,
            "ema_slow": ema_slow_val,
            "ema_gap": ema_gap_val,        # 🧩 required for fx_decider_v46
            "rsi": rsi_val,
            "atr_pct": atr_pct,
            "trust": trust,
            "confidence": conf_adj,
            "side": side,
            "why": why,
        }

    except Exception as e:
        log.exception("[ERROR] compute_features failed for %s: %s", symbol, e)
        return None
