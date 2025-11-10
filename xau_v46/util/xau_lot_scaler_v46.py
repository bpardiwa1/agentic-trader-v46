# ============================================================
# Agentic Trader XAU v4.6 — Dynamic Lot Scaler (Final)
# ============================================================
# Features:
#  • Confidence × Trust blended sizing
#  • ATR% volatility dampening
#  • Environment-driven min/max bounds
#  • Fully compatible with executor_v46
# ============================================================

from __future__ import annotations
from xau_v46.app.xau_env_v46 import ENV
from xau_v46.trust.xau_trust_engine_v46 import get_trust_score
from xau_v46.util.logger import setup_logger

log = setup_logger("xau_lot_scaler_v46", level="INFO")


def compute_lot(symbol: str,
                confidence: float,
                atr_pct: float | None = None) -> float:
    """
    Compute adaptive lot size based on:
      • Signal confidence (0-1)
      • Trust score memory (0-1)
      • ATR% volatility guardrail

    Returns float lot size.
    """

    # --- Base bounds from .env ---
    min_lot = float(ENV.get("XAU_MIN_LOTS", 0.05))
    max_lot = float(ENV.get("XAU_MAX_LOTS", 0.10))

    # --- Retrieve persistent trust score ---
    trust_score = get_trust_score(symbol)
    trust_weight = float(ENV.get("XAU_TRUST_WEIGHT", 0.4))  # blend ratio
    effective_conf = (confidence * (1 - trust_weight)) + (trust_score * trust_weight)

    # --- Confidence→lot scaling ---
    conf_scale = max(0.0, min(1.0, effective_conf))
    base_lot = min_lot + (max_lot - min_lot) * conf_scale

    # --- ATR dampening ---
    if atr_pct is not None:
        atr_min = float(ENV.get("XAU_ATR_TARGET_MIN", 0.0010))
        atr_max = float(ENV.get("XAU_ATR_TARGET_MAX", 0.0020))

        if atr_pct > atr_max:
            base_lot *= 0.7
            log.info("[LOT] %s ATR%%=%.4f → high vol dampened", symbol, atr_pct)
        elif atr_pct < atr_min:
            base_lot *= 1.1
            log.info("[LOT] %s ATR%%=%.4f → quiet market boost", symbol, atr_pct)

    # --- Clamp within limits ---
    final_lot = round(max(min_lot, min(max_lot, base_lot)), 2)

    log.info("[LOT] %s conf=%.2f trust=%.2f eff=%.2f atr%%=%.4f → lot=%.2f (%.2f-%.2f)",
             symbol, confidence, trust_score, effective_conf, (atr_pct or 0.0),
             final_lot, min_lot, max_lot)

    return final_lot
