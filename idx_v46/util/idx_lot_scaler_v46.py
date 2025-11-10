# ============================================================
# Agentic Trader IDX v4.6 — Dynamic Lot Scaler
# ============================================================

from __future__ import annotations
from idx_v46.app.idx_env_v46 import ENV
from idx_v46.trust.idx_trust_engine_v46 import get_trust as get_trust_score
from idx_v46.util.logger import setup_logger

log = setup_logger("idx_lot_scaler_v46", level=ENV.get("LOG_LEVEL", "INFO"))


def compute_lot(symbol: str,
                confidence: float,
                atr_pct: float | None = None) -> float:
    """Adaptive lot size using confidence × trust × volatility."""

    min_lot = float(ENV.get("IDX_MIN_LOTS", 0.1))
    max_lot = float(ENV.get("IDX_MAX_LOTS", 0.3))

    trust_score = get_trust_score(symbol)
    trust_weight = float(ENV.get("IDX_TRUST_WEIGHT", 0.4))
    eff_conf = (confidence * (1 - trust_weight)) + (trust_score * trust_weight)

    conf_scale = max(0.0, min(1.0, eff_conf))
    base_lot = min_lot + (max_lot - min_lot) * conf_scale

    if atr_pct is not None:
        atr_min = float(ENV.get("IDX_ATR_TARGET_MIN", 0.001))
        atr_max = float(ENV.get("IDX_ATR_TARGET_MAX", 0.002))
        if atr_pct > atr_max:
            base_lot *= 0.7
            log.info("[LOT] %s ATR%%=%.4f → high vol dampened", symbol, atr_pct)
        elif atr_pct < atr_min:
            base_lot *= 1.1
            log.info("[LOT] %s ATR%%=%.4f → quiet market boost", symbol, atr_pct)

    final_lot = round(max(min_lot, min(max_lot, base_lot)), 2)
    log.info("[LOT] %s conf=%.2f trust=%.2f eff=%.2f atr%%=%.4f → lot=%.2f",
             symbol, confidence, trust_score, eff_conf, (atr_pct or 0.0), final_lot)
    return final_lot
