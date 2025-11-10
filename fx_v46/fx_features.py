"""
Agentic Trader FX v4.6+ — Feature Computation & Confidence Tracker
------------------------------------------------------------------
Computes EMA, RSI, ATR%, and logs Confidence + Trust + Lot sizing hints.
"""

from __future__ import annotations
import pandas as pd
import numpy as np
from fx_v46.util.fx_mt5_bars import get_bars
from fx_v46.util.fx_indicators import ema as compute_ema, rsi as compute_rsi
from fx_v46.trust.trust_engine_v46 import get_trust_level
from fx_v46.util.lot_scaler import compute_lot
from fx_v46.util.logger import setup_logger

log = setup_logger("fx_features", level="INFO")


# -------------------------------------------------------------
# ATR helper
# -------------------------------------------------------------
def _atr(df: pd.DataFrame, period: int = 14) -> float:
    """Compute ATR using rolling mean of True Range."""
    if len(df) < period + 1:
        return 0.0

    high, low, close = df["high"], df["low"], df["close"]
    tr = np.maximum(
        high - low,
        np.maximum(abs(high - close.shift(1)), abs(low - close.shift(1))),
    )
    atr_val = tr.rolling(period).mean().iloc[-1]
    return float(atr_val if not np.isnan(atr_val) else 0.0)


# -------------------------------------------------------------
# Core feature computation
# -------------------------------------------------------------
def compute_features(symbol: str, params, env) -> dict | None:
    """
    Compute EMA fast/slow, RSI, ATR%, and estimated Confidence/Trust
    for diagnostic visibility.
    """
    tf = env.timeframe
    df = get_bars(symbol, tf, 200)

    if df is None or len(df) < 60:
        log.warning("[DATA] Insufficient bars for %s (%d rows)", symbol, 0 if df is None else len(df))
        return None

    close = df["close"]
    price = float(close.iloc[-1])

    ef = int(getattr(params, "ema_fast", 20))
    es = int(getattr(params, "ema_slow", 50))
    rsiper = int(getattr(params, "rsi_period", 14))
    atrper = int(getattr(env, "atr_period", 14))

    # --- Compute indicators ---
    ema_f = compute_ema(close.tolist(), ef)
    ema_s = compute_ema(close.tolist(), es)
    rsi_val = compute_rsi(close.tolist(), rsiper)
    atr_val = _atr(df, atrper)
    atr_pct = atr_val / price if price else 0.0
    ema_gap = ema_f - ema_s

    # --- Derived confidence score (simplified)
    conf_raw = abs(ema_gap) / (atr_pct * 10 + 1e-6)
    conf_norm = min(1.0, max(0.0, conf_raw))  # normalized [0,1]

    # --- Trust lookup + lot scaling preview ---
    trust_lvl = get_trust_level(symbol)
    dyn_lot = compute_lot(symbol, conf_norm)

    # --- Debug log: all live analytics ---
    log.info(
        "[DEBUG] %s EMA_FAST=%.5f EMA_SLOW=%.5f GAP=%.5f RSI=%.2f ATR%%=%.4f | CONF=%.2f TRUST=%.2f LOT=%.2f",
        symbol, ema_f, ema_s, ema_gap, rsi_val, atr_pct, conf_norm, trust_lvl, dyn_lot,
    )

    return {
        "symbol": symbol,
        "price": price,
        "ema_fast": ema_f,
        "ema_slow": ema_s,
        "ema_gap": ema_gap,
        "rsi": rsi_val,
        "atr": atr_val,
        "atr_pct": atr_pct,
        "confidence": conf_norm,
        "trust": trust_lvl,
        "lot_hint": dyn_lot,
    }
