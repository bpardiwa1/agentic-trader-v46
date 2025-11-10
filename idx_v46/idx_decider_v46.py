# ============================================================
# Agentic Trader IDX v4.6 — Decision Engine
# ------------------------------------------------------------
# Determines trade side, confidence, and SL/TP from features.
# Mirrors xau_decider_v46 log and return structure.
# ============================================================

from __future__ import annotations
import math
from typing import Any, Dict
from idx_v46.app.idx_env_v46 import ENV
from idx_v46.util.logger import setup_logger

log = setup_logger("idx_decider_v46", level=ENV.get("LOG_LEVEL", "INFO").upper())


def decide(features: Dict[str, Any], env=ENV) -> Dict[str, Any]:
    """Determine trade decision with confidence and reasoning."""
    sym = features.get("symbol")
    if not features.get("ok"):
        return {"accepted": False, "note": "no_features", "why": ["missing_data"]}

    price = float(features.get("price", 0.0))
    efast = float(features.get("ema_fast", 0.0))
    eslow = float(features.get("ema_slow", 0.0))
    rsi = float(features.get("rsi", 0.0))
    atr_pct = float(features.get("atr_pct", 0.0))

    rsi_long = float(env.get("INDICES_RSI_LONG_TH", 60))
    rsi_short = float(env.get("INDICES_RSI_SHORT_TH", 40))
    eps = float(env.get("INDICES_EPS", 10.0))
    sl = float(env.get("INDICES_SL", 100.0))
    tp = float(env.get("INDICES_TP", 200.0))

    # ------------------------------------------------------------
    # Determine regime
    # ------------------------------------------------------------
    bull = efast > eslow and rsi >= rsi_long
    bear = efast < eslow and rsi <= rsi_short

    side = ""
    why = []
    if bull:
        side, why = "LONG", ["ema_rsi_bull"]
    elif bear:
        side, why = "SHORT", ["ema_rsi_bear"]
    else:
        side, why = "", ["rsi_neutral", "mixed_or_neutral"]

    # ------------------------------------------------------------
    # Compute confidence metrics
    # ------------------------------------------------------------
    def _norm(x, a, b):
        if b <= a:
            return 0.0
        return max(0.0, min(1.0, (x - a) / (b - a)))

    ema_gap = abs(efast - eslow)
    conf_raw = _norm(ema_gap, 0.0, eps)
    conf_rsi = (
        _norm(rsi - rsi_long, 0.0, 10.0) if side == "LONG"
        else _norm(rsi_short - rsi, 0.0, 10.0) if side == "SHORT"
        else 0.0
    )
    conf_adj = 0.3 + 0.5 * conf_raw + 0.2 * conf_rsi
    conf_adj = max(0.0, min(1.0, conf_adj))

    # ------------------------------------------------------------
    # Confidence filter / gate
    # ------------------------------------------------------------
    min_conf = float(env.get("IDX_MIN_CONFIDENCE", 0.55))
    accepted = bool(side) and conf_adj >= min_conf

    # ------------------------------------------------------------
    # ATR-based SL/TP feed-through (NEW)
    # ------------------------------------------------------------
    sl_atr = features.get("sl_pips_atr")
    tp_atr = features.get("tp_pips_atr")
    if sl_atr and tp_atr:
        sl = sl_atr
        tp = tp_atr
        log.debug(
            "[SLTP] %s ATR-based overrides applied → SL=%.1f TP=%.1f",
            sym, sl, tp
        )
    else:
        log.debug(
            "[SLTP] %s using static SL/TP → SL=%.1f TP=%.1f (no ATR-based values)",
            sym, sl, tp
        )

    # ------------------------------------------------------------
    # Log decision (same style as xau_decider_v46)
    # ------------------------------------------------------------
    log.info(
        "[DECISION] %s side=%s conf=%.2f ATR%%=%.4f SL=%.1f TP=%.1f reason=%s",
        sym, side or "none", conf_adj, atr_pct, sl, tp, why
    )

    # ------------------------------------------------------------
    # Return decision structure
    # ------------------------------------------------------------
    if not accepted:
        return {
            "accepted": False,
            "side": side,
            "confidence_raw": conf_raw,
            "confidence_adj": conf_adj,
            "confidence": conf_adj,
            "atr_pct": atr_pct,
            "sl": sl,
            "tp": tp,
            "why": why,
        }

    return {
        "accepted": True,
        "side": side,
        "confidence_raw": conf_raw,
        "confidence_adj": conf_adj,
        "confidence": conf_adj,
        "atr_pct": atr_pct,
        "sl": sl,
        "tp": tp,
        "why": why,
    }
