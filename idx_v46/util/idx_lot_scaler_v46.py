# ============================================================
# Agentic Trader idx_v46 — Lot Scaler (v4.6 SCCR Phase-2 Enhanced)
# ============================================================

from __future__ import annotations
from idx_v46.app.idx_env_v46 import ENV
from idx_v46.util.idx_logger_v46 import setup_logger
from idx_v46.trust.idx_trust_engine_v46 import adjusted_confidence

log = setup_logger("idx_lot_scaler_v46", level=str(ENV.get("LOG_LEVEL", "INFO")))


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
    """
    Compute dynamic lot size (v4.6 SCCR Phase-2)
    - Trust-adjusted confidence
    - ATR volatility dampening
    - ATR quiet/hot regime multipliers
    - H1 trend reinforcement
    - SPX macro bias sizing (NAS100 only)
    - Swing-safe position sizing
    - Alignment-based adjustments (FULL / MIXED / OVERRIDE)
    - Broker-safe rounding (min/max/step)
    """

    # --- Base environment settings --------------------------------------
    # --- Base environment settings --------------------------------------
    # Per-symbol overrides:
    #   <BASE>_MIN_LOTS / <BASE>_MAX_LOTS
    #   where BASE is symbol without suffix, e.g. NAS100 from NAS100.s
    base = symbol.upper().split(".")[0]

    min_lot = float(
        ENV.get(f"{base}_MIN_LOTS", ENV.get("IDX_MIN_LOTS", 0.05))
    )
    max_lot = float(
        ENV.get(f"{base}_MAX_LOTS", ENV.get("IDX_MAX_LOTS", 0.30))
    )
    trust_weight = float(ENV.get("IDX_TRUST_WEIGHT", 0.40))

    # ATR-based volatility scaling
    quiet_mult = float(ENV.get("IDX_QUIET_LOT_MULT", 0.9))
    high_vol_mult = float(ENV.get("IDX_HIGH_VOL_LOT_MULT", 0.8))
    atr_min = float(ENV.get("IDX_ATR_TARGET_MIN", 0.0008))
    atr_max = float(ENV.get("IDX_ATR_TARGET_MAX", 0.0060))

    # SCCR: ATR regime parameters
    quiet_reg = float(ENV.get("IDX_ATR_QUIET_PCT", 0.0010))
    hot_reg = float(ENV.get("IDX_ATR_HOT_PCT", 0.0030))
    quiet_reg_mult = float(ENV.get("IDX_ATR_QUIET_LOT_MULT", 0.85))
    hot_reg_mult = float(ENV.get("IDX_ATR_HOT_LOT_MULT", 0.75))

    # Swing protection sizing
    swing_cut = int(ENV.get("IDX_SWING_SAFETY_BARS", 3))
    swing_mult = float(ENV.get("IDX_SWING_LOT_MULT", 0.70))

    # H1 / SPX enhancements
    h1_boost = float(ENV.get("IDX_H1_TREND_BOOST", 1.10))
    spx_boost = float(ENV.get("IDX_SPX_MACRO_BOOST", 1.12))
    spx_penalty = float(ENV.get("IDX_SPX_MACRO_PENALTY", 0.85))

    # Alignment factors
    fact_full = float(ENV.get("IDX_LOT_FACT_FULL_ALIGN", 1.0))
    fact_mixed = float(ENV.get("IDX_LOT_FACT_MIXED", 0.60))
    fact_override = float(ENV.get("IDX_LOT_FACT_OVERRIDE", 0.50))
    fact_edge = float(ENV.get("IDX_LOT_FACT_EDGE", 0.40))
    edge_conf = float(ENV.get("IDX_LOT_EDGE_CONF", 0.0))

    # ---------------------------------------------------------------
    # 1. Trust-adjusted confidence
    # ---------------------------------------------------------------
    eff_conf = adjusted_confidence(confidence, symbol, trust_weight)

    # ---------------------------------------------------------------
    # 2. ATR volatility dampening
    # ---------------------------------------------------------------
    if atr_pct < atr_min:
        eff_conf *= quiet_mult
    elif atr_pct > atr_max:
        eff_conf *= high_vol_mult

    # ---------------------------------------------------------------
    # 3. ATR regime multipliers (quiet/hot ranges)
    # ---------------------------------------------------------------
    if atr_pct < quiet_reg:
        eff_conf *= quiet_reg_mult
    elif atr_pct > hot_reg:
        eff_conf *= hot_reg_mult

    # ---------------------------------------------------------------
    # 4. Core linear lot scaling
    # ---------------------------------------------------------------
    lot_range = max_lot - min_lot
    raw_lot = min_lot + (lot_range * eff_conf)
    final_lot = max(min_lot, min(max_lot, raw_lot))

    # ---------------------------------------------------------------
    # 5. Swing-safe sizing (recent swing high/low detected)
    # ---------------------------------------------------------------
    if bars_since_swing is not None and bars_since_swing < swing_cut:
        final_lot *= swing_mult
        log.info(
            "[LOT_SWING] %s swing=%d < %d → *%.2f",
            symbol,
            bars_since_swing,
            swing_cut,
            swing_mult,
        )

    # ---------------------------------------------------------------
    # 6. Symbol alignment tier
    # ---------------------------------------------------------------
    if align in ("ALIGNED_BULL", "ALIGNED_BEAR"):
        tier = "FULL"
    elif override_tag:
        tier = "OVERRIDE"
    elif align == "MIXED":
        tier = "MIXED"
    else:
        tier = "FULL"

    if edge_conf > 0.0 and confidence < edge_conf:
        tier = "EDGE"

    if tier == "FULL":
        align_factor = fact_full
    elif tier == "MIXED":
        align_factor = fact_mixed
    elif tier == "OVERRIDE":
        align_factor = fact_override
    else:
        align_factor = fact_edge

    final_lot = min_lot + align_factor * (final_lot - min_lot)

    # ---------------------------------------------------------------
    # 7. H1 trend reinforcement
    # ---------------------------------------------------------------
    if trend_h1 == "BULL" and "BULL" in (align or ""):
        final_lot *= h1_boost
    elif trend_h1 == "BEAR" and "BEAR" in (align or ""):
        final_lot *= h1_boost

    # ---------------------------------------------------------------
    # 8. SPX macro-bias effect (NAS100 only)
    # ---------------------------------------------------------------
    if symbol.upper().startswith("NAS") and spx_bias in ("BULL", "BEAR"):
        if (spx_bias == "BULL" and align == "ALIGNED_BULL"):
            final_lot *= spx_boost
        elif (spx_bias == "BEAR" and align == "ALIGNED_BEAR"):
            final_lot *= spx_boost
        else:
            final_lot *= spx_penalty

    # Clamp before broker round
    final_lot = max(min_lot, min(max_lot, final_lot))

    # ---------------------------------------------------------------
    # 9. Broker volume rounding
    # ---------------------------------------------------------------
    try:
        import MetaTrader5 as mt5
        info = mt5.symbol_info(symbol)
        if info:
            broker_min = info.volume_min or min_lot
            broker_max = info.volume_max or max_lot
            broker_step = info.volume_step or 0.10

            final_lot = max(broker_min, min(broker_max, final_lot))
            final_lot = round(final_lot / broker_step) * broker_step

            log.debug(
                "[LOT_SPEC] %s broker(min=%.2f max=%.2f step=%.2f) → %.2f",
                symbol, broker_min, broker_max, broker_step, final_lot,
            )
    except Exception as e:
        log.warning("[LOT_SPEC] %s broker volume check skipped: %s", symbol, e)

    # ---------------------------------------------------------------
    # Final log
    # ---------------------------------------------------------------
    log.info(
        "[LOT] %s conf=%.2f eff=%.2f atr%%=%.4f align=%s lot=%.2f",
        symbol,
        confidence,
        eff_conf,
        atr_pct,
        align or "FULL",
        final_lot,
    )

    return round(final_lot, 2)
