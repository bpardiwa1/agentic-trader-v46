# xau_agent_v46.py
# ============================================================
# Agentic Trader XAU v4.6 — Environment-Driven Agent Core
# ============================================================

from __future__ import annotations
import json
import time
from datetime import datetime
import MetaTrader5 as mt5

from xau_v46.app.xau_env_v46 import ENV
from xau_v46.util.logger import setup_logger
from xau_v46.util.xau_mt5_bars import get_bars
from xau_v46.xau_features_v46 import compute_features
from xau_v46.xau_decider_v46 import decide_signal
from xau_v46.xau_executor_v46 import execute_trade
from xau_v46.trust.xau_trust_engine_v46 import update_trust
from xau_v46.util.xau_session_risk_v46 import check_xau_risk  # Session Risk Controller (SRC)

# ✅ NEW (SCCR-minimal): JSONL event writer (watcher-friendly)
from xau_v46.util.xau_event_logger import emit_event_jsonl
# ------------------------------------------------------------
# JSON Event Logging (Watcher-friendly)
# ------------------------------------------------------------
from xau_v46.util.xau_event_sink import emit_event as _emit_event


# Unified XAU logging (single daily file under logs/xau_v4.6)
_XAU_LOG_DIR = "logs/xau_v4.6"
_XAU_LOG_LEVEL = str(ENV.get("XAU_LOG_LEVEL", ENV.get("LOG_LEVEL", "INFO"))).upper()
_XAU_LOG_NAME = f"xau_v46_{datetime.now():%Y-%m-%d}"
log = setup_logger(_XAU_LOG_NAME, log_dir=_XAU_LOG_DIR, level=_XAU_LOG_LEVEL)


# ------------------------------------------------------------
# Cycle markers (log clarity)
# ------------------------------------------------------------
_CYCLE_SEQ: int = 0

def _next_cycle_id() -> str:
    global _CYCLE_SEQ
    _CYCLE_SEQ += 1
    return f"C{_CYCLE_SEQ:06d}"

