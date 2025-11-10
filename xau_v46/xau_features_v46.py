# xau_v46/xau_features_v46.py
from __future__ import annotations
import numpy as np
import pandas as pd

from xau_v46.app.xau_env_v46 import ENV
from xau_v46.util.xau_mt5_bars import get_bars
from xau_v46.util.xau_indicators import ema, rsi, atr
from xau_v46.trust.xau_trust_engine_v46 import adjusted_confidence
from xau_v46.util.logger import setup_logger

log = setup_logger("xau_features_v46", level="INFO")

def _sigmoid(x: float) -> float:
    import math
    return 1.0 / (1.0 + math.exp(-x))

def _conf_from_indicators(rsi_val: float, ema_gap: float, atr_pct: float) -> tuple[float, list[str]]:
    why: list[str] = []
    # RSI contribution
    rsi_dist = (rsi_val - 50.0) / 25.0
    rsi_conf = _sigmoid(2.5 * rsi_dist)
    if rsi_val >= 55:   why.append("rsi_confirms_bull")
    elif rsi_val <= 45: why.append("rsi_confirms_bear")
    else:               why.append("rsi_neutral")

    # EMA alignment contribution (normalize by ATR%)
    sign = 1.0 if ema_gap > 0 else (-1.0 if ema_gap < 0 else 0.0)
    base = float(abs(ema_gap)) / float(atr_pct * 2.0 + 1e-9)
    mag = min(1.0, base)
    ema_conf = _sigmoid(2.0 * sign * mag)

    # Low-volatility penalty
    vol_pen = max(0.6, min(1.0, atr_pct / 0.0010))  # ~0.10% baseline

    raw = max(0.0, min(1.0, 0.5 * rsi_conf + 0.5 * ema_conf)) * vol_pen
    return raw, why

def compute_features(symbol: str) -> dict | None:
    """
    Returns a dict with:
      - rsi, ema_gap, atr_pct, ema_fast, ema_slow
      - price (last close), why (list[str]), raw_conf, adj_conf
    Pulls all periods/TFs from ENV with robust defaults.
    """
    try:
        timeframe = str(ENV.get("XAU_TIMEFRAME", "M15"))
        n_bars    = int(ENV.get("XAU_HISTORY_BARS", 240))

        ema_fast_p = int(ENV.get("XAU_EMA_FAST", 20))
        ema_slow_p = int(ENV.get("XAU_EMA_SLOW", 50))
        rsi_period = int(ENV.get("XAU_RSI_PERIOD", 14))
        atr_period = int(ENV.get("XAU_ATR_PERIOD", 14))

        bars = get_bars(symbol, timeframe=timeframe, limit=n_bars)
        if bars is None or len(bars) < max(ema_fast_p, ema_slow_p, rsi_period, atr_period) + 1:
            log.error("[ERROR] compute_features failed for %s: not enough bars (have=%s)", symbol, len(bars) if bars is not None else None)
            return None

        closes_series = bars["close"].astype(float)
        price = float(closes_series.iloc[-1])

        ema_fast_s = ema(closes_series, ema_fast_p)
        ema_slow_s = ema(closes_series, ema_slow_p)
        rsi_s      = rsi(closes_series, rsi_period)
        atr_val    = float(atr(bars, atr_period))
        atr_pct    = atr_val / price if price > 0 else 0.0

        ema_fast_v = float(ema_fast_s.iloc[-1] if hasattr(ema_fast_s, "iloc") else ema_fast_s)
        ema_slow_v = float(ema_slow_s.iloc[-1] if hasattr(ema_slow_s, "iloc") else ema_slow_s)
        rsi_v      = float(rsi_s.iloc[-1] if hasattr(rsi_s, "iloc") else rsi_s)
        atr_val    = float(atr(bars, atr_period))
        ema_gap    = float(ema_fast_v - ema_slow_v)

        raw_conf, why = _conf_from_indicators(rsi_v, ema_gap, atr_pct)
        adj_conf = adjusted_confidence(raw_conf, symbol, trust_weight=float(ENV.get("XAU_TRUST_WEIGHT", 0.4)))

        features = {
            "symbol": symbol,
            "timeframe": timeframe,
            "rsi": rsi_v,
            "ema_fast": ema_fast_v,
            "ema_slow": ema_slow_v,
            "ema_gap": ema_gap,
            "atr_pct": atr_pct,
            "price": price,
            "raw_conf": round(raw_conf, 4),
            "adj_conf": round(adj_conf, 4),
            "why": why,
        }

        log.info("[DEBUG] %s TF=%s EMA_FAST=%.2f EMA_SLOW=%.2f GAP=%.2f RSI=%.2f ATR%%=%.4f | RAW=%.2f ADJ=%.2f WHY=%s",
                 symbol, timeframe, ema_fast_v, ema_slow_v, ema_gap, rsi_v, atr_pct, features["raw_conf"], features["adj_conf"], why)
        return features

    except Exception as e:
        log.exception("[ERROR] compute_features failed for %s: %s", symbol, e)
        return None
