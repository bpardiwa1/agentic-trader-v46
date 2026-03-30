# ============================================================
# NAS100 Scalper v1 — Lot Scaler (fork of IDX)
# ============================================================

from __future__ import annotations
from nas100_scalp_v1.app.nas100_env_v1 import ENV
from nas100_scalp_v1.util.nas100_logger_v1 import setup_logger
from nas100_scalp_v1.trust.nas100_trust_engine_v1 import adjusted_confidence

log = setup_logger("nas100_lot_scaler_v1", level=str(ENV.get("IDX_LOG_LEVEL", "INFO")))


def compute_lot(
    symbol: str,
    confidence: float,
    atr_pct: float,
    *,
    align: str | None = None,
    override_tag: bool = False,
    bars_since_swing: int | None = None,
    trend_h1: str | None = None,
    spx_bias: str | None = None,
) -> float:
    base = symbol.upper().split(".")[0]

    min_lot = float(ENV.get(f"{base}_MIN_LOTS", ENV.get("IDX_MIN_LOTS", 0.05)))
    max_lot = float(ENV.get(f"{base}_MAX_LOTS", ENV.get("IDX_MAX_LOTS", 0.30)))
    trust_weight = float(ENV.get("IDX_TRUST_WEIGHT", 0.40))

    eff_conf = adjusted_confidence(confidence, symbol, trust_weight)

    lot_range = max_lot - min_lot
    raw_lot = min_lot + (lot_range * eff_conf)
    final_lot = max(min_lot, min(max_lot, raw_lot))

    log.info("[LOT] %s conf=%.2f eff=%.2f atr%%=%.4f lot=%.2f", symbol, confidence, eff_conf, atr_pct, final_lot)
    return round(final_lot, 2)