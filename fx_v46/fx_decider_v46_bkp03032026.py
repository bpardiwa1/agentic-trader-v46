from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict

from fx_v46.trust.trust_engine_v46 import adjusted_confidence
from fx_v46.app.fx_env_v46 import ENV
from fx_v46.util.logger import setup_logger
from math import fabs
from fx_v46.util.fx_event_sink import emit_event

# Unified FX logging (same daily file under logs/fx_v4.6)
_FX_LOG_DIR = "logs/fx_v4.6"
_FX_LOG_LEVEL = str(ENV.get("FX_LOG_LEVEL", "INFO")).upper()
_FX_LOG_NAME = f"fx_v46_{datetime.now():%Y-%m-%d}"
log = setup_logger(_FX_LOG_NAME, log_dir=_FX_LOG_DIR, level=_FX_LOG_LEVEL)


# ------------------------------------------------------------
# EVENT JSONL helper (watcher looks for token 'EVENT' + JSON)
# ------------------------------------------------------------
def _emit_event(event: str, **fields):
    emit_event(event, fields, log=log, asset="FX")


def _sigmoid(x: float) -> float:
    # smooth 0..1 score
    import math
    return 1 / (1 + math.exp(-x))

def _conf_from_indicators(rsi: float, ema_gap: float, atr_pct: float) -> tuple[float, list[str]]:
    why = []
    # 1) RSI confidence (distance from 50 scaled)
    rsi_dist = (rsi - 50.0) / 25.0  # ~[-1,1]
    rsi_conf = _sigmoid(2.5 * rsi_dist)  # sharper edges

    if rsi >= 55:
        why.append("rsi_confirms_bull")
    elif rsi <= 45:
        why.append("rsi_confirms_bear")
    else:
        why.append("rsi_neutral")

    # 2) EMA alignment
    ema_sign = 1.0 if ema_gap > 0 else (-1.0 if ema_gap < 0 else 0.0)
    ema_mag = min(1.0, fabs(ema_gap) / (atr_pct * 2.0 + 1e-9))  # normalize by ATR%
    ema_conf = _sigmoid(2.0 * ema_sign * ema_mag)

    # 3) Low volatility penalty (prevents micro signals)
    vol_pen = max(0.6, min(1.0, (atr_pct / 0.0010)))  # ~1bps ATR% baseline

    raw = max(0.0, min(1.0, 0.5 * rsi_conf + 0.5 * ema_conf)) * vol_pen
    return raw, why

