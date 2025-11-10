# ============================================================
# Agentic Trader v4.6 — XAU Feature Builder
# ============================================================
# Computes EMA, RSI, ATR features for XAUUSD (gold)
# with ATR% normalization for volatility-aware decisions.
# ============================================================

from __future__ import annotations
import numpy as np  # noqa: F401
from fx_v46.util.logger import setup_logger
from xau_v46.app.xau_env_v46 import ENV
from xau_v46.util.xau_mt5_bars import get_bars
from xau_v46.util.xau_indicators import ema, rsi, atr

log = setup_logger("xau_features_v46", level="INFO")


# ============================================================
# Feature Computation
# ============================================================
def compute_features(symbol: str) -> dict:
    """
    Pulls M15 and H1 bars for XAUUSD and computes:
      - EMA_FAST / EMA_SLOW
      - RSI (H1)
      - ATR% (normalized)
      - EMA gap
      - Confidence hints
    """
    try:
        bars_m15 = get_bars(symbol, timeframe="M15", limit=int(ENV.get("BAR_HISTORY_BARS", 240)))
        bars_h1  = get_bars(symbol, timeframe="H1",  limit=int(ENV.get("BAR_HISTORY_BARS", 240)))
    

        if bars_m15 is None or bars_m15.empty:
            log.warning("[DATA] No bars for %s (M15)", symbol)
            return None

        close_m15 = bars_m15["close"].to_numpy(dtype=float)
        close_h1  = bars_h1["close"].to_numpy(dtype=float)

        # --- EMA and RSI ---
        ema_fast = ema(close_m15, period=int(ENV.get("EMA_FAST", 34)))
        ema_slow = ema(close_m15, period=int(ENV.get("EMA_SLOW", 89)))
        rsi_h1   = rsi(close_h1, period=int(ENV.get("RSI_PERIOD", 14)))

        ema_gap = ema_fast[-1] - ema_slow[-1]
        atr_val = atr(close_m15, period=int(ENV.get("ATR_PERIOD", 14)))
        atr_pct = (atr_val[-1] / close_m15[-1]) if close_m15[-1] != 0 else 0

        # --- Feature summary ---
        regime = "BULL" if ema_gap > 0 and rsi_h1[-1] > 55 else \
                 "BEAR" if ema_gap < 0 and rsi_h1[-1] < 45 else "NEUTRAL"

        feats = {
            "symbol": symbol,
            "price": float(close_m15[-1]),
            "ema_fast": float(ema_fast[-1]),
            "ema_slow": float(ema_slow[-1]),
            "rsi": float(rsi_h1[-1]),
            "ema_gap": float(ema_gap),
            "atr_pct": round(float(atr_pct), 6),
            "regime": regime,
            "why_local": [regime.lower()],
        }

        log.info(
            "[DEBUG] %s EMA_FAST=%.2f EMA_SLOW=%.2f RSI=%.2f ATR%%=%.4f regime=%s",
            symbol, feats["ema_fast"], feats["ema_slow"], feats["rsi"], feats["atr_pct"], regime,
        )
        return feats

    except Exception as e:
        log.exception("[ERROR] compute_features failed for %s: %s", symbol, e)
        return None
