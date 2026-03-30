# ============================================================
# Agentic Trader idx_v46 — Agent (Diagnostics + Guardrail Summary)
# NAS100 Phase-C Hardening (C1–C5, NAS100-only, minimal patch)
# ============================================================

from __future__ import annotations
import time
from datetime import datetime
from zoneinfo import ZoneInfo
import MetaTrader5 as mt5

from idx_v46.app.idx_env_v46 import ENV
from idx_v46.util.idx_logger_v46 import setup_logger
from idx_v46.idx_features_v46 import compute_features
from idx_v46.idx_decider_v46 import decide_signal
from idx_v46.idx_executor_v46 import execute_trade
from idx_v46.util.idx_lot_scaler_v46 import compute_lot
from idx_v46.util.idx_event_sink_v46 import emit_event

# Unified IDX logging (single daily file under logs/idx_v4.6)
_IDX_LOG_DIR = "logs/idx_v4.6"
_IDX_LOG_LEVEL = str(ENV.get("IDX_LOG_LEVEL", ENV.get("LOG_LEVEL", "INFO"))).upper()
_IDX_LOG_NAME = f"idx_v46_{datetime.now():%Y-%m-%d}"
log = setup_logger(_IDX_LOG_NAME, log_dir=_IDX_LOG_DIR, level=_IDX_LOG_LEVEL)

KL = ZoneInfo("Asia/Kuala_Lumpur")

# ------------------------------------------------------------
# EVENT JSONL helper (watcher looks for token 'EVENT' + JSON)
# ------------------------------------------------------------
def _emit_event(event: str, **fields):
    emit_event(event, fields, log=log, asset="INDEX")

# ------------------------------------------------------------
# Cycle markers (log clarity)
# ------------------------------------------------------------
_CYCLE_SEQ: int = 0

def _next_cycle_id() -> str:
    global _CYCLE_SEQ
    _CYCLE_SEQ += 1
    return f"C{_CYCLE_SEQ:06d}"