def decide_signal(feats: dict, env) -> dict:
    """Decide FX trade direction using EMA/RSI/ATR with policy gating.

    FX_TRADE_POLICY in env controls strictness:
      - "strict": require strong RSI + trend + volatility + higher confidence
      - "flexible": require trend + volatility + medium confidence
      - "aggressive": allow any aligned signal, lower confidence floor
    """
    symbol = feats.get("symbol", "")
    base = symbol.replace("-ECNc", "").replace(".", "_").upper()

    # Core indicators from features
    rsi = float(feats.get("rsi", 50.0))
    gap = float(feats.get("ema_gap", 0.0))
    atrp = float(feats.get("atr_pct", 0.0))

    trend_h1 = str(feats.get("trend_h1", "UNKNOWN") or "UNKNOWN")

    raw_conf, why = _conf_from_indicators(rsi, gap, atrp)

    # --- Parameter lookups from .env ---
    rsi_long_th = float(env.get(f"RSI_LONG_TH_{base}", env.get("RSI_LONG_TH", 60)))
    rsi_short_th = float(env.get(f"RSI_SHORT_TH_{base}", env.get("RSI_SHORT_TH", 40)))
    sl_pips = float(env.get(f"SL_{base}", env.get("FX_SL_DEFAULT", 40.0)))
    tp_pips = float(env.get(f"TP_{base}", env.get("FX_TP_DEFAULT", 90.0)))

    # Global acceptance gate (same as before)
    min_conf_global = float(env.get("AGENT_MIN_CONFIDENCE", 0.55))

    trust_weight = float(env.get("FX_TRUST_WEIGHT", 0.4))
    atr_floor = float(env.get("FX_MIN_ATR_PCT", 0.0005))

    # ------------------------------------------------------------
    # ATR regime tagging (parity with IDX/XAU dashboards)
    # ------------------------------------------------------------
    atr_quiet = float(env.get(f"FX_ATR_QUIET_PCT_{base}", env.get("FX_ATR_QUIET_PCT", atr_floor)))
    atr_hot = float(env.get(f"FX_ATR_HOT_PCT_{base}", env.get("FX_ATR_HOT_PCT", max(atr_floor * 3.0, atr_floor + 1e-9))))
    if atrp < atr_quiet:
        atr_level = "quiet"
        why.append("atr_quiet")
    elif atrp > atr_hot:
        atr_level = "hot"
        why.append("atr_hot")
    else:
        atr_level = "normal"

    policy = (env.get("FX_TRADE_POLICY", "strict") or "strict").lower()
    if policy not in ("strict", "flexible", "aggressive"):
        policy = "strict"

    # Per-policy confidence floor
    if policy == "strict":
        min_conf_policy = float(env.get("FX_MIN_CONF_STRICT", min_conf_global))
    elif policy == "flexible":
        min_conf_policy = float(env.get("FX_MIN_CONF_FLEX", min_conf_global))
    else:
        min_conf_policy = float(env.get("FX_MIN_CONF_AGGR", min_conf_global))

    # --- Basic side from EMA + RSI thresholds ------------------------
    side = ""
    if rsi >= rsi_long_th and gap > 0:
        side = "LONG"
        why.append("ema_rsi_bull")
    elif rsi <= rsi_short_th and gap < 0:
        side = "SHORT"
        why.append("ema_rsi_bear")
    else:
        why.append("mixed_or_neutral")

    # ------------------------------------------------------------
    # Trend-only mode (optional)
    # If enabled, require a *defined* H1 trend (BULL/BEAR) and only trade in that direction.
    # ------------------------------------------------------------
    trend_only = str(
        env.get(f"FX_TREND_ONLY_{base}", env.get("FX_TREND_ONLY", "false"))
    ).lower() in ("1", "true", "yes", "on")

    if trend_only:
        if trend_h1 not in ("BULL", "BEAR"):
            why.append("trend_only_no_h1_trend")
            side = ""
        elif side == "LONG" and trend_h1 != "BULL":
            why.append("trend_only_block")
            side = ""
        elif side == "SHORT" and trend_h1 != "BEAR":
            why.append("trend_only_block")
            side = ""

    if trend_only and side:
        why.append("trend_only")

    # Flags for policy gating
    vol_ok = atrp >= atr_floor
    rsi_strong_bull = rsi >= rsi_long_th
    rsi_strong_bear = rsi <= rsi_short_th
    rsi_strong = rsi_strong_bull or rsi_strong_bear
    trend_ok = (side == "LONG" and gap > 0) or (side == "SHORT" and gap < 0)

    # --- Blend with trust memory first -------------------------------
    adj_conf = adjusted_confidence(raw_conf, symbol, trust_weight=trust_weight)

    # --- FX_TRADE_POLICY gating --------------------------------------
    confidence_gate = str(
        env.get("FX_CONFIDENCE_GATE", env.get("CONFIDENCE_GATE", "true"))
    ).lower() in ("1", "true", "yes", "on")

    # 1) Optional confidence gate: only clear side if gate is ON and conf is below floor
    if side and confidence_gate and adj_conf < min_conf_policy:
        why.append(f"conf<{min_conf_policy:.2f}")
        side = ""
    elif side and not confidence_gate:
        why.append("conf_gate_disabled")

    # 2) Structural / policy gating (applies when side still valid)
    if side:
        if policy == "strict":
            # Need clear trend, strong RSI and adequate volatility
            if not (trend_ok and rsi_strong and vol_ok):
                why.append("policy_strict_block")
                side = ""
        elif policy == "flexible":
            # Require trend + some volatility
            if not (trend_ok and vol_ok):
                why.append("policy_flexible_block")
                side = ""
        else:  # aggressive
            # Allow aligned side even in low vol, but block if trend clearly wrong
            if not trend_ok:
                why.append("policy_aggressive_trend_miss")
                side = ""

    # --- Dynamic SL/TP scaling (optional, env-driven) ---------------
    # Only active when FX_ATR_ENABLED=true and we actually have a side.
    atr_dyn_enabled = str(env.get("FX_ATR_ENABLED", "false")).lower() in (
        "1",
        "true",
        "yes",
        "on",
    )

    if atr_dyn_enabled and side:
        # Baseline from per-symbol env (current behaviour)
        sl_base = sl_pips
        tp_base = tp_pips

        # Per-symbol min/max with global fallbacks (all optional)
        sl_min = float(
            env.get(
                f"SL_MIN_{base}",
                env.get("FX_SL_MIN", sl_base * 0.5),
            )
        )
        tp_min = float(
            env.get(
                f"TP_MIN_{base}",
                env.get("FX_TP_MIN", tp_base * 0.5),
            )
        )
        sl_max = float(
            env.get(
                f"SL_MAX_{base}",
                env.get("FX_SL_MAX", sl_base * 1.5),
            )
        )
        tp_max = float(
            env.get(
                f"TP_MAX_{base}",
                env.get("FX_TP_MAX", tp_base * 1.5),
            )
        )

        # Confidence factor: 0.5x..1.5x around an anchor (e.g. 0.60)
        conf_anchor = float(env.get("FX_SLTP_CONF_ANCHOR", 0.60))
        conf_span = float(env.get("FX_SLTP_CONF_SPAN", 0.40))  # +/- range around anchor
        if conf_span > 0:
            conf_norm = (adj_conf - conf_anchor) / conf_span
        else:
            conf_norm = 0.0
        conf_factor = max(0.5, min(1.5, 1.0 + conf_norm))

        # Volatility factor: ATR% relative to a baseline
        atr_base = float(env.get("FX_ATR_BASE_PCT", atr_floor))
        if atr_base > 0:
            vol_factor = max(0.5, min(1.5, atrp / atr_base))
        else:
            vol_factor = 1.0

        eff_scale = conf_factor * vol_factor
        sl_dyn = sl_base * eff_scale
        tp_dyn = tp_base * eff_scale

        sl_pips = max(sl_min, min(sl_max, sl_dyn))
        tp_pips = max(tp_min, min(tp_max, tp_dyn))

        # Tag for logs / analytics
        why.append(f"sltp_dyn(scale={eff_scale:.2f})")

    preview = {
        "side": side,
        "note": "momentum/trust v4.6" if side else "no_trade",
        "sl_pips": sl_pips,
        "tp_pips": tp_pips,
        "confidence": round(adj_conf, 2),
        "raw_conf": round(raw_conf, 2),
        # 👇 extra debug context for logs
        "policy": policy,
        "min_conf_gate": min_conf_policy,      # current global confidence gate
        "atr_floor": atr_floor,         # FX_MIN_ATR_PCT from env
        "atr_pct": atrp,                # actual ATR% used in decision
        "atr_level": atr_level,         # quiet/normal/hot (Step-4 parity)
        "trend_h1": trend_h1,
        "why": why,
    }

    # Final acceptance: side must be non-empty AND meet global confidence floor
    accepted = bool(side and adj_conf >= min_conf_policy)

    # Emit DECISION event from decider itself (no behavior change)
    _emit_event(
        "DECISION",
        module="decider",
        symbol=symbol,
        base=base,
        accepted=bool(accepted),
        side=str(side or ""),
        confidence=float(round(adj_conf, 2)),
        raw_conf=float(round(raw_conf, 2)),
        policy=str(policy),
        min_conf_gate=float(min_conf_policy),
        atr_pct=float(atrp),
        atr_level=str(atr_level),
        atr_floor=float(atr_floor),
        trend_h1=str(trend_h1),
        rsi=float(rsi),
        ema_gap=float(gap),
        sl_pips=float(sl_pips),
        tp_pips=float(tp_pips),
        why=why,
        trend_only=bool(trend_only),
        confidence_gate=bool(confidence_gate),
    )

    # Optional: if side cleared -> SKIP event (useful for watcher filtering)
    if not side:
        _emit_event(
            "SKIP",
            module="decider",
            symbol=symbol,
            base=base,
            reason="no_side",
            accepted=bool(accepted),
            confidence=float(round(adj_conf, 2)),
            policy=str(policy),
            atr_pct=float(atrp),
            atr_level=str(atr_level),
            why=why,
        )

    return {"accepted": accepted, "preview": preview}
