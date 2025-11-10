# ============================================================
# Agentic Trader XAU v4.6 — Decision Engine (ATR Adaptive)
# ============================================================

from __future__ import annotations
from xau_v46.trust.xau_trust_engine_v46 import adjusted_confidence
from xau_v46.util.logger import setup_logger
from xau_v46.app.xau_env_v46 import ENV

log = setup_logger("xau_decider_v46", level=ENV.get("LOG_LEVEL", "INFO").upper())


def decide_signal(features: dict, env) -> dict:
    """
    Decide trading signal (LONG/SHORT/NO TRADE) for XAUUSD.
    Adaptive logic based on RSI, EMA trend, and ATR% filtering.
    """
    symbol = features.get("symbol", "XAUUSD-ECNc")
    ema_fast = features.get("ema_fast")
    ema_slow = features.get("ema_slow")
    rsi = features.get("rsi")
    atr_pct = features.get("atr_pct", 0.0)
    raw_conf = features.get("confidence", 0.0)
    why = features.get("why", [])

    # --------------------------------------------------------
    # ATR Volatility band from ENV
    # --------------------------------------------------------
    atr_min = float(env.get("XAU_ATR_TARGET_MIN", 0.0010))
    atr_max = float(env.get("XAU_ATR_TARGET_MAX", 0.0040))

    # Adaptive confidence adjustment
    if atr_pct < atr_min:
        adj_factor = 0.7  # too quiet → reduce confidence
        why.append("atr_low_conf_reduced")
    elif atr_pct > atr_max:
        adj_factor = 0.6  # too volatile → reduce confidence
        why.append("atr_high_conf_reduced")
    else:
        adj_factor = 1.0  # within range
    conf = raw_conf * adj_factor

    # If volatility extreme (very high), still block completely
    if atr_pct > atr_max * 2.0:
        log.info("[VOL-FILTER] %s skipped (ATR%%=%.4f extreme > %.4f)", symbol, atr_pct, atr_max * 2.0)
        return {"preview": {"side": "", "confidence": 0.0, "why": ["extreme_volatility"]}}

    # --------------------------------------------------------
    # Determine side from EMA and RSI
    # --------------------------------------------------------
    side = ""
    if ema_fast > ema_slow and rsi >= 55:
        side = "LONG"
        why.append("ema_rsi_bull")
    elif ema_fast < ema_slow and rsi <= 45:
        side = "SHORT"
        why.append("ema_rsi_bear")
    else:
        side = ""
        why.append("mixed_or_neutral")

    # --------------------------------------------------------
    # Blend with trust engine
    # --------------------------------------------------------
    adj_conf = adjusted_confidence(conf, symbol, trust_weight=0.4)

    # --------------------------------------------------------
    # Define SL/TP dynamically based on ATR%
    # --------------------------------------------------------
    atr_pips = atr_pct * 10000.0
    sl_points = max(150, atr_pips * 1.5)   # adjust stop wider in volatility
    tp_points = max(300, atr_pips * 3.0)

    # --------------------------------------------------------
    # Final trade decision package
    # --------------------------------------------------------
    preview = {
        "side": side,
        "confidence": round(adj_conf, 2),
        "sl_points": round(sl_points, 1),
        "tp_points": round(tp_points, 1),
        "why": why,
    }

    log.info("[DECISION] %s side=%s conf=%.2f ATR%%=%.4f SL=%.1f TP=%.1f reason=%s",
             symbol, side, adj_conf, atr_pct, sl_points, tp_points, why)

    return {"preview": preview}
