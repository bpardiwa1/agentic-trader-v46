# ============================================================
# Agentic Trader idx_v46 — Lot Scaler (v4.6 Final, Broker-Aware)
# ============================================================

from __future__ import annotations
import math
from idx_v46.app.idx_env_v46 import ENV
from idx_v46.util.idx_logger_v46 import setup_logger
from idx_v46.trust.idx_trust_engine_v46 import adjusted_confidence

log = setup_logger("idx_lot_scaler_v46", level=str(ENV.get("LOG_LEVEL", "INFO")))


def compute_lot(symbol: str, confidence: float, atr_pct: float) -> float:
    """
    Compute dynamic lot size based on confidence, trust, and ATR%,
    then validate against broker volume constraints (min/max/step).
    """

    # --- Environment parameters -----------------------------------------
    min_lot = float(ENV.get("IDX_MIN_LOTS", 0.05))
    max_lot = float(ENV.get("IDX_MAX_LOTS", 0.30))
    trust_weight = float(ENV.get("IDX_TRUST_WEIGHT", 0.40))

    quiet_mult = float(ENV.get("IDX_QUIET_LOT_MULT", 0.9))
    high_vol_mult = float(ENV.get("IDX_HIGH_VOL_LOT_MULT", 0.8))
    atr_min = float(ENV.get("IDX_ATR_TARGET_MIN", 0.0008))
    atr_max = float(ENV.get("IDX_ATR_TARGET_MAX", 0.0060))

    # --- Adjust confidence with trust engine ----------------------------
    eff_conf = adjusted_confidence(confidence, symbol, trust_weight)

    # --- ATR-based volatility scaling -----------------------------------
    if atr_pct < atr_min:
        eff_conf *= quiet_mult
    elif atr_pct > atr_max:
        eff_conf *= high_vol_mult

    # --- Core dynamic scaling -------------------------------------------
    lot_range = max_lot - min_lot
    raw_lot = min_lot + (lot_range * eff_conf)

    # Clamp to env min/max
    final_lot = max(min_lot, min(max_lot, raw_lot))

    # --- Broker volume info (auto-detect, safe) -------------------------
    try:
        import MetaTrader5 as mt5
        info = mt5.symbol_info(symbol)
        if info:
            broker_min = info.volume_min or min_lot
            broker_max = info.volume_max or max_lot
            broker_step = info.volume_step or 0.10
            # enforce broker range
            if final_lot < broker_min:
                final_lot = broker_min
            elif final_lot > broker_max:
                final_lot = broker_max
            # round to valid step
            final_lot = round(final_lot / broker_step) * broker_step
            log.debug(
                "[LOT_SPEC] %s broker(min=%.2f max=%.2f step=%.2f) → adjusted lot=%.2f",
                symbol, broker_min, broker_max, broker_step, final_lot,
            )
    except Exception as e:
        log.warning("[LOT_SPEC] %s broker volume check skipped: %s", symbol, e)

    # --- Final log ------------------------------------------------------
    log.info(
        "[LOT] %s conf=%.2f eff=%.2f atr%%=%.4f → lot=%.2f (%.2f–%.2f)",
        symbol, confidence, eff_conf, atr_pct, final_lot, min_lot, max_lot,
    )

    return round(final_lot, 2)
