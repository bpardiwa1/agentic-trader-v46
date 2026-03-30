from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any, Dict

import MetaTrader5 as mt5  # type: ignore

from fx_v46.app.fx_env_v46 import ENV
from fx_v46.fx_features_v46 import compute_features
from fx_v46.fx_decider_v46 import decide_signal
from fx_v46.fx_executor_v46 import execute_trade, _can_open_trade
from fx_v46.acmi.acmi_interface_v46 import ACMI
from fx_v46.util.logger import setup_logger
from fx_v46.util.fx_session_risk_v46 import check_fx_risk  # 🔹 NEW
from fx_v46.util.fx_event_sink import emit_event

# Unified FX logging (single daily file under logs/fx_v4.6)
_FX_LOG_DIR = "logs/fx_v4.6"
_FX_LOG_LEVEL = str(ENV.get("FX_LOG_LEVEL", "INFO")).upper()
_FX_LOG_NAME = f"fx_v46_{datetime.now():%Y-%m-%d}"
log = setup_logger(_FX_LOG_NAME, log_dir=_FX_LOG_DIR, level=_FX_LOG_LEVEL)

# ------------------------------------------------------------
# EVENT JSONL helper (watcher looks for token 'EVENT' + JSON)
# ------------------------------------------------------------
def _emit_event(event: str, **fields):
    emit_event(event, fields, log=log, asset="FX")

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
    log.info(
        "[CYCLE] %s %s tf=%s ts=%s",
        cid,
        sym,
        tf,
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )
    _emit_event("CYCLE_START", module="agent", cycle=cid, symbol=sym, timeframe=tf)


def _cycle_end(cid: str, status: str, extra: str = "") -> None:
    if extra:
        log.info("[CYCLE_END] %s status=%s %s", cid, status, extra)
    else:
        log.info("[CYCLE_END] %s status=%s", cid, status)
    log.info("-" * 92)
    _emit_event("CYCLE_END", module="agent", cycle=cid, status=status, extra=extra)


