# ============================================================
# Agentic Trader v4.6 - XAU Lot Scaler
# ============================================================
# Independent dynamic lot sizing engine for XAUUSD and metals.
# Scales volume based on confidence, ATR% range, and environment limits.
# ============================================================

from __future__ import annotations
from math import sqrt
from xau_v46.app.xau_env_v46 import ENV


def compute_lot(symbol: str, confidence: float, atr_pct: float | None = None) -> float:
    """
    Compute adaptive lot size for metals (e.g., XAUUSD).

    Rules:
    - Base range from ENV (default 0.05–0.10)
    - Scales upward with confidence and lower ATR%
    - Avoids over-leveraging in high-vol regimes
    """
    lot_min = float(ENV.get("XAU_MIN_LOTS", 0.05))
    lot_max = float(ENV.get("XAU_MAX_LOTS", 0.10))
    atr_baseline = 0.0015  # 0.15% default ATR% normalization

    # Confidence curve (smooth exponential)
    conf_factor = min(1.0, max(0.0, confidence))
    conf_curve = sqrt(conf_factor)  # smoother growth near high confidence

    # Volatility penalty (less size if volatility high)
    if atr_pct is not None and atr_pct > 0:
        vol_adj = min(1.0, atr_baseline / atr_pct)
    else:
        vol_adj = 1.0

    scaled = lot_min + (lot_max - lot_min) * conf_curve * vol_adj
    return round(max(lot_min, min(lot_max, scaled)), 3)
