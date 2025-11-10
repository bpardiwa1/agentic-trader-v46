from __future__ import annotations
from fx_v46.trust.trust_engine_v46 import adjusted_confidence
from math import fabs

def _sigmoid(x: float) -> float:
    # smooth 0..1 score
    import math
    return 1 / (1 + math.exp(-x))

def _conf_from_indicators(rsi: float, ema_gap: float, atr_pct: float) -> tuple[float, list[str]]:
    why = []
    # 1) RSI confidence (distance from 50 scaled)
    rsi_dist = (rsi - 50.0) / 25.0  # ~[-1,1]
    rsi_conf = _sigmoid(2.5 * rsi_dist)  # sharper edges

    if rsi >= 55:   why.append("rsi_confirms_bull")
    elif rsi <= 45: why.append("rsi_confirms_bear")
    else:           why.append("rsi_neutral")

    # 2) EMA alignment
    ema_sign = 1.0 if ema_gap > 0 else (-1.0 if ema_gap < 0 else 0.0)
    ema_mag  = min(1.0, fabs(ema_gap) /  (atr_pct * 2.0 + 1e-9))  # normalize by ATR%
    ema_conf = _sigmoid(2.0 * ema_sign * ema_mag)

    # 3) Low volatility penalty (prevents micro signals)
    vol_pen = max(0.6, min(1.0, (atr_pct / 0.0010)))  # ~1bps ATR% baseline

    raw = max(0.0, min(1.0, 0.5*rsi_conf + 0.5*ema_conf)) * vol_pen
    return raw, why

def decide_signal(feats: dict, env) -> dict:
    symbol = feats.get("symbol", "")
    base = symbol.replace("-ECNc", "").replace(".", "_").upper()

    rsi = float(feats["rsi"])
    gap = float(feats["ema_gap"])
    atrp = float(feats["atr_pct"])

    raw_conf, why = _conf_from_indicators(rsi, gap, atrp)

    # --- Parameter lookups from .env ---
    rsi_long_th = float(env.get(f"RSI_LONG_TH_{base}", env.get("RSI_LONG_TH", 60)))
    rsi_short_th = float(env.get(f"RSI_SHORT_TH_{base}", env.get("RSI_SHORT_TH", 40)))
    sl_pips = float(env.get(f"SL_{base}", env.get("FX_SL_DEFAULT", 40.0)))
    tp_pips = float(env.get(f"TP_{base}", env.get("FX_TP_DEFAULT", 90.0)))
    min_conf = float(env.get("AGENT_MIN_CONFIDENCE", 0.55))

    # --- Side resolution ---
    side = ""
    if rsi >= rsi_long_th and gap > 0:
        side = "LONG"
        why.append("ema_rsi_bull")
    elif rsi <= rsi_short_th and gap < 0:
        side = "SHORT"
        why.append("ema_rsi_bear")
    else:
        why.append("mixed_or_neutral")

    # --- Blend with trust ---
    adj_conf = adjusted_confidence(raw_conf, symbol, trust_weight=0.4)

    preview = {
        "side": side,
        "note": "momentum/trust v4.6" if side else "no_trade",
        "sl_pips": sl_pips,
        "tp_pips": tp_pips,
        "confidence": round(adj_conf, 2),
        "raw_conf": round(raw_conf, 2),
        "why": why,
    }

    accepted = bool(side and adj_conf >= min_conf)
    return {"accepted": accepted, "preview": preview}

    accepted = bool(side and adj_conf >= env.agent_min_confidence)
    return {"accepted": accepted, "preview": preview}