class FxAgentV46:
    def __init__(self, symbols: list[str] | None = None):
        """Initialize FX agent. Can override symbols from CLI."""
        self.env = ENV

        # ------------------------------------------------------------
        # Timeframe (needed by cycle markers / logging)
        # ------------------------------------------------------------
        self.timeframe = str(
            self.env.get("FX_TIMEFRAME", self.env.get("TIMEFRAME", "M15"))
        ).upper()

        if symbols:
            # override env if CLI symbols provided
            self.env.symbols = symbols

        log.info("========== FX v4.6 Environment ==========")
        log.info("Env File          : %s", str(getattr(self.env, "env_path", "unknown")))
        log.info(
            "Agent Symbols     : %s",
            str(getattr(self.env, "symbols_raw", getattr(self.env, "symbols", []))),
        )
        # --- Environment summary (type-safe access) ---
        log.info(
            "Min Confidence    : %.2f",
            float(self.env.get("AGENT_MIN_CONFIDENCE", 0.55)),
        )
        log.info("ATR Enabled       : %s", str(self.env.get("FX_ATR_ENABLED", True)))
        log.info("ATR Period        : %d", int(self.env.get("FX_ATR_PERIOD", 14)))
        log.info(
            "ATR SL Multiplier : %.2f", float(self.env.get("FX_ATR_SL_MULT", 2.0))
        )
        log.info(
            "ATR TP Multiplier : %.2f", float(self.env.get("FX_ATR_TP_MULT", 3.0))
        )
        log.info("Dynamic Lots      : %s", str(self.env.get("FX_DYNAMIC_LOTS", True)))
        log.info("Min Lots          : %.3f", float(self.env.get("FX_MIN_LOTS", 0.03)))
        log.info("Max Lots          : %.3f", float(self.env.get("FX_MAX_LOTS", 0.30)))
        log.info("Confidence Gate   : %s", str(self.env.get("FX_CONFIDENCE_GATE", False)))
        log.info("Max Symbols/Loop  : %d", int(self.env.get("FX_MAX_SYMBOLS", 1)))
        log.info(
            "Symbol Batch Delay: %.2fs", float(self.env.get("FX_SYMBOL_BATCH_DELAY", 2.0))
        )
        log.info("Cooldown Seconds  : %d", int(self.env.get("FX_COOLDOWN_SEC", 60)))
        log.info(
            "Block Same Dir    : %s", str(self.env.get("FX_BLOCK_SAME_DIRECTION", True))
        )
        log.info("Max Open Trades   : %d", int(self.env.get("AGENT_MAX_OPEN", 10)))
        log.info("Max Per Symbol    : %d", int(self.env.get("AGENT_MAX_PER_SYMBOL", 3)))
        log.info("===========================================")

        if not mt5.initialize():
            log.warning("[MT5] Initialization failed. last_error=%s", mt5.last_error())
            _emit_event(
                "ERROR",
                module="agent",
                where="mt5.initialize",
                mt5_last_error=str(mt5.last_error()),
            )
        else:
            log.info("[MT5] Connected.")
            _emit_event(
                "RUN_START",
                module="agent",
                symbols=list(getattr(self.env, "symbols", [])),
                timeframe=self.timeframe,
            )

    def _run_symbol(self, sym: str, summary: dict):
        """Runs one symbol cycle: compute → decide → execute."""
        cid = _next_cycle_id()
        status = "UNKNOWN"
        extra = ""

        _cycle_start(cid, sym, self.timeframe)

        try:
            # -----------------------------------------------------------
            # 1) Load per-symbol parameters from ENV.per using base symbol
            #    e.g. EURUSD-ECNc -> EURUSD
            # -----------------------------------------------------------
            base_sym = sym.split("-")[0]
            params = self.env.per.get(base_sym)
            if not params:
                log.error(
                    "[ERROR] No per-symbol config found for %s (have=%s)",
                    base_sym,
                    list(getattr(self.env, "per", {}).keys()),
                )
                _emit_event(
                    "ERROR",
                    module="agent",
                    cycle=cid,
                    symbol=sym,
                    base_symbol=base_sym,
                    error="missing_per_symbol_config",
                )
                summary["errors"] += 1
                status = "ERROR"
                extra = "missing_per_symbol_config"
                return

            # -----------------------------------------------------------
            # 1b) Session Risk Controller (daily DD / losing-streak brakes)
            # -----------------------------------------------------------
            src = check_fx_risk(sym)
            if src.get("blocked"):
                log.info(
                    "[SRC] %s trade blocked: %s",
                    sym,
                    src.get("reason", "unknown"),
                )
                _emit_event(
                    "BLOCKED",
                    module="agent",
                    cycle=cid,
                    symbol=sym,
                    reason="src",
                    detail=str(src.get("reason", "unknown")),
                    src=src,
                )
                summary["blocked"] += 1
                status = "BLOCKED"
                extra = "src"
                return

            # -----------------------------------------------------------
            # 2) Compute features
            # -----------------------------------------------------------
            feats = compute_features(sym, params, self.env)
            if not feats:
                log.warning("[DATA] No bars for %s", sym)
                _emit_event(
                    "SKIP",
                    module="agent",
                    cycle=cid,
                    symbol=sym,
                    reason="no_bars",
                )
                summary["skipped"] += 1
                status = "SKIP"
                extra = "no_bars"
                return

            # -----------------------------------------------------------
            # 3) Make trade decision (STRICT / FLEX / AGGR)
            # -----------------------------------------------------------
            decision = decide_signal(feats, self.env)
            ACMI.post_status(sym, decision)

            preview = decision.get("preview", {})
            side = preview.get("side", "")
            conf = float(preview.get("confidence", 0.0))
            reasons = preview.get("why", [])

            # extra context from decider (may or may not be present)
            policy = preview.get("policy")
            min_conf_gate = preview.get("min_conf_gate")
            atr_floor = preview.get("atr_floor")
            atr_pct = preview.get("atr_pct")

            # Regime tagging (parity with IDX/XAU dashboards)
            atr_level = (
                preview.get("atr_level")
                or preview.get("regime")
                or preview.get("atr_regime")
                or "unknown"
            )
            regime = str(atr_level).upper()

            # Enrich reasons with policy/regime markers for analyzers
            reasons_enriched = list(reasons) if isinstance(reasons, list) else [str(reasons)]
            if policy:
                tok = f"policy_{policy}"
                if tok not in reasons_enriched:
                    reasons_enriched.append(tok)
            if regime and regime not in ("UNKNOWN", "NONE", "-"):
                tok = f"regime_{regime.lower()}"
                if tok not in reasons_enriched:
                    reasons_enriched.append(tok)

            # Log preview decision including SL/TP even if we later skip
            sl_preview = float(preview.get("sl_pips", params.get("sl_pips", 40.0)))
            tp_preview = float(preview.get("tp_pips", params.get("tp_pips", 90.0)))
            try:
                log.info(
                    "[PREVIEW] %s conf=%.4f policy=%s regime=%s side=%s atr_pct=%.4f SL=%.1f TP=%.1f reason=%s",
                    sym,
                    conf,
                    str(policy or "unknown"),
                    regime,
                    side or "-",
                    float(atr_pct or 0.0),
                    sl_preview,
                    tp_preview,
                    reasons_enriched,
                )
            except Exception:
                log.exception("[ERROR] Failed to log preview for %s", sym)

            # Emit DECISION event (JSONL)
            _emit_event(
                "DECISION",
                module="decider",
                cycle=cid,
                symbol=sym,
                side=side or "",
                confidence=conf,
                policy=str(policy or "unknown"),
                regime=regime,
                atr_pct=float(atr_pct or 0.0),
                sl_pips=sl_preview,
                tp_pips=tp_preview,
                why=reasons_enriched,
                min_conf_gate=min_conf_gate,
                atr_floor=atr_floor,
            )

            # -----------------------------------------------------------
            # 4) Skip if no valid side
            # -----------------------------------------------------------
            if not side:
                if policy is not None and min_conf_gate is not None and atr_floor is not None:
                    log.info(
                        "[SKIP] %s conf=%.4f policy=%s regime=%s atr_pct=%.4f atr_floor=%.4f min_conf=%.4f reason=%s",
                        sym,
                        conf,
                        str(policy or "unknown"),
                        regime,
                        float(atr_pct or 0.0),
                        float(atr_floor),
                        float(min_conf_gate),
                        reasons_enriched,
                    )
                else:
                    log.info(
                        "[SKIP] %s conf=%.4f policy=%s regime=%s reason=%s",
                        sym,
                        conf,
                        str(policy or "unknown"),
                        regime,
                        reasons_enriched,
                    )

                _emit_event(
                    "SKIP",
                    module="agent",
                    cycle=cid,
                    symbol=sym,
                    reason="no_side",
                    confidence=conf,
                    policy=str(policy or "unknown"),
                    regime=regime,
                    why=reasons_enriched,
                )

                summary["skipped"] += 1
                status = "SKIP"
                extra = "no_side"
                return

            # -----------------------------------------------------------
            # 5) Guardrail check before executing
            # -----------------------------------------------------------
            if not _can_open_trade(sym, side):
                log.warning("[BLOCKED] %s %s blocked by guardrail.", sym, side)
                _emit_event(
                    "BLOCKED",
                    module="agent",
                    cycle=cid,
                    symbol=sym,
                    side=side,
                    reason="guardrail",
                )
                summary["blocked"] += 1
                status = "BLOCKED"
                extra = "guardrail"
                return

            # -----------------------------------------------------------
            # 6) Execute trade safely
            # -----------------------------------------------------------
            lots = float(params.get("lots", 0.10))
            sl_pips = float(preview.get("sl_pips", params.get("sl_pips", 40.0)))
            tp_pips = float(preview.get("tp_pips", params.get("tp_pips", 90.0)))

            try:
                log.info(
                    "[DECIDE] %s conf=%.4f policy=%s regime=%s side=%s atr_pct=%.4f SL=%.1f TP=%.1f why=%s",
                    sym,
                    conf,
                    str(policy or "unknown"),
                    regime,
                    side,
                    float(atr_pct or 0.0),
                    sl_pips,
                    tp_pips,
                    reasons_enriched,
                )
            except Exception:
                log.exception("[ERROR] Failed to log decision for %s", sym)

            # Trade lifecycle markers (clarity for alerts/rollups)
            log.info(
                "[TRADE_START] %s side=%s conf=%.4f policy=%s regime=%s lot=%.2f SL=%.1f TP=%.1f",
                sym,
                side,
                conf,
                str(policy or "unknown"),
                regime,
                lots,
                sl_pips,
                tp_pips,
            )
            _emit_event(
                "TRADE_START",
                module="agent",
                cycle=cid,
                symbol=sym,
                side=side,
                confidence=conf,
                policy=str(policy or "unknown"),
                regime=regime,
                atr_pct=float(atr_pct or 0.0),
                lots=lots,
                sl_pips=sl_pips,
                tp_pips=tp_pips,
                why=reasons_enriched,
            )

            try:
                result = execute_trade(sym, side, lots, sl_pips, tp_pips, confidence=conf)
            except Exception as e:
                log.error("[ERROR] %s execution failed: %s", sym, e)
                _emit_event(
                    "FAILED",
                    module="agent",
                    cycle=cid,
                    symbol=sym,
                    side=side,
                    reason="execution_exception",
                    error=type(e).__name__,
                    detail=str(e),
                )
                summary["errors"] += 1
                status = "ERROR"
                extra = "execution_exception"
                log.info(
                    "[TRADE_END] %s status=ERROR side=%s error=%s",
                    sym,
                    side,
                    type(e).__name__,
                )
                _emit_event(
                    "TRADE_END",
                    module="agent",
                    cycle=cid,
                    symbol=sym,
                    side=side,
                    status="ERROR",
                    error=type(e).__name__,
                )
                return

            ACMI.post_status(sym, {"executed": result})

            ticket = result.get("ticket") or result.get("order") or result.get("deal")

            if result.get("ok"):
                exec_lots = float(result.get("lots", lots))
                log.info("[EXECUTED] %s %s ok (%.2f lots)", sym, side, exec_lots)
                log.info(
                    "[TRADE_END] %s status=EXECUTED side=%s ticket=%s lots=%.2f",
                    sym,
                    side,
                    str(ticket or "-"),
                    exec_lots,
                )
                _emit_event(
                    "EXECUTED",
                    module="agent",
                    cycle=cid,
                    symbol=sym,
                    side=side,
                    ticket=ticket,
                    lots=exec_lots,
                    result=result,
                )
                _emit_event(
                    "TRADE_END",
                    module="agent",
                    cycle=cid,
                    symbol=sym,
                    side=side,
                    status="EXECUTED",
                    ticket=ticket,
                    lots=exec_lots,
                )
                summary["executed"] += 1
                status = "EXECUTED"
                extra = f"ticket={ticket or '-'}"

            elif result.get("blocked"):
                log.info("[BLOCKED] %s %s blocked by cooldown/guardrail", sym, side)
                log.info(
                    "[TRADE_END] %s status=BLOCKED side=%s ticket=%s reason=%s",
                    sym,
                    side,
                    str(ticket or "-"),
                    str(result.get("reason", "guardrail")),
                )
                _emit_event(
                    "BLOCKED",
                    module="agent",
                    cycle=cid,
                    symbol=sym,
                    side=side,
                    reason=str(result.get("reason", "guardrail")),
                    ticket=ticket,
                    result=result,
                )
                _emit_event(
                    "TRADE_END",
                    module="agent",
                    cycle=cid,
                    symbol=sym,
                    side=side,
                    status="BLOCKED",
                    ticket=ticket,
                    reason=str(result.get("reason", "guardrail")),
                )
                summary["blocked"] += 1
                status = "BLOCKED"
                extra = "executor_blocked"
            else:
                log.warning("[FAILED] %s %s -> %s", sym, side, result)
                log.info(
                    "[TRADE_END] %s status=FAILED side=%s ticket=%s error=%s",
                    sym,
                    side,
                    str(ticket or "-"),
                    str(result.get("error") or result.get("reason") or "execution_failed"),
                )
                _emit_event(
                    "FAILED",
                    module="agent",
                    cycle=cid,
                    symbol=sym,
                    side=side,
                    ticket=ticket,
                    reason=str(result.get("error") or result.get("reason") or "execution_failed"),
                    result=result,
                )
                _emit_event(
                    "TRADE_END",
                    module="agent",
                    cycle=cid,
                    symbol=sym,
                    side=side,
                    status="FAILED",
                    ticket=ticket,
                    reason=str(result.get("error") or result.get("reason") or "execution_failed"),
                )
                summary["errors"] += 1
                status = "FAILED"
                extra = "execution_failed"

        except Exception as e:
            summary["errors"] += 1
            log.exception("[ERROR] %s failed: %s", sym, e)
            _emit_event(
                "ERROR",
                module="agent",
                cycle=cid,
                symbol=sym,
                error=type(e).__name__,
                detail=str(e),
            )
            status = "EXCEPTION"
            extra = type(e).__name__

        finally:
            _cycle_end(cid, status, extra)

    # ---------------------------------------------------
    def run_once(self):
        """Run single iteration for all configured symbols."""
        log.info("[RUN] Processing %d symbols.", len(self.env.symbols))
        summary = {"executed": 0, "skipped": 0, "blocked": 0, "errors": 0}

        batch_delay = float(self.env.get("FX_SYMBOL_BATCH_DELAY", 2.0))

        for sym in self.env.symbols:
            self._run_symbol(sym, summary)
            time.sleep(batch_delay)

        log.info(
            "[SUMMARY] Executed: %d | Skipped: %d | Blocked: %d | Errors: %d",
            summary["executed"],
            summary["skipped"],
            summary["blocked"],
            summary["errors"],
        )
        _emit_event("SUMMARY", module="agent", **summary)
        log.info("[RUN-END] Completed symbol batch.\n")

    # ---------------------------------------------------
    def run_forever(self, interval: int = 30):
        """Continuous loop runner."""
        log.info("[LOOP] Starting continuous loop (interval=%ds).", interval)
        _emit_event("LOOP_START", module="agent", interval_sec=int(interval))
        while True:
            self.run_once()
            time.sleep(interval)
