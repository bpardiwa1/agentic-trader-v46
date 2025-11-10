# ============================================================
# Agentic Trader IDX v4.6a — Main Trading Agent
# ------------------------------------------------------------
# • Fetches bars, computes features, decides trades, executes
# • Handles both env-based and parameterized initialization
# • Proper skip handling for outside trading windows
# ============================================================

from __future__ import annotations
import time
import traceback
from idx_v46.app.idx_env_v46 import ENV
from core.mt5_connect_v46 import ensure_mt5_initialized
from idx_v46.util.logger import setup_logger
from idx_v46.idx_features_v46 import compute_features
from idx_v46.idx_decider_v46 import decide
from idx_v46.idx_executor_v46 import execute_trade

log = setup_logger("idx_agent_v46", level=ENV.get("LOG_LEVEL", "INFO").upper())


class IdxAgentV46:
    def __init__(self, symbols: list[str] | None = None, timeframe: str | None = None):
        # Allow external or env-driven setup
        self.symbols = symbols or [
            s.strip() for s in ENV.get("AGENT_SYMBOLS", "").split(",") if s.strip()
        ]
        self.tf = timeframe or ENV.get("IDX_TIMEFRAME", "M15")
        self.interval = int(ENV.get("IDX_INTERVAL", 60))

        ensure_mt5_initialized(ENV)
        log.info(
            "[MT5] Connected build=%s | Timeframe=%s | Bars=%s | Lots=[%.2f–%.2f]",
            ENV.get("MT5_BUILD", "(unknown)"),
            self.tf,
            ENV.get("IDX_BARS", 240),
            float(ENV.get("IDX_MIN_LOTS", 0.10)),
            float(ENV.get("IDX_MAX_LOTS", 0.30)),
        )

    # --------------------------------------------------------
    # Core Loop
    # --------------------------------------------------------
    def run_forever(self):
        """Continuous loop mode."""
        log.info("[LOOP] Continuous run mode enabled.")
        while True:
            self.run_once()
            time.sleep(self.interval)

    def run_once(self):
        """Single iteration of processing all symbols."""
        log.info("[LOOP] Continuous mode (interval=%ds)", self.interval)
        executed = skipped = blocked = errors = 0

        for sym in self.symbols:
            try:
                res = self._run_symbol(sym)
                if res == "executed":
                    executed += 1
                elif res == "skipped":
                    skipped += 1
                elif res == "blocked":
                    blocked += 1
            except Exception as e:
                errors += 1
                log.error("[ERROR] %s failed: %s", sym, str(e))
                traceback.print_exc()

        log.info(
            "[SUMMARY] Executed=%d | Skipped=%d | Blocked=%d | Errors=%d",
            executed, skipped, blocked, errors
        )

    # --------------------------------------------------------
    # Per Symbol Execution
    # --------------------------------------------------------
    def _run_symbol(self, sym: str) -> str:
        """Handles one symbol end-to-end."""
        feats = compute_features(sym, ENV)

        # Handle missing or skipped features
        if not feats.get("ok"):
            reason = feats.get("reason", "").lower()

            if "outside trading window" in reason:
                log.info("[SKIP] %s outside trading session window", sym)
                return "skipped"

            elif "no bars" in reason:
                log.warning("[DATA] %s no bars available", sym)
                return "skipped"

            else:
                log.warning("[DATA] %s feature extraction failed or no data (%s)", sym, reason)
                return "skipped"

        # Decision logic
        dec = decide(feats, ENV)
        if not dec.get("accepted"):
            log.info("[SKIP] %s no trade (conf=%.2f, reason=%s)", sym, dec.get("confidence_adj", 0.0), dec.get("why", []))
            return "skipped"

        # Execution phase
        log.info(
            "[SIGNAL] %s %s (conf=%.2f, lots=%.2f, sl=%.1f, tp=%.1f)",
            sym,
            dec.get("side"),
            dec.get("confidence_adj"),
            0.0,  # placeholder (executor recalculates)
            dec.get("sl", 0.0),
            dec.get("tp", 0.0),
        )

        result = execute_trade(sym, dec, feats)

        if not result.get("ok"):
            reason = result.get("reason", "unknown")
            if "blocked" in reason or "guard" in reason:
                log.info("[BLOCKED] %s blocked by guardrails.", sym)
                return "blocked"
            log.warning("[FAILED] %s %s -> %s", sym, dec.get("side"), reason)
            return "skipped"

        log.info("[EXECUTED] %s %s successfully", sym, dec.get("side"))
        return "executed"
