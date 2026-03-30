# xau_decider_v46.py
# ============================================================
# Agentic Trader XAU v4.6 — Decision Engine
# ATR Regimes + Trust Scaling + H1 Context + Session Gating
# + RSI-dominant fallback in NORMAL ATR band (FLEX/AGGR)
# ============================================================

from __future__ import annotations
# import json

from datetime import datetime

from xau_v46.trust.xau_trust_engine_v46 import get_trust_score as get_trust
from xau_v46.util.logger import setup_logger
from xau_v46.app.xau_env_v46 import ENV
from xau_v46.util.xau_event_sink import emit_event as _emit_event

# Unified XAU logging
_XAU_LOG_DIR = "logs/xau_v4.6"
_XAU_LOG_LEVEL = str(ENV.get("XAU_LOG_LEVEL", ENV.get("LOG_LEVEL", "INFO"))).upper()
_XAU_LOG_NAME = f"xau_v46_{datetime.now():%Y-%m-%d}"
log = setup_logger(_XAU_LOG_NAME, log_dir=_XAU_LOG_DIR, level=_XAU_LOG_LEVEL)

def decide_signal(features: dict, env) -> dict:
    """
    Decide trading signal (LONG/SHORT/NO TRADE) for XAUUSD.

    v4.6 SCCR extensions:
      - Uses ATR regimes from features: QUIET / NORMAL / HOT
      - Enforces trading session window via features['in_session']
      - Incorporates H1 context regime for stricter policy gating
      - Keeps ATR band filtering + trust scaling + SL/TP scaling
      - PLUS: RSI-dominant fallback in NORMAL ATR band for FLEX/AGGR

    This version adds:
      - Richer logging of raw_conf / base_adj_conf / conf_after_atr / trust / adj_conf
      - Env-driven ATR regime confidence multipliers:
            XAU_ATR_CONF_MULT_QUIET
            XAU_ATR_CONF_MULT_NORMAL
            XAU_ATR_CONF_MULT_HOT
      - Env-driven policy min_conf (already present) kept as primary gate.
    """

    symbol = features.get("symbol", "XAUUSD-ECNc")

    # Core indicators from features
    ema_fast = float(features.get("ema_fast") or 0.0)
    ema_slow = float(features.get("ema_slow") or 0.0)
    rsi = float(features.get("rsi") or 50.0)
    atr_pct = float(features.get("atr_pct", 0.0) or 0.0)

    # New SCCR inputs from features_v46
    atr_regime = str(features.get("atr_regime", "UNKNOWN") or "UNKNOWN").upper()
    in_session = bool(features.get("in_session", True))
    swing_lock_allowed = bool(features.get("swing_lock_allowed", False))

    context = features.get("context") or {}
    h1_ctx = context.get("h1") or {}
    h1_regime = h1_ctx.get("regime")

    # v4.6 uses raw_conf / adj_conf from features.
    # Fallback to legacy 'confidence' key if present.
    raw_conf = float(features.get("raw_conf", features.get("confidence", 0.0)) or 0.0)
    base_adj_conf = float(features.get("adj_conf", raw_conf) or raw_conf)

    why = list(features.get("why", []))

    # --------------------------------------------------------
    # Session gating (IDX-style)
    # --------------------------------------------------------
    policy_default = (env.get("XAU_TRADE_POLICY", "strict") or "strict").lower()
    if policy_default not in ("strict", "flexible", "aggressive"):
        policy_default = "strict"
    if not in_session:
        why.append("out_of_session_window")
        log.info(
            "[SESSION-GATE] %s skipped: outside trading window (atr_regime=%s, h1=%s)",
            symbol,
            atr_regime,
            h1_regime,
        )
        return {
            "preview": {
                "side": "",
                "confidence": 0.0,
                "sl_points": 0.0,
                "tp_points": 0.0,
                "why": why,
                "atr_regime": atr_regime,
                "in_session": in_session,
                "session": "OUT",
                "policy": policy_default,
                "swing_lock_allowed": swing_lock_allowed,
            }
        }

    # --------------------------------------------------------
    # ATR Volatility band from ENV
    # --------------------------------------------------------
    atr_min = float(env.get("XAU_ATR_TARGET_MIN", 0.0010))
    atr_max = float(env.get("XAU_ATR_TARGET_MAX", 0.0040))

    # Env-driven ATR regime confidence multipliers
    quiet_mult = float(env.get("XAU_ATR_CONF_MULT_QUIET", 0.7))
    normal_mult = float(env.get("XAU_ATR_CONF_MULT_NORMAL", 1.0))
    hot_mult = float(env.get("XAU_ATR_CONF_MULT_HOT", 0.6))

    # ATR regime-aware confidence adjustment
    if atr_regime == "QUIET":
        adj_factor = quiet_mult
        why.append("atr_regime_quiet_conf_damped")
    elif atr_regime == "NORMAL":
        adj_factor = normal_mult
        why.append("atr_regime_normal_ok")
    elif atr_regime == "HOT":
        adj_factor = hot_mult
        why.append("atr_regime_hot_conf_damped")
    else:
        # Fallback to original ATR band behaviour if regime unknown
        if atr_pct < atr_min:
            adj_factor = quiet_mult  # too quiet → reduce confidence
            why.append("atr_low_conf_reduced")
        elif atr_pct > atr_max:
            adj_factor = hot_mult  # too volatile → reduce confidence
            why.append("atr_high_conf_reduced")
        else:
            adj_factor = normal_mult
            why.append("atr_band_ok")

    # Start from feature-level adjusted confidence then apply ATR modulation
    conf_after_atr = base_adj_conf * adj_factor

    # If volatility extreme (very high), still block completely
    if atr_pct > atr_max * 2.0:
        why.append("extreme_volatility_block")
        log.info(
            "[VOL-FILTER] %s skipped (ATR%%=%.4f extreme > %.4f) regime=%s "
            "raw_conf=%.3f base_adj_conf=%.3f conf_after_atr=%.3f",
            symbol,
            atr_pct,
            atr_max * 2.0,
            atr_regime,
            raw_conf,
            base_adj_conf,
            conf_after_atr,
        )
        return {
            "preview": {
                "side": "",
                "confidence": 0.0,
                "sl_points": 0.0,
                "tp_points": 0.0,
                "why": why,
                "atr_regime": atr_regime,
                "in_session": in_session,
                "session": "IN",
                "policy": policy_default,
                "swing_lock_allowed": swing_lock_allowed,
            }
        }

    # --------------------------------------------------------
    # Determine side from EMA and RSI
    # --------------------------------------------------------
    side = ""
    if ema_fast > ema_slow and rsi >= 55:
        side = "LONG"
        why.append("ema_rsi_bull")
    elif ema_fast < ema_slow and rsi <= 45:
        side = "SHORT"
        why.append("ema_rsi_bear")
    else:
        side = ""
        why.append("mixed_or_neutral")

    # --------------------------------------------------------
    # Blend with trust engine (for SL/TP scaling & gating)
    # --------------------------------------------------------
    trust_weight = float(env.get("XAU_TRUST_WEIGHT", 0.4))

    # Trust memory: may fail early during cold start, so keep it safe
    try:
        trust = float(get_trust(symbol))
    except Exception:
        trust = 0.5  # fallback if not initialized

    # Blend ATR-modulated confidence with trust
    blend = (conf_after_atr * (1.0 - trust_weight)) + (trust * trust_weight)
    # Clamp into [0.0, 1.0]; conf_scale has a soft floor for SL/TP sizing
    adj_conf = max(0.0, min(1.0, blend))
    conf_scale = max(0.2, adj_conf)

    # --------------------------------------------------------
    # Define SL/TP dynamically based on Confidence × Trust × ATR regime
    # --------------------------------------------------------
    # Base SL/TP from env (fallbacks unchanged)
    sl_base = float(env.get("XAU_SL_DEFAULT", 150))
    tp_base = float(env.get("XAU_TP_DEFAULT", 300))
    sl_min = float(env.get("XAU_SL_MIN", 60))
    tp_min = float(env.get("XAU_TP_MIN", 120))
    atr_mult = float(env.get("XAU_SLTP_ATR_MULT", 1.0))

    # Volatility-based factor around the ATR band midpoint
    atr_mid = 0.5 * (atr_min + atr_max)
    if atr_mid > 0:
        # Clamp between 0.5x and 1.5x so extremes don’t explode risk
        vol_factor = max(0.5, min(1.5, atr_pct / atr_mid))
    else:
        vol_factor = 1.0

    # Optional regime-specific SL/TP tuning
    if atr_regime == "QUIET":
        regime_mult = float(env.get("XAU_ATR_SLTP_MULT_QUIET", 1.0))
    elif atr_regime == "NORMAL":
        regime_mult = float(env.get("XAU_ATR_SLTP_MULT_NORMAL", 1.0))
    elif atr_regime == "HOT":
        regime_mult = float(env.get("XAU_ATR_SLTP_MULT_HOT", 1.0))
    else:
        regime_mult = 1.0

    # Apply scaling: confidence × ATR_MULT × volatility factor × regime
    eff_scale = conf_scale * atr_mult * vol_factor * regime_mult
    sl_points = max(sl_min, sl_base * eff_scale)
    tp_points = max(tp_min, tp_base * eff_scale)

    # Add debug visibility for SL/TP scaling (kept as-is but now complemented by richer DECISION log)
    log.debug(
        "[SLTP] %s raw_conf=%.3f base_adj_conf=%.3f conf_after_atr=%.3f trust=%.3f "
        "adj_conf=%.3f conf_scale=%.3f vol_factor=%.3f regime_mult=%.3f eff_scale=%.3f "
        "SL=%.1f TP=%.1f atr_regime=%s",
        symbol,
        raw_conf,
        base_adj_conf,
        conf_after_atr,
        trust,
        adj_conf,
        conf_scale,
        vol_factor,
        regime_mult,
        eff_scale,
        sl_points,
        tp_points,
        atr_regime,
    )

    # --------------------------------------------------------
    # Final trade decision package
    # --------------------------------------------------------
    # --- XAU_TRADE_POLICY gating -------------------------------------
    # Env-driven behaviour: strict | flexible | aggressive
    policy = (env.get("XAU_TRADE_POLICY", "strict") or "strict").lower()
    if policy not in ("strict", "flexible", "aggressive"):
        policy = "strict"

    # Indicators for policy checks
    gap = ema_fast - ema_slow

    # --------------------------------------------------------
    # CHANGE 4 — EMA GAP CLAMP (loss reduction in chop)
    # --------------------------------------------------------
    # --------------------------------------------------------
    # CHANGE 4 — EMA GAP CLAMP (loss reduction in chop)
    # --------------------------------------------------------
    def _truthy(v) -> bool:
        return str(v or "").strip().lower() in ("1", "true", "yes", "on")

    change4_gap_clamp = _truthy(env.get("XAU_CHANGE4_GAP_CLAMP", "1"))

    gap_abs = abs(gap)
    gap_min_normal = float(env.get("XAU_EMA_GAP_MIN_NORMAL", 0.90))
    gap_min_range = float(env.get("XAU_EMA_GAP_MIN_RANGE", 1.20))

    h1u = str(h1_regime or "").upper()
    h1_is_range_mixed = ("RANGE" in h1u or "MIXED" in h1u) and ("TRENDING" not in h1u)

    # Base requirement: RANGE/MIXED requires larger gap
    gap_min_required = gap_min_range if h1_is_range_mixed else gap_min_normal

    # --------------------------------------------------------
    # NEW (minimal) — Dynamic gap by ATR + trust-adaptive widening
    # --------------------------------------------------------
    dyn_gap_on = _truthy(env.get("XAU_DYNAMIC_GAP_BY_ATR", "1"))

    # Use the same atr_mid already computed earlier (vol_factor section)
    # If atr_mid is not valid, fall back safely to 1.0
    if dyn_gap_on:
        try:
            _atr_mid = float(atr_mid) if float(atr_mid) > 0 else 0.0
        except Exception:
            _atr_mid = 0.0

        if _atr_mid > 0.0:
            ratio = atr_pct / _atr_mid
            ratio = max(0.5, min(1.5, ratio))

            # In low ATR (ratio<1), increase gap requirement (stricter).
            # In high ATR (ratio>1), decrease gap requirement slightly (looser).
            gap_factor_min = float(env.get("XAU_GAP_FACTOR_MIN", 0.85))
            gap_factor_max = float(env.get("XAU_GAP_FACTOR_MAX", 1.35))

            if ratio < 1.0:
                f_atr = 1.0 + (1.0 - ratio) * (gap_factor_max - 1.0)
            else:
                f_atr = 1.0 - (ratio - 1.0) * (1.0 - gap_factor_min)

            f_atr = max(gap_factor_min, min(gap_factor_max, f_atr))
        else:
            f_atr = 1.0

        # Loss-adaptive / defensive mode via trust:
        # when trust drops below pivot, demand a bigger EMA gap
        trust_pivot = float(env.get("XAU_GAP_TRUST_PIVOT", 0.55))
        trust_penalty = float(env.get("XAU_GAP_TRUST_PENALTY", 0.80))

        f_trust = 1.0
        if trust < trust_pivot:
            f_trust = 1.0 + (trust_pivot - trust) * trust_penalty

        gap_dyn_factor = f_atr * f_trust
        gap_min_required = gap_min_required * gap_dyn_factor

        # keep diagnostics visible (shows up in logs + skip reasons)
        why.append(f"gap_dyn={gap_dyn_factor:.2f}")

    # Volatility floor for gold
    atr_floor = float(env.get("XAU_MIN_ATR_PCT", 0.0010))
    vol_ok = atr_pct >= atr_floor

    # RSI thresholds for "strong" signals (XAU-specific, env-driven)
    rsi_long_th = float(env.get("XAU_RSI_LONG_TH", 55.0))
    rsi_short_th = float(env.get("XAU_RSI_SHORT_TH", 45.0))
    rsi_strong_bull = rsi >= rsi_long_th
    rsi_strong_bear = rsi <= rsi_short_th
    rsi_strong = rsi_strong_bull or rsi_strong_bear

    # Trend aligned with side on M15?
    m15_trend_ok = (side == "LONG" and gap > 0) or (side == "SHORT" and gap < 0)

    # H1 trend alignment (optional SCCR)
    if h1_regime in ("TRENDING_UP", "TRENDING_DOWN") and side:
        h1_trend_ok = (
            (side == "LONG" and h1_regime == "TRENDING_UP")
            or (side == "SHORT" and h1_regime == "TRENDING_DOWN")
        )
    else:
        # If H1 is RANGE/MIXED or unknown, treat as neutral (not blocking)
        h1_trend_ok = True

    # --------------------------------------------------------
    # Trend-only mode (optional)
    # If enabled, ONLY trade when H1 regime is TRENDING_UP/DOWN and aligned with side.
    # This removes counter-trend mean-reversion behaviour (stabilisation before scaling).
    # --------------------------------------------------------
    trend_only = _truthy(env.get("XAU_TREND_ONLY", "0"))
    if trend_only:
        if h1_regime not in ("TRENDING_UP", "TRENDING_DOWN"):
            why.append("trend_only_no_h1_trend")
            side = ""
            h1_trend_ok = False
        elif side == "LONG" and h1_regime != "TRENDING_UP":
            why.append("trend_only_block")
            side = ""
            h1_trend_ok = False
        elif side == "SHORT" and h1_regime != "TRENDING_DOWN":
            why.append("trend_only_block")
            side = ""
            h1_trend_ok = False

    # --------------------------------------------------------
    # Policy-based minimum confidence thresholds
    # (relaxed defaults; ENV overrides still win)
    # --------------------------------------------------------
    if policy == "strict":
        min_conf = float(env.get("XAU_MIN_CONF_STRICT", 0.60))
    elif policy == "flexible":
        min_conf = float(env.get("XAU_MIN_CONF_FLEX", 0.50))
    else:  # aggressive
        min_conf = float(env.get("XAU_MIN_CONF_AGGR", 0.40))

    # Fallback floor for RSI-dominant mode (2–3 ticks below min_conf, but ≥ 0.35)
    fallback_min_conf = float(env.get("XAU_FALLBACK_MIN_CONF", max(0.35, min_conf - 0.05)))

    # Gate by confidence before allowing policy-specific indicator logic
    if adj_conf < min_conf:
        why.append(f"conf<{min_conf}")
        side = ""   # BLOCK trade early (policy confidence failure)

    # Extra: in STRICT policy, do not allow HOT regime at all
    if side and policy == "strict" and atr_regime == "HOT":
        why.append("strict_block_hot_regime")
        side = ""

    # CHANGE 4: block entries when EMA gap is too small (chop protection)
    if change4_gap_clamp and side and gap_abs < gap_min_required:
        why.append(f"ema_gap_small<{gap_min_required:.2f}")
        if h1_is_range_mixed:
            why.append("h1_range_mixed_gap_clamp")
        side = ""

    # Primary policy gating
    if side:
        if policy == "strict":
            # Need clear M15 trend, H1 aligned (or neutral), strong RSI and adequate volatility
            if not (m15_trend_ok and h1_trend_ok and rsi_strong and vol_ok):
                why.append("xau_policy_strict_block")
                side = ""
        elif policy == "flexible":
            # Require M15 trend + some volatility; log H1 mismatch but don't hard block
            if not (m15_trend_ok and vol_ok):
                why.append("xau_policy_flexible_block")
                side = ""
            elif not h1_trend_ok and h1_regime in ("TRENDING_UP", "TRENDING_DOWN"):
                why.append("h1_trend_mismatch_flex")
        else:  # aggressive
            # Allow aligned side even in low vol, but block if trend clearly wrong
            if not m15_trend_ok:
                why.append("xau_policy_aggressive_trend_miss")
                side = ""

    # --------------------------------------------------------
    # RSI-dominant fallback in NORMAL ATR band (FLEX / AGGR)
    # --------------------------------------------------------
    enable_rsi_fallback = _truthy(env.get("XAU_ENABLE_RSI_FALLBACK", "1"))

    if (
        enable_rsi_fallback
        and (not side)
        and policy in ("flexible", "aggressive")
        and atr_regime == "NORMAL"
    ):
        atr_band_ok = atr_min <= atr_pct <= atr_max  # "NORMAL" band

        # ✅ TREND_ONLY MUST ALSO APPLY TO FALLBACK
        if trend_only:
            if h1_regime not in ("TRENDING_UP", "TRENDING_DOWN"):
                why.append("fallback_block_trend_only_no_h1_trend")
            else:
                # allow fallback only if it would align with H1 trend
                pass

        if atr_band_ok and vol_ok and rsi_strong and adj_conf >= fallback_min_conf:
            # If TREND_ONLY, require RSI direction to align with H1 regime
            if trend_only and h1_regime in ("TRENDING_UP", "TRENDING_DOWN"):
                if rsi_strong_bull and h1_regime != "TRENDING_UP":
                    why.append("fallback_block_h1_mismatch")
                elif rsi_strong_bear and h1_regime != "TRENDING_DOWN":
                    why.append("fallback_block_h1_mismatch")
                else:
                    # CHANGE 4: apply the same gap clamp to fallback entries
                    if change4_gap_clamp and gap_abs < gap_min_required:
                        why.append(f"ema_gap_small<{gap_min_required:.2f}")
                        if h1_is_range_mixed:
                            why.append("h1_range_mixed_gap_clamp")
                    else:
                        if rsi_strong_bull:
                            side = "LONG"
                            why.append("fallback_rsi_dominant_long")
                        elif rsi_strong_bear:
                            side = "SHORT"
                            why.append("fallback_rsi_dominant_short")

            # Not TREND_ONLY → keep existing fallback behavior
            elif not trend_only:
                # CHANGE 4: apply the same gap clamp to fallback entries
                if change4_gap_clamp and gap_abs < gap_min_required:
                    why.append(f"ema_gap_small<{gap_min_required:.2f}")
                    if h1_is_range_mixed:
                        why.append("h1_range_mixed_gap_clamp")
                else:
                    if rsi_strong_bull:
                        side = "LONG"
                        why.append("fallback_rsi_dominant_long")
                    elif rsi_strong_bear:
                        side = "SHORT"
                        why.append("fallback_rsi_dominant_short")

    # --------------------------------------------------------
    # STEP 1B — DECISION INVARIANT ENFORCEMENT (XAU)
    # --------------------------------------------------------
    if side in ("LONG", "SHORT"):
        # Invariant 1: confidence must be positive
        if adj_conf <= 0.0:
            why.append("invalid_decision_context_conf")
            side = ""

        # Invariant 2: WHY must be non-empty
        elif not isinstance(why, list) or len(why) == 0:
            why.append("invalid_decision_context_why")
            side = ""

    preview = {
        # --- core decision ---
        "side": side,
        "confidence": round(adj_conf, 2),

        # --- risk parameters ---
        "sl_points": round(sl_points, 1),
        "tp_points": round(tp_points, 1),

        # --- decision context (STEP 2) ---
        "policy": policy,
        "atr_regime": atr_regime,
        "session": "IN" if in_session else "OUT",
        "h1_regime": h1_regime,
        "swing_lock_allowed": swing_lock_allowed,

        # --- diagnostics ---
        "why": why,
    }

    # Richer DECISION log line for SCCR analysis
    log.info(
        "[DECISION] %s side=%s raw_conf=%.3f base_adj_conf=%.3f conf_after_atr=%.3f "
        "trust=%.3f adj_conf=%.3f policy=%s min_conf=%.2f fallback_min_conf=%.2f "
        "ATR%%=%.4f regime=%s SL=%.1f TP=%.1f H1=%s session=%s swing_lock_allowed=%s "
        "why=%s",
        symbol,
        side,
        raw_conf,
        base_adj_conf,
        conf_after_atr,
        trust,
        adj_conf,
        policy,
        min_conf,
        fallback_min_conf,
        atr_pct,
        atr_regime,
        sl_points,
        tp_points,
        h1_regime,
        in_session,
        swing_lock_allowed,
        why,
    )

    # JSON event line for external Telegram watcher (decoupled alerting)
    _emit_event(
        "DECISION",
        {
            "symbol": symbol,
            "side": side,
            "raw_conf": round(raw_conf, 4),
            "base_adj_conf": round(base_adj_conf, 4),
            "conf_after_atr": round(conf_after_atr, 4),
            "trust": round(trust, 4),
            "adj_conf": round(adj_conf, 4),
            "policy": policy,
            "min_conf": round(min_conf, 4),
            "fallback_min_conf": round(fallback_min_conf, 4),
            "atr_pct": round(atr_pct, 6),
            "atr_regime": atr_regime,
            "sl_points": round(sl_points, 2),
            "tp_points": round(tp_points, 2),
            "h1_regime": h1_regime,
            "in_session": bool(in_session),
            "swing_lock_allowed": bool(swing_lock_allowed),
            "why": why,
        },
    )

    return {"preview": preview}
