# ============================================================
# Agentic Trader v4.6 — IDX Indicators Utility
# ------------------------------------------------------------
# Provides reusable technical indicator functions for EMA, RSI,
# and ATR calculations. Imported by idx_features_v46.py.
# ============================================================

from __future__ import annotations
import pandas as pd
import numpy as np

def ema(series: pd.Series, period: int) -> float:
    if len(series) < period:
        return float("nan")
    return float(series.ewm(span=period, adjust=False).mean().iloc[-1])

def rsi(series: pd.Series, period: int = 14) -> float:
    if len(series) < period + 1:
        return float("nan")
    delta = series.diff()
    up = delta.clip(lower=0).rolling(period).mean()
    dn = (-delta.clip(upper=0)).rolling(period).mean()
    rs = up / (dn.replace(0, np.nan))
    rsi_val = 100 - (100 / (1 + rs))
    return float(rsi_val.iloc[-1])

def atr(df: pd.DataFrame, period: int = 14) -> float:
    if len(df) < period + 1:
        return float("nan")
    h, l, c1 = df["high"], df["low"], df["close"].shift(1)
    tr = pd.concat([(h - l), (h - c1).abs(), (l - c1).abs()], axis=1).max(axis=1)
    return float(tr.rolling(period).mean().iloc[-1])
