# ============================================================
# Agentic Trader idx_v46 — Decider (v4.6 + SCCR Phase-2)
# ============================================================

from __future__ import annotations

from datetime import datetime
import time as pytime
from idx_v46.app.idx_env_v46 import ENV
from idx_v46.util.idx_logger_v46 import setup_logger
from idx_v46.util.idx_event_sink_v46 import emit_event  # ✅ EVENTS: added

# ------------------------------------------------------------
# Unified IDX logging (single daily file under logs/idx_v4.6)
# ------------------------------------------------------------
_IDX_LOG_DIR = "logs/idx_v4.6"
_IDX_LOG_LEVEL = str(ENV.get("IDX_LOG_LEVEL", ENV.get("LOG_LEVEL", "INFO"))).upper()
_IDX_LOG_NAME = f"idx_v46_{datetime.now():%Y-%m-%d}"
log = setup_logger(_IDX_LOG_NAME, log_dir=_IDX_LOG_DIR, level=_IDX_LOG_LEVEL)

# ------------------------------------------------------------
# EVENT JSONL helper (watcher looks for token 'EVENT' + JSON)
# ------------------------------------------------------------
def _emit_event(event: str, **fields):  # ✅ EVENTS: added (FX/XAU style)
    emit_event(event, fields, log=log, asset="IDX")


# ------------------------------------------------------------
# Trade spacing / anti-duplicate state (in-process)
# ------------------------------------------------------------
_LAST_DECISION_TS: dict[str, float] = {}
_LAST_DECISION_SIDE: dict[str, str] = {}


def _min_trade_spacing_sec(sym: str, base: str) -> int:
    """
    Minimum seconds required between consecutive *entry decisions*
    for the same symbol. This prevents duplicate/rapid re-entries.
    """
    # Global default (recommended: 60 for M15 loop=60s)
    default_sec = int(ENV.get("IDX_MIN_TRADE_SPACING_SEC", 60))

    # Per-symbol override examples:
    # IDX_MIN_TRADE_SPACING_SEC_UK100=120
    # IDX_MIN_TRADE_SPACING_SEC_NAS100=60
    # IDX_MIN_TRADE_SPACING_SEC_HK50=90
    per_key = f"IDX_MIN_TRADE_SPACING_SEC_{base}"
    try:
        return int(ENV.get(per_key, default_sec))
    except Exception:
        return default_sec


def _spacing_gate(sym: str, base: str, side: str) -> tuple[bool, str]:
    """
    Returns (allowed, why_reason).
    why_reason is a compact string we can append to `why` if blocked.
    """
    if not side:
        return True, ""

    now = pytime.time()
    min_sec = _min_trade_spacing_sec(sym, base)

    last_t = float(_LAST_DECISION_TS.get(sym, 0.0))
    last_side = str(_LAST_DECISION_SIDE.get(sym, "") or "")

    # Optional: block same-direction rapid re-entries more strictly
    block_same_dir = str(ENV.get("IDX_BLOCK_SAME_DIR_REENTRY", "true")).lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    same_dir = (last_side == side)

    if min_sec > 0 and last_t > 0 and (now - last_t) < min_sec:
        if (not block_same_dir) or same_dir:
            remaining = int(max(0, min_sec - (now - last_t)))
            return False, f"spacing_block<{min_sec}s(rem={remaining}s,last={last_side},now={side})"

    return True, ""


def _csv_set(raw: str) -> set[str]:
    """Parse comma-separated env values into a normalized set."""
    if not raw:
        return set()
    return {p.strip().upper() for p in raw.split(",") if p.strip()}



# ------------------------------------------------------------
# Decision reason normalization (dashboard attribution)
# ------------------------------------------------------------
def _primary_reason(why: list[str] | None, side: str | None) -> str:
    """
    Collapse 'why' list into a single stable reason tag for attribution.
    Priority:
      1) explicit signal tags (ema_rsi_*, ema_*_soft, *_neutral)
      2) gating tags (H1_conflict, SPX_macrobias_block, trend_only_block, etc.)
      3) fallbacks: no_side / no_condition_matched
    """
    w = [str(x) for x in (why or []) if str(x)]
    # Signal-first (what actually caused the entry)
    signal_priority = (
        "ema_rsi_bull",
        "ema_rsi_bear",
        "ema_bull_soft",
        "ema_bear_soft",
        "bull_neutral",
        "bear_neutral",
    )
    for k in signal_priority:
        if k in w:
            return k

    # Gating / context (why we blocked or degraded)
    gate_priority = (
        "H1_conflict",
        "SPX_macrobias_block",
        "trend_only_block",
        "trend_only_no_h1_trend",
        "uk100_neutral_block",
        "hk50_soft_block",
        "uk100_soft_neutral_clamp",
        "atr_quiet",
        "atr_hot",
        "emas_flat",
        "rr_floor",
    )
    for k in gate_priority:
        if k in w:
            return k

    if not (side or ""):
        return "no_side"
    return w[0] if w else "no_condition_matched"

