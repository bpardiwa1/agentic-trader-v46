from __future__ import annotations

import numpy as np
import pandas as pd

EPS = 1e-12

def ema(series: pd.Series, period: int) -> float:
    if series is None or len(series) < period:
        return float("nan")
    return float(series.ewm(span=period, adjust=False).mean().iloc[-1])

def rsi(series: pd.Series, period: int = 14) -> float:
    if series is None or len(series) < period + 1:
        return float("nan")
    delta = series.diff()
    gains = delta.clip(lower=0.0)
    losses = (-delta).clip(lower=0.0)

    avg_gain = gains.rolling(period).mean()
    avg_loss = losses.rolling(period).mean()

    rs = avg_gain / (avg_loss + EPS)
    r = 100.0 - (100.0 / (1.0 + rs))
    return float(r.iloc[-1])

def atr(df, period: int = 14) -> float:
    if df is None or len(df) < period + 2:
        return float("nan")
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)
    prev_close = close.shift(1)

    tr = np.maximum(high - low, np.maximum((high - prev_close).abs(), (low - prev_close).abs()))
    return float(pd.Series(tr).rolling(period).mean().iloc[-1])