# ============================================================
# Agentic Trader idx_v46 — Decider (v4.6 Final with Safe Soft EMA)
# ============================================================

from __future__ import annotations
from idx_v46.app.idx_env_v46 import ENV
from idx_v46.util.idx_logger_v46 import setup_logger

log = setup_logger("idx_decider_v46", level=str(ENV.get("LOG_LEVEL", "INFO")))


def decide_signal(features: dict) -> dict:
    sym = features.get("symbol", "")
    ema_fast = float(features.get("ema_fast", 0.0))
    ema_slow = float(features.get("ema_slow", 0.0))
    rsi = float(features.get("rsi", 50.0))
    atr_pct = float(features.get("atr_pct", 0.0))
    conf = float(features.get("adj_conf", features.get("raw_conf", 0.0)))

    # thresholds
    atr_min = float(ENV.get("IDX_ATR_TARGET_MIN", 0.0008))
    atr_max = float(ENV.get("IDX_ATR_TARGET_MAX", 0.0060))
    rsi_long_th = float(ENV.get("IDX_RSI_LONG_TH", 60))
    rsi_short_th = float(ENV.get("IDX_RSI_SHORT_TH", 40))
    allow_soft = str(ENV.get("IDX_ALLOW_SOFT_SIGNALS", "true")).lower() in ("1", "true", "yes", "on")
    soft_weight = float(ENV.get("IDX_SOFT_SIGNAL_WEIGHT", 0.8))

    why: list[str] = []

    # --- ATR quiet/hot gating ---------------------------------------------
    quiet_mult = float(ENV.get("IDX_ATR_QUIET_CONF_MULT", 0.7))
    hot_mult = float(ENV.get("IDX_ATR_HOT_CONF_MULT", 0.6))
    if atr_pct < atr_min:
        conf *= quiet_mult
        why.append("atr_quiet")
    elif atr_pct > atr_max:
        conf *= hot_mult
        why.append("atr_hot")

    # --- Directional regime detection (safe, fully logged) ----------------
    side = ""
    if ema_fast > ema_slow:
        if rsi >= rsi_long_th:
            side = "LONG"; why.append("ema_rsi_bull")
        elif allow_soft and rsi > 50:
            side = "LONG"; conf *= soft_weight; why.append("ema_bull_soft")
        else:
            why.append("bull_neutral")
    elif ema_fast < ema_slow:
        if rsi <= rsi_short_th:
            side = "SHORT"; why.append("ema_rsi_bear")
        elif allow_soft and rsi < 50:
            side = "SHORT"; conf *= soft_weight; why.append("ema_bear_soft")
        else:
            why.append("bear_neutral")
    else:
        why.append("emas_flat")

    # --- ATR-based dynamic SL/TP with confidence adjustment ---------------
    atr_pts_mult = float(ENV.get("IDX_ATR_POINTS_MULT", 10000.0))
    atr_pts = max(1.0, atr_pct * atr_pts_mult)
    sl_mult = float(ENV.get("IDX_SL_ATR_MULT", 1.5))
    tp_mult = float(ENV.get("IDX_TP_ATR_MULT", 3.0))
    conf_weight = float(ENV.get("IDX_CONF_SLTP_WEIGHT", 0.5))
    sl_min = float(ENV.get("IDX_SL_MIN_POINTS", 20.0))
    tp_min = float(ENV.get("IDX_TP_MIN_POINTS", 40.0))

    conf_scale = max(0.0, min(1.0, conf))
    spread_adj = 1.0 + (conf_scale - 0.5) * conf_weight * 2

    sl_points = max(sl_min, atr_pts * sl_mult * (2 - spread_adj))  # low conf → tighter
    tp_points = max(tp_min, atr_pts * tp_mult * spread_adj)        # high conf → wider

    preview = {
        "side": side,
        "confidence": round(conf, 2),
        "sl_points": round(sl_points, 1),
        "tp_points": round(tp_points, 1),
        "why": why or ["no_condition_matched"],
    }

    log.info(
        "[DECIDE] %s side=%s conf=%.2f ATR%%=%.4f SL=%.1f TP=%.1f why=%s",
        sym, side or "–", conf, atr_pct, sl_points, tp_points, why or ["none"],
    )
    return {"preview": preview}
