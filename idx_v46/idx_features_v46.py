# ============================================================
# Agentic Trader idx_v46 — Feature Computation (Env-Driven)
# ============================================================

from __future__ import annotations
import pandas as pd

from idx_v46.app.idx_env_v46 import ENV
from idx_v46.util.idx_mt5_bars_v46 import get_bars
from idx_v46.util.idx_logger_v46 import setup_logger
from idx_v46.util.idx_indicators_v46 import ema, rsi, atr
from idx_v46.trust.idx_trust_engine_v46 import adjusted_confidence

log = setup_logger("idx_features_v46", level=str(ENV.get("LOG_LEVEL", "INFO")))


def compute_features(symbol: str) -> dict | None:
    """
    Fetch MT5 bars, compute EMA/RSI/ATR indicators,
    derive normalized confidence metrics, and return feature dict.
    All parameters are environment-driven.
    """
    try:
        tf = str(ENV.get("IDX_TIMEFRAME", "M15"))
        n_bars = int(ENV.get("IDX_HISTORY_BARS", 240))

        ema_fast_p = int(ENV.get("IDX_EMA_FAST", 20))
        ema_slow_p = int(ENV.get("IDX_EMA_SLOW", 50))
        rsi_period = int(ENV.get("IDX_RSI_PERIOD", 14))
        atr_period = int(ENV.get("IDX_ATR_PERIOD", 14))

        df = get_bars(symbol, timeframe=tf, limit=n_bars)
        if df is None or len(df) < max(ema_fast_p, ema_slow_p, rsi_period, atr_period) + 1:
            log.warning("[DATA] %s insufficient bars", symbol)
            return None

        closes = df["close"].astype(float)
        price = float(closes.iloc[-1])
        ema_fast_val = ema(closes, ema_fast_p)
        ema_slow_val = ema(closes, ema_slow_p)
        rsi_val = rsi(closes, rsi_period)
        atr_val = atr(df, atr_period)

        atr_pct = float(atr_val / price) if price else 0.0
        ema_gap = float(ema_fast_val - ema_slow_val)

        # weighted confidence (EMA + RSI)
        ema_weight = float(ENV.get("IDX_CONF_BASE_WEIGHT_EMA", 0.5))
        rsi_weight = float(ENV.get("IDX_CONF_BASE_WEIGHT_RSI", 0.5))

        ema_score = min(1.0, abs(ema_gap) / (max(1e-9, atr_pct) * 2.0))
        rsi_score = abs(rsi_val - 50.0) / 50.0

        raw_conf = max(0.0, min(1.0, ema_weight * ema_score + rsi_weight * rsi_score))
        adj_conf = adjusted_confidence(
            raw_conf, symbol, trust_weight=float(ENV.get("IDX_TRUST_WEIGHT", 0.4))
        )

        out = {
            "symbol": symbol,
            "timeframe": tf,
            "price": price,
            "ema_fast": float(ema_fast_val),
            "ema_slow": float(ema_slow_val),
            "ema_gap": float(ema_gap),
            "rsi": float(rsi_val),
            "atr_pct": float(atr_pct),
            "raw_conf": round(raw_conf, 4),
            "adj_conf": round(adj_conf, 4),
        }

        log.info(
            "[FEAT] %s TF=%s EMAf=%.2f EMAs=%.2f RSI=%.2f ATR%%=%.4f RAW=%.2f ADJ=%.2f",
            symbol, tf, out["ema_fast"], out["ema_slow"], out["rsi"],
            out["atr_pct"], out["raw_conf"], out["adj_conf"]
        )
        return out

    except Exception as e:
        log.exception("[ERROR] compute_features failed for %s: %s", symbol, e)
        return None
