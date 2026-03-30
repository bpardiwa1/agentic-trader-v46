"""
Agentic Trader FX v4.6 — Dynamic Lot Scaler
--------------------------------------------
Computes adaptive lot sizes based on confidence and
environment constraints (min/max lots).
"""

from __future__ import annotations

from fx_v46.app.fx_env_v46 import ENV  # 🔁 fixed module name
from fx_v46.util.logger import setup_logger

log = setup_logger("lot_scaler_v46", level="INFO")


def _base_symbol(symbol: str) -> str:
    """Normalize broker symbol to logical base (EURUSD-ECNc -> EURUSD)."""
    return symbol.split("-", 1)[0]


def compute_lot(symbol: str, confidence: float) -> float:
    """
    Dynamically scale lot size based on signal confidence.

    - Uses ENV.dynamic_lots flag to switch dynamic/static behavior.
    - Confidence ∈ [0.0, 1.0] maps to ENV.min_lots → ENV.max_lots.
    - When dynamic_lots is False, uses per-symbol static lot from ENV.per.
    """
    try:
        # -----------------------------
        # Static mode — per-symbol lots
        # -----------------------------
        if not getattr(ENV, "dynamic_lots", False):
            base = _base_symbol(symbol)
            params = ENV.per.get(base)
            if params and hasattr(params, "lots"):
                lot = float(params.lots)
            else:
                lot = float(getattr(ENV, "min_lots", 0.01) + getattr(ENV, "max_lots", 0.30)) / 2.0
            lot = round(lot, 3)
            log.debug("[STATIC] %s base=%s lot=%.3f (dynamic_lots=False)", symbol, base, lot)
            return lot

        # -----------------------------
        # Dynamic mode — confidence map
        # -----------------------------
        conf = max(0.0, min(1.0, float(confidence)))
        min_lots = float(getattr(ENV, "min_lots", 0.03))
        max_lots = float(getattr(ENV, "max_lots", 0.30))

        lot = min_lots + (max_lots - min_lots) * conf
        lot = round(lot, 3)

        log.debug("[DYNAMIC] %s conf=%.2f → lot=%.3f (range=%.3f–%.3f)",
                  symbol, conf, lot, min_lots, max_lots)
        return lot

    except Exception as e:
        # Very conservative fallback
        min_lots = float(getattr(ENV, "min_lots", 0.03))
        max_lots = float(getattr(ENV, "max_lots", 0.30))
        lot = round((min_lots + max_lots) / 2.0, 3)
        log.warning("[FALLBACK] compute_lot failed for %s: %s -> lot=%.3f", symbol, e, lot)
        return lot
