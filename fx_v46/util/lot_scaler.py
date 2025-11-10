"""
Agentic Trader FX v4 — Dynamic Lot Scaler
-----------------------------------------
Scales position size between FX_MIN_LOTS and FX_MAX_LOTS
based on confidence and symbol trust.
"""

from fx_v4.trust.trust_engine import load_trust
from fx_v4.app.fx_env import ENV


def compute_lot(symbol: str, confidence: float) -> float:
    """Return dynamically scaled lot size for given confidence and trust."""
    min_lot = ENV.min_lots
    max_lot = ENV.max_lots
    dyn_enabled = ENV.dynamic_lots

    if not dyn_enabled:
        return min_lot

    trust_data = load_trust()
    trust = trust_data.get("trust", {}).get(symbol, 0.5)
    trust_weight = 0.5 + (trust - 0.5) * 1.2  # slightly amplify above/below avg

    # Scale factor 0–1 from confidence
    conf_factor = max(0.0, min(1.0, confidence))

    # Combined factor (weighted mean)
    scale_factor = (0.7 * conf_factor) + (0.3 * trust_weight)
    scale_factor = max(0.0, min(1.0, scale_factor))

    lot = min_lot + (max_lot - min_lot) * scale_factor
    return round(lot, 2)
