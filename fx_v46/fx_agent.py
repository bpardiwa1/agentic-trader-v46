from __future__ import annotations
import time
import MetaTrader5 as mt5  # type: ignore

from fx_v4.app.fx_env import ENV
from fx_v4.fx_features import compute_features
from fx_v4.fx_decider import decide_signal
from fx_v4.fx_executor import execute_trade, _can_open_trade
from fx_v4.acmi.acmi_interface import ACMI
from fx_v4.util.logger import setup_logger

log = setup_logger("fx_agent", level="INFO")


class FxAgent:
    def __init__(self):
        self.env = ENV

        log.info("========== FX v4 Environment Summary ==========")
        log.info("Symbols           : %s", ", ".join(self.env.symbols))
        log.info("Timeframe         : %s", self.env.timeframe)
        log.info("Min Confidence    : %.2f", self.env.min_conf)
        log.info("ATR Enabled       : %s", self.env.atr_enabled)
        log.info("ATR Period        : %d", self.env.atr_period)
        log.info("ATR SL Multiplier : %.2f", self.env.atr_sl_mult)
        log.info("ATR TP Multiplier : %.2f", self.env.atr_tp_mult)
        log.info("Dynamic Lots      : %s", self.env.dynamic_lots)
        log.info("Mixed Regimes OK  : %s", self.env.accept_mixed)
        log.info("Min Lots          : %.3f", self.env.min_lots)
        log.info("Max Lots          : %.3f", self.env.max_lots)
        log.info("Max Symbols/Loop  : %d", self.env.max_symbols)
        log.info("Symbol Batch Delay: %.2fs", self.env.batch_delay)
        log.info("Max Open Trades   : %d", self.env.agent_max_open)
        log.info("Max Per Symbol    : %d", self.env.agent_max_per_symbol)
        log.info("===============================================")

        if not mt5.initialize():
            log.warning("[MT5] Initialization failed. last_error=%s", mt5.last_error())
        else:
            log.info("[MT5] Connected and initialized.")

    # ---------------------------------------------------
    def _run_symbol(self, sym: str, summary: dict):
        """Runs one symbol cycle: compute → decide → execute."""
        try:
            params = self.env.per[sym]
            feats = compute_features(sym, params, self.env)
            if not feats:
                log.warning("[DATA] No bars for %s", sym)
                summary["skipped"] += 1
                return

            decision = decide_signal(feats, self.env)
            ACMI.post_status(sym, decision)
            preview = decision.get("preview", {})

            # Skip if no valid trade side
            if not preview.get("side"):
                log.info("[SKIP] %s no trade (conf=%.2f, reason=%s)",
                         sym, preview.get("confidence", 0.0),
                         preview.get("why", []))
                summary["skipped"] += 1
                return

            # ✅ NEW: dynamic guardrail check from fx_executor
            if not _can_open_trade(sym):
                log.info("[BLOCKED] %s blocked by guardrails.", sym)
                ACMI.post_status(sym, {"guardrail_blocked": True})
                summary["blocked"] += 1
                return

            # Proceed to execute
            result = execute_trade(
                sym,
                preview["side"],
                params.lots,
                float(preview["sl_pips"]),
                float(preview["tp_pips"]),
                self.env,
            )
            ACMI.post_status(sym, {"executed": result})

            # ✅ Enhanced result handling
            if result.get("ok"):
                log.info("[EXECUTED] %s %s ok (%.2f lots)",
                         sym, preview["side"], params.lots)
                summary["executed"] += 1

            elif result.get("blocked"):
                log.warning("[BLOCKED] %s %s blocked by guardrail (%s).",
                            sym, preview["side"], result.get("reason", "limit"))
                summary["blocked"] += 1

            else:
                log.warning("[FAILED] %s %s -> %s",
                            sym, preview["side"], result)
                summary["errors"] += 1

        except Exception as e:
            summary["errors"] += 1
            log.exception("[ERROR] %s failed: %s", sym, e)

    # ---------------------------------------------------
    def run_once(self):
        """Run single iteration for all configured symbols."""
        log.info("[RUN] Processing %d symbols.", len(self.env.symbols))
        summary = {"executed": 0, "skipped": 0, "blocked": 0, "errors": 0}

        for sym in self.env.symbols:
            self._run_symbol(sym, summary)
            time.sleep(self.env.batch_delay)

        log.info("[SUMMARY] Executed: %d | Skipped: %d | Blocked: %d | Errors: %d",
                 summary["executed"], summary["skipped"],
                 summary["blocked"], summary["errors"])
        log.info("[RUN-END] Completed symbol batch.\n")

    # ---------------------------------------------------
    def run_forever(self, interval: int = 30):
        """Continuous loop runner."""
        log.info("[LOOP] Starting continuous loop (interval=%ds).", interval)
        while True:
            self.run_once()
            time.sleep(interval)