def _cycle_start(cid: str, sym: str, tf: str) -> None:
    log.info("")
    log.info("=" * 92)
    log.info("[CYCLE] %s %s tf=%s ts=%s", cid, sym, tf, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    _emit_event("CYCLE_START", module="agent", cycle=cid, symbol=sym, timeframe=tf)

def _cycle_end(cid: str, status: str, extra: str = "") -> None:
    if extra:
        log.info("[CYCLE_END] %s status=%s %s", cid, status, extra)
    else:
        log.info("[CYCLE_END] %s status=%s", cid, status)
    log.info("-" * 92)
    _emit_event("CYCLE_END", module="agent", cycle=cid, status=status, extra=extra)

# Stateful swing lock per symbol: tracks lock window + last bars_since_swing
_swing_state: dict[str, dict] = {}

def _symbols_from_env() -> list[str]:
    s = ENV.get("AGENT_SYMBOLS", "NAS100.s,UK100.s,HK50.s")
    return [x.strip() for x in str(s).split(",") if x.strip()]

def _in_session_kl(symbol: str) -> bool:
    """Return True if symbol is within its defined KL trading window."""
    now = datetime.now(KL)
    base = symbol.upper().split(".")[0]  # e.g. NAS100 from NAS100.s

    # ------------------------------------------------------------
    # C3 (NAS100-only): default hard window 09:00–11:00 KL
    # ONLY if per-symbol env is NOT set.
    # ------------------------------------------------------------
    default_start = "00:00"
    default_end = "23:59"
    default_days = "1,2,3,4,5"

    if base.startswith("NAS100"):
        # If user hasn't specified per-symbol overrides, enforce 09:00–11:00
        if ENV.get("IDX_TRADE_START_NAS100") is None and ENV.get("IDX_TRADE_END_NAS100") is None:
            default_start = "09:00"
            default_end = "11:00"

    start_s = str(ENV.get(f"IDX_TRADE_START_{base}", ENV.get("IDX_TRADE_START", default_start)))
    end_s = str(ENV.get(f"IDX_TRADE_END_{base}", ENV.get("IDX_TRADE_END", default_end)))
    days_csv = str(ENV.get(f"IDX_TRADE_DAYS_{base}", ENV.get("IDX_TRADE_DAYS", default_days)))
    days = {int(x.strip()) for x in days_csv.split(",") if x.strip().isdigit()}

    dow = ((now.isoweekday() - 1) % 7) + 1
    if dow not in days:
        log.info("[SESSION] %s skipped (KL %s, not a trading day)", symbol, now.strftime("%H:%M"))
        return False

    try:
        sh, sm = [int(x) for x in start_s.split(":", 1)]
        eh, em = [int(x) for x in end_s.split(":", 1)]
    except Exception:
        return True  # malformed → always active

    start = now.replace(hour=sh, minute=sm, second=0, microsecond=0)
    end = now.replace(hour=eh, minute=em, second=0, microsecond=0)

    in_session = (start <= now <= end) if start <= end else (now >= start or now <= end)

    if in_session:
        log.info(
            "[SESSION] %s active (KL %s within %s-%s days=%s)",
            symbol,
            now.strftime("%H:%M"),
            start_s,
            end_s,
            ",".join(map(str, sorted(days))),
        )
    else:
        log.info(
            "[SESSION] %s skipped (KL %s outside %s-%s)",
            symbol,
            now.strftime("%H:%M"),
            start_s,
            end_s,
        )

    return in_session

class IdxAgentV46:
    def __init__(self, symbols: list[str], timeframe: str | None = None):
        self.symbols = symbols
        self.timeframe = timeframe or ENV.get("IDX_TIMEFRAME", "M15")
        self.loop_delay = int(ENV.get("LOOP_INTERVAL", 60))

        log.info("=== SWING PATCH v1.1 ACTIVE ===")

        # Per-symbol last trade timestamps for cooldown
        self._last_trade_time: dict[str, datetime] = {}

        # ------------------------------------------------------------
        # C2 (NAS100-only): one-shot per direction per session/day
        # Track count per (symbol, kl_date, side)
        # ------------------------------------------------------------
        self._nas100_dir_count: dict[tuple[str, str, str], int] = {}

        # Per-symbol last executed side (per KL day) to prevent flip-chop when enabled
        # value: (YYYY-MM-DD, side)
        self._last_side_by_day: dict[str, tuple[str, str]] = {}

        if not mt5.initialize():
            raise RuntimeError("MT5 initialization failed")
        v = mt5.version()
        log.info("[MT5] Connected build=%s", v)
        log.info("[INIT] TF=%s Symbols=%s", self.timeframe, ", ".join(self.symbols))
        _emit_event("RUN_START", module="agent", symbols=list(self.symbols), timeframe=str(self.timeframe))

    def _nas100_one_shot_block(self, sym: str, side: str) -> bool:
        """Return True if NAS100 has already traded this direction today (KL)."""
        base = sym.upper().split(".", 1)[0]
        if not base.startswith("NAS100"):
            return False
        if side not in ("LONG", "SHORT"):
            return False

        d = datetime.now(KL).strftime("%Y-%m-%d")
        key = (sym, d, side)
        n = int(self._nas100_dir_count.get(key, 0))
        if n >= 1:
            return True
        return False

    def _nas100_mark_trade(self, sym: str, side: str) -> None:
        base = sym.upper().split(".", 1)[0]
        if not base.startswith("NAS100"):
            return
        if side not in ("LONG", "SHORT"):
            return
        d = datetime.now(KL).strftime("%Y-%m-%d")
        key = (sym, d, side)
        self._nas100_dir_count[key] = int(self._nas100_dir_count.get(key, 0)) + 1

    def _run_symbol(self, sym: str, summary: dict):
        cid = _next_cycle_id()
        status = "UNKNOWN"
        extra = ""
        _cycle_start(cid, sym, str(self.timeframe))

        try:
            # Session guard
            if not _in_session_kl(sym):
                _emit_event("SKIP", module="agent", cycle=cid, symbol=sym, reason="out_of_session")
                summary["skipped"] += 1
                status = "SKIP"
                extra = "out_of_session"
                return

            # Time-based cooldown (per symbol, in seconds)
            cooldown_sec = int(ENV.get("IDX_COOLDOWN_SEC", 60))
            if cooldown_sec > 0:
                now = datetime.now(KL)
                last = self._last_trade_time.get(sym)
                if last is not None:
                    elapsed = (now - last).total_seconds()
                    if elapsed < cooldown_sec:
                        remaining = int(cooldown_sec - elapsed)
                        log.info(
                            "[COOLDOWN] %s skip (%ds remaining of %ds)",
                            sym,
                            remaining,
                            cooldown_sec,
                        )
                        _emit_event(
                            "SKIP",
                            module="agent",
                            cycle=cid,
                            symbol=sym,
                            reason="cooldown",
                            remaining_sec=remaining,
                            cooldown_sec=cooldown_sec,
                        )
                        summary["skipped"] += 1
                        status = "SKIP"
                        extra = "cooldown"
                        return

            # Sessionone clear input
            from idx_v46.util.idx_session_risk_v46 import check_idx_risk

            src = check_idx_risk(sym)
            if src.get("blocked"):
                log.info(
                    "[SRC] %s trade blocked: %s",
                    sym,
                    src.get("reason", "unknown"),
                )
                _emit_event("BLOCKED", module="agent", cycle=cid, symbol=sym, reason="src", src=src)
                summary["blocked"] += 1
                status = "BLOCKED"
                extra = "src"
                return

            # Feature computation
            feats = compute_features(sym)
            if not feats:
                log.info(
                    "[SKIP] %s conf=0.00 trust=0.00 regime=MIXED atr%%=0.00 reason=%s",
                    sym,
                    ["no_features"],
                )
                summary["skipped"] += 1
                status = "SKIP"
                extra = "no_features"
                _emit_event(
                    "SKIP",
                    module="agent",
                    cycle=cid,
                    symbol=sym,
                    reason="no_features",
                    confidence=0.0,
                    trust=0.0,
                    regime="UNKNOWN",
                    atr_pct=0.0,
                    why=["no_features"],
                )
                return

            # --------------------------------------------------------
            # Swing protection
            # --------------------------------------------------------
            base = sym.upper().split(".", 1)[0]

            lock_bars = int(
                ENV.get(
                    f"IDX_SWING_LOCK_BARS_{base}",
                    ENV.get("IDX_SWING_LOCK_BARS", ENV.get("IDX_SWING_COOLDOWN_BARS", 2)),
                )
            )
            lock_bars = max(0, lock_bars)

            quiet_pct = float(
                ENV.get(
                    f"IDX_ATR_QUIET_PCT_{base}",
                    ENV.get("IDX_ATR_QUIET_PCT", 0.0020),
                )
            )

            swing_min_atr = float(
                ENV.get(
                    f"IDX_SWING_MIN_ATR_PCT_{base}",
                    ENV.get("IDX_SWING_MIN_ATR_PCT", 0.0),
                )
            )

            bs = int(feats.get("bars_since_swing", 999))
            atr_pct_for_swing = float(feats.get("atr_pct", 0.0))

            # --------------------------------------------------------
            # Swing regime classification aligned to decider:
            # quiet / normal / hot based on ATR%
            # --------------------------------------------------------
            hot_pct = float(
                ENV.get(
                    f"IDX_ATR_HOT_PCT_{base}",
                    ENV.get("IDX_ATR_HOT_PCT", 0.0060),
                )
            )

            if atr_pct_for_swing < quiet_pct:
                swing_regime = "quiet"
            elif atr_pct_for_swing > hot_pct:
                swing_regime = "hot"
            else:
                swing_regime = "normal"

            # --------------------------------------------------------
            # Trade-allowed regimes (default: normal,hot)
            # If current regime is not allowed, skip BEFORE any further gating.
            # --------------------------------------------------------
            allowed_raw = str(
                ENV.get(
                    f"IDX_TRADE_ALLOWED_REGIMES_{base}",
                    ENV.get("IDX_TRADE_ALLOWED_REGIMES", "normal,hot"),
                )
            ).lower()
            allowed_regimes = {r.strip() for r in allowed_raw.split(",") if r.strip()}
            if allowed_regimes and swing_regime not in allowed_regimes:
                log.info(
                    "[REGIME] %s skipped (atr%%=%.4f regime=%s not in allowed=%s)",
                    sym,
                    atr_pct_for_swing,
                    swing_regime,
                    ",".join(sorted(allowed_regimes)),
                )
                _emit_event(
                    "SKIP",
                    module="agent",
                    cycle=cid,
                    symbol=sym,
                    reason="regime_not_allowed",
                    atr_pct=float(atr_pct_for_swing),
                    regime=str(swing_regime),
                    allowed=",".join(sorted(allowed_regimes)),
                )
                summary["skipped"] += 1
                status = "SKIP"
                extra = "regime_not_allowed"
                return

            # --------------------------------------------------------
            # Swing-lock regimes (DEFAULT: normal,hot)
            # Lock should be ON in NORMAL/HOT; freer in QUIET.
            # --------------------------------------------------------
            lock_regimes_raw = str(
                ENV.get(
                    f"IDX_SWING_LOCK_REGIMES_{base}",
                    ENV.get("IDX_SWING_LOCK_REGIMES", "normal,hot"),
                )
            ).lower()
            lock_regimes = {r.strip() for r in lock_regimes_raw.split(",") if r.strip()}

            st = _swing_state.get(sym, {"lock": 0, "last_bs": 999})
            lock = int(st.get("lock", 0))
            last_bs = int(st.get("last_bs", 999))

            # Apply lock ONLY if current regime is in lock_regimes
            if lock_bars > 0 and swing_regime in lock_regimes and atr_pct_for_swing >= swing_min_atr:
                # Start a lock window when a fresh swing is detected (bs drops)
                if bs < lock_bars and bs < last_bs:
                    lock = lock_bars
                elif lock > 0:
                    lock = max(0, lock - 1)
            else:
                lock = 0

            st["lock"] = lock
            st["last_bs"] = bs
            _swing_state[sym] = st

            if lock > 0:
                log.info(
                    "[SWING] %s lockout (%d bars remaining, bs=%d, lock_bars=%d, atr%%=%.4f quiet<thr=%.4f hot>thr=%.4f, regime=%s, lock_regimes=%s)",
                    sym,
                    lock,
                    bs,
                    lock_bars,
                    atr_pct_for_swing,
                    quiet_pct,
                    hot_pct,
                    swing_regime,
                    ",".join(sorted(lock_regimes)) if lock_regimes else "-",
                )
                _emit_event(
                    "SKIP",
                    module="agent",
                    cycle=cid,
                    symbol=sym,
                    reason="swing_lock",
                    lock_remaining=int(lock),
                    bars_since_swing=int(bs),
                    lock_bars=int(lock_bars),
                    atr_pct=float(atr_pct_for_swing),
                    regime=str(swing_regime),
                    lock_regimes=",".join(sorted(lock_regimes)) if lock_regimes else "-",
                )
                summary["skipped"] += 1
                status = "SKIP"
                extra = "swing_lock"
                return

            # Base decision from decider
            decision = decide_signal(feats)
            p = decision.get("preview", {}) or {}
            side = (p.get("side", "") or "").upper()
            conf = float(p.get("confidence", 0.0))

            ema_fast = float(feats.get("ema_fast", 0.0))
            ema_slow = float(feats.get("ema_slow", 0.0))
            ema_gap = float(feats.get("ema_gap", ema_fast - ema_slow))
            rsi = float(feats.get("rsi", 50.0))
            atr_pct = float(feats.get("atr_pct", 0.0))
            adj_conf = float(feats.get("adj_conf", conf))

            if ema_fast > ema_slow and rsi >= 55.0:
                regime = "ALIGNED_BULL"
            elif ema_fast < ema_slow and rsi <= 45.0:
                regime = "ALIGNED_BEAR"
            else:
                regime = "MIXED"

            trust = adj_conf

            sl_points = float(p.get("sl_points", float(ENV.get("IDX_SL_POINTS_BASE", 100.0))))
            tp_points = float(p.get("tp_points", float(ENV.get("IDX_TP_POINTS_BASE", 200.0))))

            why_list = list(p.get("why", []))
            has_override = "trust_override" in (why_list or [])

            lot_preview = compute_lot(
                sym,
                confidence=adj_conf,
                atr_pct=atr_pct,
                align=regime,
                override_tag=has_override,
                bars_since_swing=bs,
                trend_h1=feats.get("trend_h1"),
                spx_bias=feats.get("spx_bias"),
            )

            # ✅ EVENTS: add RISK event (no logic change)
            _emit_event(
                "RISK",
                module="agent",
                cycle=cid,
                symbol=sym,
                side=side or "",
                confidence=float(conf),
                trust=float(trust),
                regime=str(regime),
                atr_pct=float(atr_pct),
                sl_points=float(sl_points),
                tp_points=float(tp_points),
                lots=float(lot_preview),
                why=why_list,
            )

            # ------------------------------------------------------------
            # C1 (NAS100-only): hard-disable override for NAS100
            # (even if IDX_OVERRIDE_ENABLE=true globally)
            # ------------------------------------------------------------
            override_enabled = str(ENV.get("IDX_OVERRIDE_ENABLE", "false")).lower() in ("1", "true", "yes", "on")
            if base.startswith("NAS100"):
                override_enabled = False

            if override_enabled and (not side or str(side).upper() == "NONE"):
                neutral_markers = {
                    "bear_neutral",
                    "bull_neutral",
                    "mixed_neutral",
                    "atr_neutral",
                    "atr_too_low",
                    "conf_neutral",
                }
                reasons_set = {str(w) for w in (why_list or [])}
                neutral_ok = not reasons_set or any(r in neutral_markers for r in reasons_set)

                o_min_conf = float(ENV.get("IDX_OVERRIDE_MIN_CONF", 0.60))
                o_min_trust = float(ENV.get("IDX_OVERRIDE_MIN_TRUST", 0.60))
                o_min_atr = float(ENV.get("IDX_OVERRIDE_MIN_ATR_PCT", 0.0007))
                o_max_gap = float(ENV.get("IDX_OVERRIDE_MAX_GAP_POINTS", 15.0))
                o_rsi_bull = float(ENV.get("IDX_OVERRIDE_RSI_BULL", 58.0))
                o_rsi_bear = float(ENV.get("IDX_OVERRIDE_RSI_BEAR", 42.0))

                cond_conf_trust = conf >= o_min_conf and trust >= o_min_trust
                cond_atr = atr_pct >= o_min_atr
                cond_gap = abs(ema_gap) <= o_max_gap

                overridden_side = ""
                if neutral_ok and cond_conf_trust and cond_atr and cond_gap:
                    if rsi >= o_rsi_bull and ema_fast >= ema_slow - o_max_gap:
                        overridden_side = "LONG"
                    elif rsi <= o_rsi_bear and ema_fast <= ema_slow + o_max_gap:
                        overridden_side = "SHORT"

                if overridden_side:
                    log.info(
                        "[OVERRIDE] %s from=%s to=%s reason=%s conf=%.2f trust=%.2f atr%%=%.4f gap=%.2f rsi=%.2f",
                        sym,
                        side or "NONE",
                        overridden_side,
                        why_list or ["neutral"],
                        conf,
                        trust,
                        atr_pct,
                        ema_gap,
                        rsi,
                    )
                    side = overridden_side
                    if "trust_override" not in why_list:
                        why_list.append("trust_override")
                    p["side"] = side
                    p["why"] = why_list

            _emit_event(
                "DECISION",
                module="decider",
                cycle=cid,
                symbol=sym,
                side=side or "",
                confidence=float(conf),
                trust=float(trust),
                regime=str(regime),
                atr_pct=float(atr_pct),
                sl_points=float(sl_points),
                tp_points=float(tp_points),
                lots=float(lot_preview),
                why=why_list,
            )

            log.info(
                "[DECISION] %s side=%s conf=%.2f trust=%.2f regime=%s atr%%=%.4f "
                "sl=%.1f tp=%.1f vol=%.2f why=%s",
                sym,
                side or "NONE",
                conf,
                trust,
                regime,
                atr_pct,
                sl_points,
                tp_points,
                lot_preview,
                why_list,
            )

            # ------------------------------------------------------------
            # C5 (NAS100-only): unknown / invalid decision context => BLOCK
            # ------------------------------------------------------------
            if base.startswith("NAS100"):
                pol = str(p.get("policy", "") or "").lower()
                atr_level = str(p.get("atr_level", "") or "").lower()
                if pol not in ("strict", "flexible", "aggressive") or atr_level not in ("quiet", "normal", "hot"):
                    log.info(
                        "[BLOCKED] %s invalid decision context (policy=%s atr_level=%s why=%s)",
                        sym,
                        pol or "missing",
                        atr_level or "missing",
                        why_list,
                    )
                    _emit_event(
                        "BLOCKED",
                        module="agent",
                        cycle=cid,
                        symbol=sym,
                        reason="invalid_decision_context",
                        policy=pol,
                        atr_level=atr_level,
                        why=why_list,
                    )
                    summary["blocked"] += 1
                    status = "BLOCKED"
                    extra = "invalid_decision_context"
                    return
                if not isinstance(why_list, list) or len(why_list) == 0:
                    log.info("[BLOCKED] %s invalid decision context (empty why)", sym)
                    _emit_event(
                        "BLOCKED",
                        module="agent",
                        cycle=cid,
                        symbol=sym,
                        reason="invalid_decision_context",
                        policy=pol,
                        atr_level=atr_level,
                        why=why_list,
                    )
                    summary["blocked"] += 1
                    status = "BLOCKED"
                    extra = "invalid_decision_context"
                    return

            # If no side, skip
            if not side:
                log.info(
                    "[SKIP] %s conf=%.2f trust=%.2f regime=%s atr%%=%.4f reason=%s",
                    sym,
                    conf,
                    trust,
                    regime,
                    atr_pct,
                    why_list or ["no_side"],
                )
                _emit_event(
                    "SKIP",
                    module="agent",
                    cycle=cid,
                    symbol=sym,
                    reason="no_side",
                    confidence=float(conf),
                    trust=float(trust),
                    regime=str(regime),
                    atr_pct=float(atr_pct),
                    why=why_list or ["no_side"],
                )
                summary["skipped"] += 1
                status = "SKIP"
                extra = "no_side"
                return

            # Confidence floor (existing)
            min_conf = float(ENV.get("IDX_MIN_CONFIDENCE", 0.52))
            if conf < min_conf:
                log.info(
                    "[SKIP] %s conf=%.2f trust=%.2f regime=%s atr%%=%.4f reason=%s",
                    sym,
                    conf,
                    trust,
                    regime,
                    atr_pct,
                    why_list or ["confidence_below_min"],
                )
                _emit_event(
                    "SKIP",
                    module="agent",
                    cycle=cid,
                    symbol=sym,
                    reason="conf_below_min",
                    min_conf=float(min_conf),
                    confidence=float(conf),
                    trust=float(trust),
                    regime=str(regime),
                    atr_pct=float(atr_pct),
                    why=why_list or ["confidence_below_min"],
                )
                summary["skipped"] += 1
                status = "SKIP"
                extra = "conf_below_min"
                return

            # ------------------------------------------------------------
            # No-flip guard (prevents range chop, especially UK100)
            # If enabled for a symbol, do not allow switching direction within the same KL day.
            # ------------------------------------------------------------
            no_flip_raw = str(ENV.get("IDX_NO_FLIP_SYMBOLS", "UK100")).upper()
            no_flip_syms = {x.strip() for x in no_flip_raw.split(",") if x.strip()}
            if base in no_flip_syms and side in ("LONG", "SHORT"):
                d = datetime.now(KL).strftime("%Y-%m-%d")
                last_d, last_side = self._last_side_by_day.get(sym, ("", ""))
                if last_d == d and last_side and last_side != side:
                    log.info(
                        "[BLOCKED] %s flip blocked (%s -> %s on %s)",
                        sym,
                        last_side,
                        side,
                        d,
                    )
                    _emit_event(
                        "BLOCKED",
                        module="agent",
                        cycle=cid,
                        symbol=sym,
                        reason="no_flip",
                        last_side=str(last_side),
                        new_side=str(side),
                        day=str(d),
                    )
                    summary["blocked"] += 1
                    status = "BLOCKED"
                    extra = "no_flip"
                    return

            # ------------------------------------------------------------
            # C2 (NAS100-only): one-shot per direction per session/day
            # ------------------------------------------------------------
            if base.startswith("NAS100") and self._nas100_one_shot_block(sym, side):
                log.info(
                    "[BLOCKED] %s %s one-shot rule: already traded this direction today (KL)",
                    sym,
                    side,
                )
                _emit_event(
                    "BLOCKED",
                    module="agent",
                    cycle=cid,
                    symbol=sym,
                    side=str(side),
                    reason="nas100_one_shot",
                )
                summary["blocked"] += 1
                status = "BLOCKED"
                extra = "nas100_one_shot"
                return

            # Trade lifecycle marker
            _emit_event(
                "TRADE_START",
                module="agent",
                cycle=cid,
                symbol=sym,
                side=side,
                confidence=float(conf),
                regime=str(regime),
                atr_pct=float(atr_pct),
                sl_points=float(sl_points),
                tp_points=float(tp_points),
                lots=float(lot_preview),
                why=why_list,
            )

            # Execute
            res = execute_trade(
                symbol=sym,
                side=side,
                sl_points=sl_points,
                tp_points=tp_points,
                confidence=conf,
                atr_pct=atr_pct,
                align=regime,
                bars_since_swing=bs,
                trend_h1=feats.get("trend_h1"),
                spx_bias=feats.get("spx_bias"),
                override_tag=has_override,
                reason=why_list,   # keep
            )

            if res.get("ok"):
                exec_lots = float(res.get("lots", lot_preview))
                log.info(
                    "[AGENT] %s %s ok (%.2f lots, conf=%.2f)",
                    sym,
                    side,
                    exec_lots,
                    conf,
                )
                self._last_trade_time[sym] = datetime.now(KL)

                # Track last executed side for no-flip guard
                self._last_side_by_day[sym] = (datetime.now(KL).strftime("%Y-%m-%d"), side)

                # C2: mark NAS100 direction used
                if base.startswith("NAS100"):
                    self._nas100_mark_trade(sym, side)

                _emit_event("EXECUTED", module="agent", cycle=cid, symbol=sym, side=side, result=res)
                _emit_event("TRADE_END", module="agent", cycle=cid, symbol=sym, side=side, status="EXECUTED", result=res)
                summary["executed"] += 1
                status = "EXECUTED"
                extra = f"ticket={res.get('ticket') or res.get('order') or res.get('deal') or '-'}"
                return

            if res.get("blocked"):
                _emit_event(
                    "BLOCKED",
                    module="executor",
                    cycle=cid,
                    symbol=sym,
                    side=side,
                    reason=str(res.get("reason", "guardrail")),
                    result=res,
                )
                _emit_event(
                    "TRADE_END",
                    module="agent",
                    cycle=cid,
                    symbol=sym,
                    side=side,
                    status="BLOCKED",
                    reason=str(res.get("reason", "guardrail")),
                )
                summary["guardrail"] += 1
                status = "BLOCKED"
                extra = "executor_guardrail"
                log.info("[GUARDRAIL] %s trade blocked by guardrail", sym)
                return

            _emit_event(
                "FAILED",
                module="executor",
                cycle=cid,
                symbol=sym,
                side=side,
                reason=str(res.get("error") or res.get("reason") or "execution_failed"),
                result=res,
            )
            _emit_event(
                "TRADE_END",
                module="agent",
                cycle=cid,
                symbol=sym,
                side=side,
                status="FAILED",
                reason=str(res.get("error") or res.get("reason") or "execution_failed"),
            )
            summary["errors"] += 1
            status = "FAILED"
            extra = "execution_failed"
            log.warning("[FAILED] %s %s -> %s", sym, side, res)
            return

        except Exception as e:
            log.exception("[ERROR] %s run failed: %s", sym, e)
            _emit_event("ERROR", module="agent", cycle=cid, symbol=sym, error=type(e).__name__, detail=str(e))
            summary["errors"] += 1
            status = "ERROR"
            extra = type(e).__name__
            return

        finally:
            _cycle_end(cid, status, extra)

    def run_once(self):
        summary = {
            "executed": 0,
            "skipped": 0,
            "blocked": 0,
            "guardrail": 0,
            "errors": 0,
        }

        for s in self.symbols:
            log.info("")
            log.info("========== [%s] ==========", s)

            try:
                self._run_symbol(s, summary)
            except Exception:
                summary["errors"] += 1
                log.exception("[ERROR] %s unhandled exception in _run_symbol", s)

        log.info(
            "[SUMMARY] Executed=%d Skipped=%d Blocked=%d Guardrail=%d Errors=%d",
            summary["executed"],
            summary["skipped"],
            summary["blocked"],
            summary["guardrail"],
            summary["errors"],
        )

    def run_forever(self, interval: int | None = None):
        loop = int(interval or self.loop_delay)
        log.info("[LOOP] interval=%ds", loop)
        while True:
            self.run_once()
            time.sleep(loop)