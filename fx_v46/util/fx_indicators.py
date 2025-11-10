"""
Agentic Trader FX v4 - Technical Indicators
-------------------------------------------
Implements EMA, RSI, and ATR indicators using NumPy.
"""

import numpy as np
# import math

def ema(values: np.ndarray, period: int) -> float:
    if values is None or len(values) < period:
        return float("nan")
    k = 2.0 / (period + 1.0)
    e = float(np.mean(values[:period]))
    for v in values[period:]:
        e = (v - e) * k + e
    return round(e, 6)

def rsi(values: np.ndarray, period: int = 14) -> float:
    if values is None or len(values) <= period:
        return float("nan")
    diff = np.diff(values)
    gain = np.where(diff > 0, diff, 0.0)
    loss = np.where(diff < 0, -diff, 0.0)
    avg_gain = np.mean(gain[:period])
    avg_loss = np.mean(loss[:period])
    for i in range(period, len(diff)):
        avg_gain = (avg_gain * (period - 1) + gain[i]) / period
        avg_loss = (avg_loss * (period - 1) + loss[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100.0 - (100.0 / (1.0 + rs)), 2)

def atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> float:
    if len(high) != len(low) or len(close) != len(high) or len(close) < period + 1:
        return float("nan")
    trs = np.zeros(len(close) - 1)
    prev_close = close[0]
    for i in range(1, len(close)):
        tr = max(high[i] - low[i], abs(high[i] - prev_close), abs(low[i] - prev_close))
        trs[i - 1] = tr
        prev_close = close[i]
    atr_val = np.mean(trs[:period])
    for t in trs[period:]:
        atr_val = (atr_val * (period - 1) + t) / period
    return round(atr_val, 6)