def _reason_is_valid_for_trade(reason: str) -> bool:
    return str(reason or "") not in ("", "no_side", "no_condition_matched")


def _emit_preview_and_skip(
    sym: str,
    side: str,
    conf: float,
    policy: str,
    atr_level: str,
    why: list[str],
) -> None:
    """
    Emit structured lines that log_analyzer_v46 can parse:
      [PREVIEW] <sym> conf=0.55 policy=strict regime=QUIET side=LONG reason=[...]
      [SKIP]    <sym> conf=0.42 policy=strict regime=QUIET reason=[...]
    """
    regime = atr_level.upper()  # QUIET/NORMAL/HOT
    enriched = list(why or [])
    enriched.append(f"policy_{policy}")
    enriched.append(f"regime_{regime.lower()}")

    log.info(
        "[PREVIEW] %s conf=%.4f policy=%s regime=%s side=%s reason=%s",
        sym,
        conf,
        policy,
        regime,
        side or "-",
        enriched,
    )

    # If no trade direction, also count as SKIP (behaviour stats)
    if not side:
        log.info(
            "[SKIP] %s conf=%.4f policy=%s regime=%s reason=%s",
            sym,
            conf,
            policy,
            regime,
            enriched,
        )


def decide_signal(features: dict) -> dict:
    sym = str(features.get("symbol", "") or "")
    tf = str(features.get("tf") or features.get("timeframe") or "")

    # Base key for per-symbol overrides: NAS100.s -> NAS100, HK50.s -> HK50, UK100.s -> UK100
    base = sym.upper().split(".", 1)[0].replace("-", "_") if sym else ""

    is_nas100 = base.startswith("NAS100") or base == "NAS100"
    is_uk100 = base.startswith("UK100") or base == "UK100"
    is_hk50 = base.startswith("HK50") or base == "HK50"

    # ------------------------------------------------------------
    # Focus / allowlist (optional): restrict decisions to specific symbols
    # Example: IDX_FOCUS_SYMBOLS=NAS100
    #          IDX_FOCUS_SYMBOLS=NAS100,UK100
    # If unset/empty => all symbols allowed.
    # ------------------------------------------------------------
    focus_syms = _csv_set(str(ENV.get("IDX_FOCUS_SYMBOLS", "")))
    if focus_syms and (base not in focus_syms) and (sym.upper() not in focus_syms):
        conf_local = float(features.get("adj_conf", features.get("raw_conf", 0.0)) or 0.0)
        policy_local = str(ENV.get("IDX_TRADE_POLICY", "strict") or "strict").lower()
        atr_level_local = "normal"
        why = [f"symbol_filtered({base})"]
        _emit_preview_and_skip(sym, side="", conf=conf_local, policy=policy_local, atr_level=atr_level_local, why=why)

        # ✅ EVENTS: decider emits DECISION + SKIP for filtered symbols (no logic change)
        _emit_event(
            "DECISION",
            module="decider",
            symbol=sym,
            base=base,
            accepted=False,
            side="",
            confidence=float(round(conf_local, 2)),
            policy=str(policy_local),
            atr_pct=float(features.get("atr_pct", 0.0) or 0.0),
            atr_level=str(atr_level_local),
            timeframe=str(tf or ""),
            why=why,
        )
        _emit_event(
            "SKIP",
            module="decider",
            symbol=sym,
            base=base,
            reason="symbol_filtered",
            accepted=False,
            confidence=float(round(conf_local, 2)),
            policy=str(policy_local),
            atr_pct=float(features.get("atr_pct", 0.0) or 0.0),
            atr_level=str(atr_level_local),
            timeframe=str(tf or ""),
            why=why,
        )

        return {
            "action": "skip",
            "symbol": sym,
            "timeframe": tf,
            "side": "",
            "confidence": conf_local,
            "reason": "symbol_filtered",
            "why": why,
        }

    ema_fast = float(features.get("ema_fast", 0.0) or 0.0)
    ema_slow = float(features.get("ema_slow", 0.0) or 0.0)
    rsi = float(features.get("rsi", 50.0) or 50.0)
    atr_pct = float(features.get("atr_pct", 0.0) or 0.0)

    # Main confidence signal from features
    conf = float(features.get("adj_conf", features.get("raw_conf", 0.0)) or 0.0)

    # H1 + macro bias (from features layer)
    trend_h1 = str(features.get("trend_h1", "UNKNOWN") or "UNKNOWN")
    spx_bias = str(features.get("spx_bias", "UNKNOWN") or "UNKNOWN")

    # ATR targets
    atr_min_target = float(ENV.get("IDX_ATR_TARGET_MIN", 0.0008))
    atr_max_target = float(ENV.get("IDX_ATR_TARGET_MAX", 0.0060))

    # Prefer explicit quiet/hot bands if present (with per-symbol overrides)
    atr_quiet_pct = float(ENV.get(f"IDX_ATR_QUIET_PCT_{base}", ENV.get("IDX_ATR_QUIET_PCT", atr_min_target)))
    atr_hot_pct = float(ENV.get(f"IDX_ATR_HOT_PCT_{base}", ENV.get("IDX_ATR_HOT_PCT", atr_max_target)))

    rsi_long_th = float(ENV.get("IDX_RSI_LONG_TH", 60))
    rsi_short_th = float(ENV.get("IDX_RSI_SHORT_TH", 40))

    allow_soft = str(ENV.get(f"IDX_ALLOW_SOFT_SIGNALS_{base}", ENV.get("IDX_ALLOW_SOFT_SIGNALS", "true"))).lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    soft_weight = float(ENV.get(f"IDX_SOFT_SIGNAL_WEIGHT_{base}", ENV.get("IDX_SOFT_SIGNAL_WEIGHT", 0.8)))
    # ------------------------------------------------------------
    # Step-3: NAS100 noise reduction — prefer NO soft entries
    # ------------------------------------------------------------
    if is_nas100:
        allow_soft = str(
            ENV.get("IDX_ALLOW_SOFT_SIGNALS_NAS100", ENV.get("IDX_ALLOW_SOFT_SIGNALS", "true"))
        ).lower() in ("1", "true", "yes", "on")
        soft_weight = float(
            ENV.get("IDX_SOFT_SIGNAL_WEIGHT_NAS100", ENV.get("IDX_SOFT_SIGNAL_WEIGHT", 0.8))
        )

    # RSI exhaustion soft multiplier
    rsi_overbought = float(ENV.get("IDX_RSI_OVERBOUGHT", 70))
    rsi_oversold = float(ENV.get("IDX_RSI_OVERSOLD", 30))
    rsi_exhaust_soft_mult = float(ENV.get("IDX_RSI_EXHAUST_SOFT_MULT", 0.9))
    # Step-3: NAS100 stronger exhaustion dampener
    if is_nas100:
        rsi_exhaust_soft_mult = float(
            ENV.get("IDX_RSI_EXHAUST_SOFT_MULT_NAS100", rsi_exhaust_soft_mult)
        )

    # SL/TP floors
    sl_atr_floor = float(ENV.get("IDX_SL_ATR_FLOOR", 1.0))  # min SL = N * ATR
    tp_atr_floor = float(ENV.get("IDX_TP_ATR_FLOOR", 1.5))  # min TP = M * ATR
    min_rr = float(ENV.get("IDX_MIN_RR", 1.5))  # min RR = TP / SL

    why: list[str] = []

    # ------------------------------------------------------------
    # ATR regime tagging + confidence nudge
    # ------------------------------------------------------------
    quiet_mult = float(ENV.get("IDX_ATR_QUIET_CONF_MULT", 0.7))
    hot_mult = float(ENV.get("IDX_ATR_HOT_CONF_MULT", 0.6))

    # Prefer ATR regime computed by features (keeps [PREVIEW] aligned with [FEAT] ATR_LVL)
    atr_level_raw = str(features.get("atr_level", "") or "").lower()
    if atr_level_raw in ("quiet", "normal", "hot"):
        atr_level = atr_level_raw
    else:
        # Fallback: derive locally from ATR% thresholds
        atr_level = "normal"
        if atr_pct < atr_quiet_pct:
            atr_level = "quiet"
        elif atr_pct > atr_hot_pct:
            atr_level = "hot"

    # Apply confidence dampeners based on final ATR regime (existing behavior preserved)
    if atr_level == "quiet":
        conf *= quiet_mult
        why.append("atr_quiet")
    elif atr_level == "hot":
        conf *= hot_mult
        why.append("atr_hot")

    # ------------------------------------------------------------
    # Directional signal
    # ------------------------------------------------------------
    side = ""
    if ema_fast > ema_slow:
        if rsi >= rsi_long_th:
            side = "LONG"
            why.append("ema_rsi_bull")
        elif allow_soft and rsi > 50:
            side = "LONG"
            conf *= soft_weight
            why.append("ema_bull_soft")
        else:
            why.append("bull_neutral")
    elif ema_fast < ema_slow:
        if rsi <= rsi_short_th:
            side = "SHORT"
            why.append("ema_rsi_bear")
        elif allow_soft and rsi < 50:
            side = "SHORT"
            conf *= soft_weight
            why.append("ema_bear_soft")
        else:
            why.append("bear_neutral")
    else:
        why.append("emas_flat")

    # ------------------------------------------------------------
    # Trend-only mode (optional)
    # If enabled, require a *defined* H1 trend (BULL/BEAR) and only trade in that direction.
    # ------------------------------------------------------------
    trend_only = str(
        ENV.get(f"IDX_TREND_ONLY_{base}", ENV.get("IDX_TREND_ONLY", "false"))
    ).lower() in ("1", "true", "yes", "on")

    # ------------------------------------------------------------
    # H1 trend confluence gating
    # Only enforce hard H1 conflict blocking when trend-only is enabled
    # for the effective symbol.
    # ------------------------------------------------------------
    if trend_only and side and trend_h1 in ("BULL", "BEAR"):
        if side == "LONG" and trend_h1 == "BEAR":
            why.append("H1_conflict")
            side = ""
        elif side == "SHORT" and trend_h1 == "BULL":
            why.append("H1_conflict")
            side = ""

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

    # NAS100 SPX macro bias filter
    if side and is_nas100 and spx_bias in ("BULL", "BEAR"):
        if side == "LONG" and spx_bias == "BEAR":
            why.append("SPX_macrobias_block")
            side = ""
        elif side == "SHORT" and spx_bias == "BULL":
            why.append("SPX_macrobias_block")
            side = ""

    # RSI exhaustion soft filter
    if side == "LONG" and rsi >= rsi_overbought:
        conf *= rsi_exhaust_soft_mult
        why.append("rsi_overbought_soft")
    elif side == "SHORT" and rsi <= rsi_oversold:
        conf *= rsi_exhaust_soft_mult
        why.append("rsi_oversold_soft")

    # ------------------------------------------------------------
    # Extension filter (avoid late entries into exhaustion)
    # Blocks entries when price is too far from EMA_SLOW in ATR units.
    # ------------------------------------------------------------
    if side:
        price = float(
            features.get("price")
            or features.get("close")
            or features.get("last_close")
            or 0.0
        )
        if price > 0.0 and atr_pct > 0.0:
            atr_abs = atr_pct * price
            dist = abs(price - ema_slow)
            ext_atr = dist / atr_abs if atr_abs > 0 else 0.0

            # Per-regime thresholds (with per-symbol overrides)
            if atr_level == "quiet":
                k = float(ENV.get(f"IDX_MAX_EXT_ATR_QUIET_{base}", ENV.get("IDX_MAX_EXT_ATR_QUIET", 1.20)))
            elif atr_level == "hot":
                k = float(ENV.get(f"IDX_MAX_EXT_ATR_HOT_{base}", ENV.get("IDX_MAX_EXT_ATR_HOT", 2.20)))
            else:
                k = float(ENV.get(f"IDX_MAX_EXT_ATR_NORMAL_{base}", ENV.get("IDX_MAX_EXT_ATR_NORMAL", 1.80)))

            # UK100 tends to mean-revert intraday; make NORMAL a bit stricter by default
            if is_uk100 and atr_level == "normal":
                k = float(ENV.get("IDX_MAX_EXT_ATR_NORMAL_UK100", k))

            if ext_atr > k:
                why.append(f"ext_block>{k:.2f}atr({ext_atr:.2f})")
                side = ""
        else:
            why.append("ext_skip_no_price_or_atr")

    # ------------------------------------------------------------
    # Trade policy gating: strict / flexible / aggressive
    # ------------------------------------------------------------
    policy = str(ENV.get("IDX_TRADE_POLICY", "strict") or "strict").lower()
    if policy not in ("strict", "flexible", "aggressive"):
        policy = "strict"

    if policy == "strict":
        base_min_conf = float(ENV.get("IDX_MIN_CONF_STRICT", 0.60))
    elif policy == "flexible":
        # SCCR: lower default slightly; can be overridden in env
        base_min_conf = float(ENV.get("IDX_MIN_CONF_FLEX", 0.52))
    else:
        base_min_conf = float(ENV.get("IDX_MIN_CONF_AGGR", 0.45))

    # ------------------------------------------------------------
    # Per-symbol min confidence override (dashboard hardening)
    # Examples:
    #   IDX_MIN_CONF_FLEX_NAS100=0.60
    #   IDX_MIN_CONF_STRICT_UK100=0.62
    # ------------------------------------------------------------
    pol_key = "STRICT" if policy == "strict" else ("FLEX" if policy == "flexible" else "AGGR")
    base_min_conf = float(ENV.get(f"IDX_MIN_CONF_{pol_key}_{base}", base_min_conf))

    # Regime-specific floors (global)
    conf_floor_normal = float(ENV.get("IDX_CONF_FLOOR_NORMAL", base_min_conf))
    conf_floor_quiet = float(ENV.get("IDX_CONF_FLOOR_QUIET", base_min_conf - 0.05))
    conf_floor_hot = float(ENV.get("IDX_CONF_FLOOR_HOT", base_min_conf + 0.03))

    # Per-symbol floor override (e.g. UK100)
    if atr_level == "quiet":
        min_conf = float(ENV.get(f"IDX_CONF_FLOOR_QUIET_{base}", conf_floor_quiet))
    elif atr_level == "hot":
        min_conf = float(ENV.get(f"IDX_CONF_FLOOR_HOT_{base}", conf_floor_hot))
    else:
        min_conf = float(ENV.get(f"IDX_CONF_FLOOR_NORMAL_{base}", conf_floor_normal))
    # ------------------------------------------------------------
    # Step-3: NAS100 neutral / soft entries require higher confidence
    # ------------------------------------------------------------
    if is_nas100 and side:
        if (
            "bull_neutral" in why
            or "bear_neutral" in why
            or "ema_bull_soft" in why
            or "ema_bear_soft" in why
        ):
            boost = float(ENV.get("IDX_NEUTRAL_CONF_BOOST_NAS100", ENV.get("IDX_NEUTRAL_CONF_BOOST", 0.05)))
            min_neutral = min_conf + boost
            if conf < min_neutral:
                why.append(f"nas100_neutral_conf<{min_neutral:.2f}")
                side = ""

    # ------------------------------------------------------------
    # UK100 clamps (based on dashboard recommendations)
    # ------------------------------------------------------------
    ema_gap_pts = abs(ema_fast - ema_slow)

    if is_uk100 and side:
        # A) Reason clamp: ema_rsi_bull / ema_rsi_bear require minimum EMA gap
        gap_min_uk = float(ENV.get("IDX_EMA_GAP_MIN_POINTS_UK100", ENV.get("IDX_EMA_GAP_MIN_POINTS", 8.0)))
        if ("ema_rsi_bull" in why or "ema_rsi_bear" in why) and ema_gap_pts < gap_min_uk:
            why.append(f"ema_gap_small<{gap_min_uk:.2f}_UK100")
            side = ""

        # B) Neutral-state clamp: block SOFT signals unless RSI is convincingly away from 50
        if side and ("ema_bull_soft" in why or "ema_bear_soft" in why):
            rsi_soft_long = float(ENV.get("IDX_RSI_SOFT_LONG_MIN_UK100", 55.0))
            rsi_soft_short = float(ENV.get("IDX_RSI_SOFT_SHORT_MAX_UK100", 45.0))
            if (side == "LONG" and rsi < rsi_soft_long) or (side == "SHORT" and rsi > rsi_soft_short):
                why.append("uk100_soft_neutral_clamp")
                side = ""

        # C) Hard block neutral-derived entries on UK100 unless RSI is decisive
        if is_uk100 and side:
            if "bull_neutral" in why or "bear_neutral" in why:
                why.append("uk100_neutral_block")
                side = ""

    # ------------------------------------------------------------
    # HK50 clamps (chop control; minimal diff)
    # ------------------------------------------------------------
    if is_hk50 and side:
        # A) Prefer NO soft entries on HK50 (noise reduction)
        hk_allow_soft = str(
            ENV.get("IDX_ALLOW_SOFT_SIGNALS_HK50", ENV.get("IDX_ALLOW_SOFT_SIGNALS", "true"))
        ).lower() in ("1", "true", "yes", "on")

        if not hk_allow_soft and ("ema_bull_soft" in why or "ema_bear_soft" in why):
            why.append("hk50_soft_block")
            side = ""

        # B) Require a stronger EMA gap for any HK50 entry
        gap_min_hk = float(ENV.get("IDX_EMA_GAP_MIN_POINTS_HK50", ENV.get("IDX_EMA_GAP_MIN_POINTS", 8.0)))
        if side and ema_gap_pts < gap_min_hk:
            why.append(f"ema_gap_small<{gap_min_hk:.2f}_HK50")
            side = ""

    # ------------------------------------------------------------
    # Keep your NAS EMA gap clamp (only applies when side still present)
    # ------------------------------------------------------------
    if is_nas100 and side:
        gap_min = float(ENV.get("IDX_EMA_GAP_MIN_POINTS_NAS100", 8.0))
        if ema_gap_pts < gap_min:
            why.append(f"ema_gap_small<{gap_min:.2f}")
            side = ""

    # ------------------------------------------------------------
    # Final confidence gate
    # ------------------------------------------------------------
    if side and conf < min_conf:
        why.append(f"conf<{min_conf:.2f}")
        side = ""
    # ------------------------------------------------------------
    # Step-2: Trade spacing / duplicate-entry prevention
    # (blocks too-frequent re-entries per symbol)
    # ------------------------------------------------------------
    allowed, spacing_reason = _spacing_gate(sym, base, side)
    if side and not allowed:
        why.append(spacing_reason)
        side = ""

    # ------------------------------------------------------------
    # Swing-lock config
    # Default is NORMAL/HOT (lock ON there). QUIET is freer scalp regime.
    # ------------------------------------------------------------
    lock_regimes_raw = str(
        ENV.get(
            f"IDX_SWING_LOCK_REGIMES_{base}",
            ENV.get("IDX_SWING_LOCK_REGIMES", "normal,hot"),
        )
    ).lower()
    lock_regimes = {r.strip() for r in lock_regimes_raw.split(",") if r.strip()}

    swing_lock_bars = int(ENV.get(f"IDX_SWING_LOCK_BARS_{base}", ENV.get("IDX_SWING_LOCK_BARS", 0)))
    if side and swing_lock_bars > 0:
        swing_age = int(
            features.get("swing_bars")
            or features.get("bars_since_swing")
            or features.get("pivot_age")
            or 999999
        )

        if atr_level in lock_regimes:
            if swing_age < swing_lock_bars:
                why.append(f"swing_lock pivot_age={swing_age}<lock={swing_lock_bars} ({atr_level})")
                side = ""
        else:
            why.append(f"swing_lock_disabled_atr={atr_level}")

    # ------------------------------------------------------------
    # ATR-based SL/TP
    # ------------------------------------------------------------
    atr_pts_mult = float(ENV.get("IDX_ATR_POINTS_MULT", 10000.0))
    price = float(features.get("price", 0.0) or 0.0)

    if price > 0.0:
        atr_pts = max(1.0, atr_pct * price)
    else:
        atr_pts = max(1.0, atr_pct * atr_pts_mult)
        why.append("atr_pts_fallback_mult")

    sl_mult = float(ENV.get("IDX_SL_ATR_MULT", 1.5))
    tp_mult = float(ENV.get("IDX_TP_ATR_MULT", 3.0))
    conf_weight = float(ENV.get("IDX_CONF_SLTP_WEIGHT", 0.5))
    sl_min = float(ENV.get("IDX_SL_MIN_POINTS", 20.0))
    tp_min = float(ENV.get("IDX_TP_MIN_POINTS", 40.0))

    conf_scale = max(0.0, min(1.0, conf))

    sl_points = atr_pts * sl_mult
    tp_points = atr_pts * tp_mult * (1.0 + conf_scale * conf_weight)

    sl_points = max(sl_points, atr_pts * sl_atr_floor)
    tp_points = max(tp_points, atr_pts * tp_atr_floor)

    sl_points = max(sl_min, sl_points)
    tp_points = max(tp_min, tp_points)

    if tp_points < sl_points * min_rr:
        tp_points = sl_points * min_rr
        why.append("rr_floor")

    # ------------------------------------------------------------
    # Decision reason (single tag) + integrity kill-switch
    # ------------------------------------------------------------
    reason = _primary_reason(why, side)

    # Hard stop: never allow an executable side without a mapped reason.
    # This protects attribution + prevents "no_reason" losses.
    if side and (not _reason_is_valid_for_trade(reason)):
        why.append("missing_decision_reason")
        _emit_event(
            "BLOCKED",
            module="decider",
            symbol=sym,
            base=base,
            reason="missing_decision_reason",
            accepted=False,
            side=str(side or ""),
            confidence=float(round(conf, 2)),
            policy=str(policy),
            atr_pct=float(atr_pct),
            atr_level=str(atr_level),
            timeframe=str(tf or ""),
            why=why,
        )
        side = ""
        reason = _primary_reason(why, side)

    # If we still have a side at this point, this is an "entry-allowed" decision.
    # Record it so subsequent loops can spacing-block duplicates.
    if side:
        _LAST_DECISION_TS[sym] = pytime.time()
        _LAST_DECISION_SIDE[sym] = side

    preview = {
        "side": side,
        "reason": str(reason),
        "confidence": round(conf, 2),
        "sl_points": round(sl_points, 1),
        "tp_points": round(tp_points, 1),
        "why": why or ["no_condition_matched"],
        "trend_h1": trend_h1,
        "spx_bias": spx_bias,
        "atr_pct": atr_pct,
        "atr_level": atr_level,
        "policy": policy,
        "timeframe": tf,
    }

    log.info(
        "[DECIDE] %s tf=%s side=%s conf=%.2f ATR%%=%.4f SL=%.1f TP=%.1f why=%s H1=%s SPX=%s ATR_LVL=%s",
        sym,
        tf or "-",
        side or "-",
        conf,
        atr_pct,
        sl_points,
        tp_points,
        why or ["none"],
        trend_h1,
        spx_bias,
        atr_level,
    )

    _emit_preview_and_skip(sym, side, conf, policy, atr_level, why)

    # ✅ EVENTS: emit DECISION always, SKIP when no side (FX parity; no logic change)
    accepted = bool(side)
    _emit_event(
        "DECISION",
        module="decider",
        symbol=sym,
        base=base,
        reason=str(reason),
        accepted=bool(accepted),
        side=str(side or ""),
        confidence=float(round(conf, 2)),
        policy=str(policy),
        min_conf_gate=float(min_conf),
        atr_pct=float(atr_pct),
        atr_level=str(atr_level),
        atr_floor=float(atr_quiet_pct),
        trend_h1=str(trend_h1),
        spx_bias=str(spx_bias),
        rsi=float(rsi),
        ema_fast=float(ema_fast),
        ema_slow=float(ema_slow),
        sl_points=float(round(sl_points, 1)),
        tp_points=float(round(tp_points, 1)),
        why=why or ["no_condition_matched"],
        timeframe=str(tf or ""),
        trend_only=bool(trend_only),
        allow_soft=bool(allow_soft),
    )

    if not side:
        _emit_event(
            "SKIP",
            module="decider",
            symbol=sym,
            base=base,
            reason="no_side",
            accepted=bool(accepted),
            confidence=float(round(conf, 2)),
            policy=str(policy),
            min_conf_gate=float(min_conf),
            atr_pct=float(atr_pct),
            atr_level=str(atr_level),
            timeframe=str(tf or ""),
            why=why or ["no_condition_matched"],
        )

    return {"preview": preview}