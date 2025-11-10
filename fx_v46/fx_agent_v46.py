from __future__ import annotations
import os
import time
import MetaTrader5 as mt5  # type: ignore

from fx_v46.app.fx_env_v46 import ENV
from fx_v46.fx_features_v46 import compute_features
from fx_v46.fx_decider_v46 import decide_signal
from fx_v46.fx_executor_v46 import execute_trade, _can_open_trade
from fx_v46.acmi.acmi_interface_v46 import ACMI
from fx_v46.util.logger import setup_logger

log = setup_logger("fx_agent_v46", level="INFO")


class FxAgentV46:
    def __init__(self, symbols: list[str] | None = None):
        """Initialize FX agent. Can override symbols from CLI."""
        self.env = ENV
        if symbols:
            # override env if CLI symbols provided
            self.env.symbols = symbols

        log.info("========== FX v4.6 Environment ==========")
        # --- Environment summary (type-safe access) ---
        log.info("Min Confidence    : %.2f", float(self.env.get("AGENT_MIN_CONFIDENCE", 0.55)))
        log.info("ATR Enabled       : %s", str(self.env.get("FX_ATR_ENABLED", True)))
        log.info("ATR Period        : %d", int(self.env.get("FX_ATR_PERIOD", 14)))
        log.info("ATR SL Multiplier : %.2f", float(self.env.get("FX_ATR_SL_MULT", 2.0)))
        log.info("ATR TP Multiplier : %.2f", float(self.env.get("FX_ATR_TP_MULT", 3.0)))
        log.info("Dynamic Lots      : %s", str(self.env.get("FX_DYNAMIC_LOTS", True)))
        log.info("Min Lots          : %.3f", float(self.env.get("FX_MIN_LOTS", 0.03)))
        log.info("Max Lots          : %.3f", float(self.env.get("FX_MAX_LOTS", 0.30)))
        log.info("Confidence Gate   : %s", str(self.env.get("FX_CONFIDENCE_GATE", False)))
        log.info("Max Symbols/Loop  : %d", int(self.env.get("FX_MAX_SYMBOLS", 1)))
        log.info("Symbol Batch Delay: %.2fs", float(self.env.get("FX_SYMBOL_BATCH_DELAY", 2.0)))
        log.info("Cooldown Seconds  : %d", int(self.env.get("FX_COOLDOWN_SEC", 60)))
        log.info("Block Same Dir    : %s", str(self.env.get("FX_BLOCK_SAME_DIRECTION", True)))
        log.info("Max Open Trades   : %d", int(self.env.get("AGENT_MAX_OPEN", 10)))
        log.info("Max Per Symbol    : %d", int(self.env.get("AGENT_MAX_PER_SYMBOL", 3)))
        log.info("===========================================")

        if not mt5.initialize():
            log.warning("[MT5] Initialization failed. last_error=%s", mt5.last_error())
        else:
            log.info("[MT5] Connected.")

    def _run_symbol(self, sym: str, summary: dict):
        """Runs one symbol cycle: compute → decide → execute."""
        try:
            # params = self.env.per[sym]
            params = {
                "ema_fast": int(os.getenv(f"EMA_FAST_{sym.upper()}", 20)),
                "ema_slow": int(os.getenv(f"EMA_SLOW_{sym.upper()}", 50)),
                "rsi_period": int(os.getenv(f"RSI_PERIOD_{sym.upper()}", 14)),
                "rsi_long_th": float(os.getenv(f"RSI_LONG_TH_{sym.upper()}", 55)),
                "rsi_short_th": float(os.getenv(f"RSI_SHORT_TH_{sym.upper()}", 45)),
                "sl_pips": float(os.getenv(f"SL_{sym.upper()}", 40)),
                "tp_pips": float(os.getenv(f"TP_{sym.upper()}", 90)),
                "lots": float(os.getenv(f"LOTS_{sym.upper()}", 0.1)),
            }

            feats = compute_features(sym, params, self.env)
            feats = compute_features(sym, params, self.env)
            if not feats:
                log.warning("[DATA] No bars for %s", sym)
                summary["skipped"] += 1
                return

            # --- Make trade decision ---
            decision = decide_signal(feats, self.env)
            ACMI.post_status(sym, decision)
            preview = decision.get("preview", {})

            side = preview.get("side", "")
            conf = float(preview.get("confidence", 0.0))
            reasons = preview.get("why", [])

            # --- Skip if no valid side ---
            if not side:
                log.info("[SKIP] %s no trade (conf=%.2f, reason=%s)", sym, conf, reasons)
                summary["skipped"] += 1
                return

            # --- Get per-symbol parameters safely ---
            base_sym = sym.split("-")[0]  # e.g. AUDUSD-ECNc → AUDUSD
            params = self.env.per.get(base_sym)
            if not params:
                log.error("[ERROR] No per-symbol config found for %s", base_sym)
                summary["errors"] += 1
                return

            # --- Guardrail check before executing ---
            if not _can_open_trade(sym, side):
                log.warning("[BLOCKED] %s %s blocked by guardrail.", sym, side)
                summary["blocked"] += 1
                return

            # --- Execute trade safely ---
            try:
                execute_trade(
                    sym,
                    side,
                    float(params.get("lots", 0.10)),
                    float(params.get("sl_pips", 40.0)),
                    float(params.get("tp_pips", 90.0)),
                )
                summary["executed"] += 1
            except Exception as e:
                log.error("[ERROR] %s execution failed: %s", sym, e)
                summary["errors"] += 1

            # ✅ Guardrail check
            
            side = decision["preview"]["side"] if "preview" in decision else ""
            if not _can_open_trade(sym, side):
                log.info("[BLOCKED] %s blocked by guardrails.", sym)
                ACMI.post_status(sym, {"guardrail_blocked": True})
                summary["blocked"] += 1
                return

            # ✅ Execute trade
            result = execute_trade(
                sym,
                preview["side"],
                params.lots,
                float(preview["sl_pips"]),
                float(preview["tp_pips"]),
                self.env,
            )
            ACMI.post_status(sym, {"executed": result})

            if result.get("ok"):
                log.info("[EXECUTED] %s %s ok (%.2f lots)",
                         sym, preview["side"], params.lots)
                summary["executed"] += 1
            elif result.get("blocked"):
                log.info("[BLOCKED] %s %s blocked by cooldown/guardrail", sym, preview["side"])
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
            time.sleep(float(self.env.fx_symbol_batch_delay))


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
