"""
Agentic Trader FX v4.6 — Dynamic Lot Scaler
--------------------------------------------
Computes adaptive lot sizes based on confidence and
environment constraints (min/max lots).
"""

from __future__ import annotations
from fx_v46.app.fx_env import ENV
from fx_v46.util.logger import setup_logger

log = setup_logger("lot_scaler_v46", level="INFO")


def compute_lot(symbol: str, confidence: float) -> float:
    """
    Dynamically scale lot size based on signal confidence.

    - Uses ENV.dynamic_lots flag to switch dynamic/static behavior.
    - Confidence ∈ [0.0, 1.0] maps to ENV.min_lots → ENV.max_lots.
    """

    try:
        if not ENV.dynamic_lots:
            # Use static per-symbol lot if available
            params = ENV.per.get(symbol)
            if params:
                lot = params.lots
            else:
                lot = (ENV.min_lots + ENV.max_lots) / 2
            log.debug("[STATIC] %s lot=%.3f (dynamic_lots=False)", symbol, lot)
            return round(lot, 3)

        # Confidence-based dynamic scaling
        conf = max(0.0, min(1.0, confidence))
        lot = ENV.min_lots + (ENV.max_lots - ENV.min_lots) * conf
        lot = round(lot, 3)

        log.debug("[DYNAMIC] %s conf=%.2f → lot=%.3f", symbol, conf, lot)
        return lot

    except Exception as e:
        log.warning("[FALLBACK] compute_lot failed for %s: %s", symbol, e)
        return round((ENV.min_lots + ENV.max_lots) / 2, 3)