def _cycle_start(cid: str, sym: str, tf: str) -> None:
    # blank line + strong delimiter so cycles are visually separable
    log.info("")
    log.info("=" * 92)
    log.info("[CYCLE] %s %s tf=%s ts=%s", cid, sym, tf, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

def _cycle_end(cid: str, status: str, extra: str = "") -> None:
    if extra:
        log.info("[CYCLE_END] %s status=%s %s", cid, status, extra)
    else:
        log.info("[CYCLE_END] %s status=%s", cid, status)
    log.info("-" * 92)

def _symbols_from_env() -> list[str]:
    """Read active trading symbols from ENV."""
    s = (
        ENV.get("XAU_AGENT_SYMBOLS")
        or ENV.get("AGENT_SYMBOLS")
        or "XAUUSD-ECNc"
    )
    return [x.strip() for x in str(s).split(",") if x.strip()]





class XauAgentV46:
    """Main agent class handling bar fetch, feature calc, and trade execution."""

    def __init__(self, symbols: list[str], timeframe: str | None = None):
        self.symbols = symbols
        self.timeframe = timeframe or ENV.get("XAU_TIMEFRAME", "M15")
        self.bar_history = int(ENV.get("BAR_HISTORY_BARS", 240))
        self.min_lots = float(ENV.get("XAU_MIN_LOTS", 0.05))
        self.max_lots = float(ENV.get("XAU_MAX_LOTS", 0.10))
        self.cooldown_sec = int(ENV.get("XAU_COOLDOWN_SEC", 300))
        self.loop_delay = int(ENV.get("LOOP_INTERVAL", 60))

        # 🔹 New: same-direction stacking guard knobs
        self.max_same_dir_per_symbol = int(ENV.get("XAU_MAX_SAME_DIR_PER_SYMBOL", 1))
        self.same_dir_cooldown_sec = int(ENV.get("XAU_SAME_DIR_COOLDOWN_SEC", 0))

        if not mt5.initialize():
            raise RuntimeError("MT5 initialization failed")

        log.info("[MT5] Connected build=%s", mt5.version())
        log.info(
            "[INIT] Timeframe=%s | Bars=%d | Lots=[%.2f–%.2f] | MaxSameDir=%d | SameDirCooldown=%ds",
            self.timeframe,
            self.bar_history,
            self.min_lots,
            self.max_lots,
            self.max_same_dir_per_symbol,
            self.same_dir_cooldown_sec,
        )

    # ------------------------------------------------------------
    # Same-direction stacking guard
    # ------------------------------------------------------------
    def _check_same_direction_guard(self, symbol: str, side: str) -> dict:
        """
        Prevent over-stacking in the SAME direction:
          • Per-direction max open positions
          • Time-based cooldown between entries in that direction

        Returns:
            {"blocked": bool, "reason": str}
        """
        side = (side or "").upper()
        if side not in ("LONG", "SHORT"):
            return {"blocked": False, "reason": ""}

        max_same = self.max_same_dir_per_symbol
        cooldown_sec = self.same_dir_cooldown_sec

        # Feature disabled if both are non-positive
        if max_same <= 0 and cooldown_sec <= 0:
            return {"blocked": False, "reason": ""}

        positions = mt5.positions_get(symbol=symbol) or []
        same_dir_positions = []

        for p in positions:
            if side == "LONG" and p.type == mt5.POSITION_TYPE_BUY:
                same_dir_positions.append(p)
            elif side == "SHORT" and p.type == mt5.POSITION_TYPE_SELL:
                same_dir_positions.append(p)

        # --- Per-direction cap -----------------------------------------
        if max_same > 0 and len(same_dir_positions) >= max_same:
            log.info(
                "[STACK] %s %s blocked: same-dir cap reached (%d/%d)",
                symbol,
                side,
                len(same_dir_positions),
                max_same,
            )
            return {"blocked": True, "reason": "same_dir_cap"}

        # --- Cooldown since last entry in this direction ---------------
        if cooldown_sec > 0 and same_dir_positions:
            try:
                now_ts = float(mt5.time_current())
            except Exception:
                now_ts = time.time()

            # Prefer MT5 epoch timestamps if available (normalize ms → s when needed)
            def _pos_ts(p):
                if hasattr(p, "time_setup") and p.time_setup:
                    ts = float(p.time_setup)
                    if ts > 1e10:  # looks like ms epoch
                        ts = ts / 1000.0
                    return ts

                if hasattr(p, "time_msc") and p.time_msc:
                    return float(p.time_msc) / 1000.0

                # Absolute last fallback (normalize if needed)
                try:
                    ts = float(p.time)
                    if ts > 1e10:
                        ts = ts / 1000.0
                    return ts
                except Exception:
                    return 0.0

            last_ts = max(_pos_ts(p) for p in same_dir_positions)
            elapsed = now_ts - last_ts

            # PATCH: if broker/MT5 timestamp appears ahead of local clock,
            # do NOT clamp to 0 (that freezes the countdown at full cooldown).
            # Treat as clock skew and use absolute delta so cooldown can decay.
            if elapsed < 0:
                log.warning(
                    "[STACK] %s %s time skew detected (now_ts=%.0f last_ts=%.0f, elapsed=%.1fs). "
                    "Using abs(elapsed) for cooldown.",
                    symbol,
                    side,
                    now_ts,
                    last_ts,
                    elapsed,
                )
                elapsed = abs(elapsed)

            if elapsed < cooldown_sec:
                remaining = int(max(0.0, cooldown_sec - elapsed))
                log.info(
                    "[STACK] %s %s blocked: same-dir cooldown active "
                    "(%ds remaining, last_entry=%ds ago)",
                    symbol,
                    side,
                    remaining,
                    int(elapsed),
                )
                return {"blocked": True, "reason": "same_dir_cooldown"}

        # --- All clear --------------------------------------------------
        return {"blocked": False, "reason": ""}

    def _run_symbol(self, sym: str, summary: dict):
        cid = _next_cycle_id()
        status = "UNKNOWN"
        extra = ""

        _cycle_start(cid, sym, self.timeframe)

        try:
            # --- Session Risk Controller (daily DD / losing-streak brakes) ---
            src = check_xau_risk(sym)
            if src.get("blocked"):
                log.info(
                    "[SRC] %s trade blocked: %s",
                    sym,
                    src.get("reason", "unknown"),
                )
                summary["blocked"] += 1
                status = "BLOCKED"
                extra = "src"
                return

            # --- Data fetch ---------------------------------------------------
            bars = get_bars(sym, timeframe=self.timeframe, limit=self.bar_history)
            if len(bars) < 50:
                log.warning("[DATA] %s insufficient bars (%d < 50)", sym, len(bars))
                summary["errors"] += 1
                status = "ERROR"
                extra = "insufficient_bars"
                return

            feats = compute_features(sym)
            if not feats:
                log.warning("[DATA] %s feature extraction failed", sym)
                summary["errors"] += 1
                status = "ERROR"
                extra = "features_failed"
                return

            # --------------------------------------------------
            # STEP 1 — HARD DECISION CONTEXT GATE (XAU)
            # (IMPORTANT: decide_signal called ONCE)
            # --------------------------------------------------
            decision = decide_signal(feats, ENV)
            preview = decision.get("preview", {})

            policy = preview.get("policy", "")
            atr_regime_ctx = preview.get("atr_regime", "")
            session_ctx = preview.get("session", "")

            side = (preview.get("side") or "").upper()
            confidence = float(preview.get("confidence", 0.0))
            why = preview.get("why") or []

            # If no trade signal, log SKIP with full context
            if not side:
                log.info(
                    "[SKIP] %s no trade (conf=%.2f, reason=%s, policy=%s, atr_regime=%s, session=%s)",
                    sym,
                    confidence,
                    why,
                    policy,
                    atr_regime_ctx,
                    session_ctx,
                )
                _emit_event(
                    "SKIP",
                    {
                        "cycle_id": cid,
                        "symbol": sym,
                        "side": "",
                        "confidence": round(confidence, 4),
                        "policy": policy,
                        "atr_regime": atr_regime_ctx,
                        "session": session_ctx,
                        "why": why,
                        "reason": "no_side",
                    },
                )
                summary["skipped"] += 1
                status = "SKIP"
                extra = "no_side"
                return

            if side not in ("LONG", "SHORT"):
                log.info(
                    "[SKIP] %s invalid side (%s) preview=%s",
                    sym,
                    side,
                    preview,
                )
                summary["skipped"] += 1
                status = "SKIP"
                extra = "invalid_side"
                return

            if confidence <= 0.0:
                log.info(
                    "[SKIP] %s zero confidence side=%s (policy=%s, atr_regime=%s, session=%s)",
                    sym,
                    side,
                    policy,
                    atr_regime_ctx,
                    session_ctx,
                )
                summary["skipped"] += 1
                status = "SKIP"
                extra = "zero_conf"
                return

            if not isinstance(why, list) or len(why) == 0:
                log.info(
                    "[SKIP] %s empty WHY side=%s conf=%.3f",
                    sym,
                    side,
                    confidence,
                )
                summary["skipped"] += 1
                status = "SKIP"
                extra = "empty_why"
                return

            # --------------------------------------------------
            # STEP 3 — QUIET REGIME: SWING-ONLY ENFORCEMENT
            # --------------------------------------------------
            atr_regime = str(preview.get("atr_regime", "") or "").upper()
            if atr_regime == "QUIET" and not preview.get("swing_lock_allowed", False):
                log.info(
                    "[SKIP] %s QUIET regime non-swing entry blocked (why=%s)",
                    sym,
                    why,
                )
                summary["skipped"] += 1
                status = "SKIP"
                extra = "quiet_non_swing"
                return

            # --- Same-direction stacking guard -----------------------------
            stack_guard = self._check_same_direction_guard(sym, side)
            if stack_guard.get("blocked"):
                log.info(
                    "[BLOCKED] %s %s blocked by same-dir guard: %s",
                    sym,
                    side,
                    stack_guard.get("reason", "unknown"),
                )
                _emit_event(
                    "BLOCKED",
                    {
                        "cycle_id": cid,
                        "symbol": sym,
                        "side": side,
                        "confidence": round(confidence, 4),
                        "policy": policy,
                        "atr_regime": atr_regime_ctx,
                        "session": session_ctx,
                        "why": why,
                        "reason": str(stack_guard.get("reason", "unknown")),
                        "blocked_by": "same_dir_guard",
                    },
                )
                summary["blocked"] += 1
                status = "BLOCKED"
                extra = "same_dir_guard"
                return

            # ------------------------------------------------------------
            # SCCR Decision Completeness Gate (XAU)
            # Prevent execution without full decision context
            # ------------------------------------------------------------
            policy_l = str(policy or "").lower()
            if policy_l not in ("strict", "flexible", "aggressive"):
                log.info("[BLOCKED] %s decision incomplete (policy=%s)", sym, policy_l or "missing")
                summary["blocked"] += 1
                status = "BLOCKED"
                extra = "incomplete_policy"
                return

            atr_regime_l = str(atr_regime_ctx or "").lower()
            if atr_regime_l not in ("quiet", "normal", "hot"):
                log.info("[BLOCKED] %s decision incomplete (atr_regime=%s)", sym, atr_regime_l or "missing")
                summary["blocked"] += 1
                status = "BLOCKED"
                extra = "incomplete_atr"
                return

            session_u = str(session_ctx or "").upper()
            if session_u not in ("IN", "OUT"):
                log.info("[BLOCKED] %s decision incomplete (session=%s)", sym, session_u or "missing")
                summary["blocked"] += 1
                status = "BLOCKED"
                extra = "incomplete_session"
                return

            # --------------------------------------------------
            # SAFE TO EXECUTE
            # --------------------------------------------------
            sl_points = float(preview.get("sl_points", 300))
            tp_points = float(preview.get("tp_points", 600))
            base_lot = float(preview.get("base_lot", self.min_lots))
            why_str = "|".join([str(x) for x in why])

            log.info(
                "[TRADE_START] %s side=%s conf=%.2f policy=%s atr_regime=%s session=%s lot=%.2f SL=%.1f TP=%.1f why=%s",
                sym,
                side,
                confidence,
                policy,
                atr_regime_ctx,
                session_ctx,
                base_lot,
                sl_points,
                tp_points,
                why_str,
            )
            _emit_event(
                "TRADE_START",
                {
                    "cycle_id": cid,
                    "symbol": sym,
                    "side": side,
                    "confidence": round(confidence, 4),
                    "policy": policy,
                    "atr_regime": atr_regime_ctx,
                    "session": session_ctx,
                    "base_lot": round(base_lot, 4),
                    "sl_points": round(sl_points, 2),
                    "tp_points": round(tp_points, 2),
                    "why": why,
                },
            )
           
            res = execute_trade(
                symbol=sym,
                side=side,
                base_lot=base_lot,
                sl_points=sl_points,
                tp_points=tp_points,
                confidence=confidence,
                atr_pct=float(feats.get("atr_pct", 0.0)),
            )

            if not isinstance(res, dict):
                res = {"ok": False, "error": f"execute_trade returned {type(res)}", "raw": res}

            ticket = None
            try:
                ticket = res.get("ticket") or res.get("order") or res.get("deal")
                if ticket:
                    log.info(
                        "[EXECUTOR] %s ticket=%s policy=%s atr_regime=%s session=%s why=%s",
                        sym,
                        str(ticket),
                        str(policy),
                        str(atr_regime_ctx),
                        str(session_ctx),
                        why_str,
                    )
            except Exception:
                pass

            if res.get("ok"):
                exec_lots = float(res.get("lots", preview.get("base_lot", self.min_lots)))
                log.info(
                    "[AGENT] %s %s ok (%.2f lots, conf=%.2f, policy=%s, atr_regime=%s, session=%s, why=%s)",
                    sym,
                    side,
                    exec_lots,
                    confidence,
                    policy,
                    atr_regime_ctx,
                    session_ctx,
                    why_str,
                )
                log.info(
                    "[TRADE_END] %s status=EXECUTED side=%s ticket=%s lots=%.2f",
                    sym,
                    side,
                    str(ticket or "-"),
                    exec_lots,
                )
                _emit_event(
                    "TRADE_END",
                    {
                        "cycle_id": cid,
                        "symbol": sym,
                        "status": "EXECUTED",
                        "side": side,
                        "ticket": str(ticket or "-"),
                        "lots": round(exec_lots, 4),
                        "confidence": round(confidence, 4),
                        "policy": policy,
                        "atr_regime": atr_regime_ctx,
                        "session": session_ctx,
                        "why": why,
                    },
                )

                
                update_trust(sym, True)
                summary["executed"] += 1
                status = "EXECUTED"
                extra = f"ticket={ticket or '-'}"

            elif res.get("blocked"):
                reason = str(res.get("reason", "guardrail"))
                log.info("[BLOCKED] %s blocked by guardrails.", sym)
                log.info("[TRADE_END] %s status=BLOCKED side=%s ticket=%s reason=%s", sym, side, str(ticket or "-"), reason)

                _emit_event(
                    "TRADE_END",
                    {
                        "cycle_id": cid,
                        "symbol": sym,
                        "status": "BLOCKED",
                        "side": side,
                        "ticket": str(ticket or "-"),
                        "confidence": round(confidence, 4),
                        "policy": policy,
                        "atr_regime": atr_regime_ctx,
                        "session": session_ctx,
                        "why": why,
                        "reason": reason,
                        "blocked_by": "executor_guardrail",
                    },
                )

                

                summary["blocked"] += 1
                status = "BLOCKED"
                extra = "executor_guardrail"

            else:
                err = str(res.get("error") or res.get("reason") or "execution_failed")
                log.warning("[FAILED] %s %s -> %s", sym, side, res)
                log.info("[TRADE_END] %s status=FAILED side=%s ticket=%s error=%s", sym, side, str(ticket or "-"), err)

                _emit_event(
                    "TRADE_END",
                    {
                        "cycle_id": cid,
                        "symbol": sym,
                        "status": "FAILED",
                        "side": side,
                        "ticket": str(ticket or "-"),
                        "confidence": round(confidence, 4),
                        "policy": policy,
                        "atr_regime": atr_regime_ctx,
                        "session": session_ctx,
                        "why": why,
                        "error": err,
                    },
                )

                
                update_trust(sym, False)
                summary["errors"] += 1
                status = "FAILED"
                extra = "execution_failed"
         
        except Exception as e:
            log.exception("[ERROR] %s failed: %s", sym, e)
            _emit_event(
                "ERROR",
                {
                    "cycle_id": cid,
                    "symbol": sym,
                    "error": str(e),
                    "error_type": type(e).__name__,
                },
            )
            summary["errors"] += 1
            status = "EXCEPTION"
            extra = type(e).__name__

        finally:
            _cycle_end(cid, status, extra)


    # ------------------------------------------------------------
    # Run one batch of all configured symbols
    # ------------------------------------------------------------
    def run_once(self):
        summary = {"executed": 0, "skipped": 0, "blocked": 0, "errors": 0}
        for sym in self.symbols:
            self._run_symbol(sym, summary)
        log.info(
            "[SUMMARY] Executed=%d | Skipped=%d | Blocked=%d | Errors=%d",
            summary["executed"],
            summary["skipped"],
            summary["blocked"],
            summary["errors"],
        )

    # ------------------------------------------------------------
    # Continuous loop
    # ------------------------------------------------------------
    def run_forever(self, interval: int | None = None):
        loop_interval = interval or self.loop_delay
        log.info("[LOOP] Starting continuous loop (interval=%ds)", loop_interval)
        while True:
            self.run_once()
            time.sleep(loop_interval)
