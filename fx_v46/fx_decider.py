"""
Agentic Trader FX v4 — Auto-Decider
-----------------------------------
Generates trade signals with confidence scoring
based on multi-factor momentum regime analysis.
"""

from __future__ import annotations
from typing import Dict, Any
import numpy as np
from fx_v4.app.fx_env import FxEnv
from fx_v4.trust.trust_engine import adjust_confidence
from fx_v4.util.logger import setup_logger
# from fx_v4.trust.trust_engine import adjust_confidence

log = setup_logger("fx_decider", level="INFO")


"""
Agentic Trader FX v4 — Auto-Decider (Verbose)
---------------------------------------------
Adds full transparency for EMA, RSI, ATR, and confidence evolution.
"""





def decide_signal(feats: Dict[str, Any], env) -> Dict[str, Any]:
    symbol = feats["symbol"]
    price = feats["price"]
    ema_fast = feats["ema_fast"]
    ema_slow = feats["ema_slow"]
    rsi = feats["rsi"]
    atr = feats.get("atr", 0)
    atr_pct = feats.get("atr_pct", 0)
    params = feats["params"]

    reasons = []
    confidence = 0.0
    side = ""
    regime = ""

    # === EMA Alignment ===
    if ema_fast > ema_slow:
        regime = "BULL"
        confidence += 0.3
    elif ema_fast < ema_slow:
        regime = "BEAR"
        confidence += 0.3
    else:
        regime = "NEUTRAL"
        reasons.append("ema_flat")

    # === RSI Confirmation ===
    if rsi > params.rsi_long_th:
        if regime == "BULL":
            confidence += 0.3
            reasons.append("rsi_confirms_bull")
        else:
            reasons.append("rsi_mixed")
    elif rsi < params.rsi_short_th:
        if regime == "BEAR":
            confidence += 0.3
            reasons.append("rsi_confirms_bear")
        else:
            reasons.append("rsi_mixed")
    else:
        reasons.append("rsi_neutral")

    # === ATR Volatility Adjustment ===
    if env.atr_enabled and atr_pct > 0:
        if atr_pct < 0.0015:
            confidence -= 0.05
            reasons.append("low_vol")
        elif atr_pct > 0.004:
            confidence -= 0.05
            reasons.append("high_vol")
        else:
            reasons.append("stable_vol")

    # --- Trust-weighted confidence ---
    raw_conf = max(0.0, min(1.0, confidence))
    adj_conf = adjust_confidence(symbol, raw_conf)
    accepted = adj_conf >= env.min_conf

    if accepted:
        side = "LONG" if regime == "BULL" else "SHORT" if regime == "BEAR" else ""
    else:
        reasons.append("below_conf_threshold")

    note = f"{regime}_{'ALIGNED' if accepted else 'MIXED'}"

    # === Full diagnostic log ===
    log.info(
        "[DEBUG] %s EMA_FAST=%.5f EMA_SLOW=%.5f GAP=%.5f RSI=%.2f ATR%%=%.4f RAW_CONF=%.2f ADJ_CONF=%.2f WHY=%s",
        symbol, ema_fast, ema_slow, (ema_fast - ema_slow), rsi, atr_pct, raw_conf, adj_conf, reasons
    )

    return {
        "accepted": accepted,
        "preview": {
            "symbol": symbol,
            "side": side,
            "note": note,
            "confidence": round(adj_conf, 3),
            "why": reasons,
            "sl_pips": params.sl_pips,
            "tp_pips": params.tp_pips,
            "ema_fast": round(ema_fast, 5),
            "ema_slow": round(ema_slow, 5),
            "rsi": round(rsi, 2),
            "atr_pct": round(atr_pct, 4),
        },
    }
