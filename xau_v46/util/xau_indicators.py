# ============================================================
# Agentic Trader v4.6 — IDX Indicator Engine
# ------------------------------------------------------------
# Purpose:
#   • Compute EMA fast/slow, RSI, and ATR for IDX.
#   • Output dict used by idx_decider_v46.
# ============================================================

from __future__ import annotations
import numpy as np
import pandas as pd

# ------------------------------------------------------------
# Core math helpers
# ------------------------------------------------------------
def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> float:
    delta = series.diff()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)
    avg_gain = up.rolling(period).mean()
    avg_loss = down.rolling(period).mean()
    rs = avg_gain / (avg_loss + 1e-9)
    rsi_val = 100 - (100 / (1 + rs))
    return float(rsi_val.iloc[-1])


def atr(df: pd.DataFrame, period: int = 14) -> float:
    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift()).abs()
    low_close = (df["low"] - df["close"].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return float(tr.rolling(period).mean().iloc[-1])


# ------------------------------------------------------------
# Main feature computation
# ------------------------------------------------------------
def compute_features(bars: pd.DataFrame,
                     ema_fast: int = 20,
                     ema_slow: int = 50,
                     rsi_period: int = 14,
                     atr_period: int = 14) -> dict:
    """
    Compute indicators and derived features for IDX.

    Returns:
        dict with ema_fast, ema_slow, ema_gap, rsi, atr_pct.
    """
    if len(bars) < max(ema_slow, rsi_period, atr_period) + 1:
        raise ValueError("Not enough bars for indicator computation.", len(bars))

    closes = bars["close"]
    ema_fast_val = float(ema(closes, ema_fast).iloc[-1])
    ema_slow_val = float(ema(closes, ema_slow).iloc[-1])
    rsi_val = rsi(closes, rsi_period)
    atr_val = atr(bars, atr_period)

    price = float(closes.iloc[-1])
    atr_pct = atr_val / price if price != 0 else 0.0
    ema_gap = ema_fast_val - ema_slow_val

    return {
        "ema_fast": ema_fast_val,
        "ema_slow": ema_slow_val,
        "ema_gap": ema_gap,
        "rsi": rsi_val,
        "atr_pct": atr_pct,
    }
