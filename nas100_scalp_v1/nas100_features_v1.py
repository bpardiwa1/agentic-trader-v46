# ============================================================
# NAS100 Scalper v1 — Features (M1 entry + M5 bias)
# ============================================================

from __future__ import annotations

from typing import Any, Dict
import pandas as pd
import MetaTrader5 as mt5  # type: ignore

from nas100_scalp_v1.app.nas100_env_v1 import ENV


def _get_bars(symbol: str, timeframe: str, count: int) -> pd.DataFrame | None:
    tf = getattr(mt5, f"TIMEFRAME_{timeframe}", None)
    if tf is None:
        return None
    rates = mt5.copy_rates_from_pos(symbol, tf, 0, count)
    if rates is None or len(rates) == 0:
        return None
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    return df


def _ema(series: pd.Series, period: int) -> float:
    if series is None or len(series) < period:
        return float("nan")
    return float(series.ewm(span=period, adjust=False).mean().iloc[-1])


def _rsi(series: pd.Series, period: int = 14) -> float:
    if series is None or len(series) < period + 1:
        return float("nan")
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    rs = avg_gain / (avg_loss + 1e-12)
    r = 100.0 - (100.0 / (1.0 + rs))
    return float(r.iloc[-1])


def _atr(df: pd.DataFrame, period: int = 14) -> float:
    if df is None or len(df) < period + 2:
        return float("nan")
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)
    prev_close = close.shift(1)
    tr = (high - low).abs()
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    true_range = pd.concat([tr, tr2, tr3], axis=1).max(axis=1)
    return float(true_range.rolling(period).mean().iloc[-1])


def compute_features(symbol: str) -> Dict[str, Any] | None:
    tf_entry = str(ENV.get("SCALP_TF_ENTRY", "M1"))
    tf_bias = str(ENV.get("SCALP_TF_BIAS", "M5"))

    ema_fast = int(ENV.get("SCALP_EMA_FAST", 9))
    ema_slow = int(ENV.get("SCALP_EMA_SLOW", 21))
    rsi_period = int(ENV.get("SCALP_RSI_PERIOD", 14))
    atr_period = int(ENV.get("SCALP_ATR_PERIOD", 14))

    df1 = _get_bars(symbol, tf_entry, count=max(240, ema_slow + atr_period + 30))
    df5 = _get_bars(symbol, tf_bias, count=max(240, ema_slow + 30))
    if df1 is None or df5 is None or df1.empty or df5.empty:
        return None

    c1 = df1["close"].astype(float)
    c5 = df5["close"].astype(float)

    price = float(c1.iloc[-1])

    ema_f1 = _ema(c1, ema_fast)
    ema_s1 = _ema(c1, ema_slow)
    rsi1 = _rsi(c1, rsi_period)
    atr1 = _atr(df1, atr_period)
    atr_pct_1 = float(atr1 / price) if price else 0.0

    ema_f5 = _ema(c5, ema_fast)
    ema_s5 = _ema(c5, ema_slow)

    bias_side = "LONG" if ema_f5 > ema_s5 else ("SHORT" if ema_f5 < ema_s5 else "")

    return {
        "symbol": symbol,
        "price": price,
        "tf_entry": tf_entry,
        "tf_bias": tf_bias,
        "ema_fast_m1": float(ema_f1),
        "ema_slow_m1": float(ema_s1),
        "ema_gap_m1": float(ema_f1 - ema_s1),
        "rsi_m1": float(rsi1),
        "atr_pct_m1": float(atr_pct_1),
        "ema_fast_m5": float(ema_f5),
        "ema_slow_m5": float(ema_s5),
        "bias_side": bias_side,
        "bar_time_m1": str(df1["time"].iloc[-1]),
        "bar_time_m5": str(df5["time"].iloc[-1]),
    }