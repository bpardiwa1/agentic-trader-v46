# ============================================================
# Agentic Trader XAU v4.6 — Environment-Driven Agent Core
# ============================================================

from __future__ import annotations
import time
import MetaTrader5 as mt5
from xau_v46.app.xau_env_v46 import ENV
from xau_v46.util.logger import setup_logger
from xau_v46.util.xau_mt5_bars import get_bars
from xau_v46.xau_features_v46 import compute_features
from xau_v46.xau_decider_v46 import decide_signal
from xau_v46.xau_executor_v46 import execute_trade
from xau_v46.trust.xau_trust_engine_v46 import update_trust

log = setup_logger("xau_agent_v46", level=ENV.get("LOG_LEVEL", "INFO").upper())


def _symbols_from_env() -> list[str]:
    """Read active trading symbols from ENV"""
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

        if not mt5.initialize():
            raise RuntimeError("MT5 initialization failed")

        log.info("[MT5] Connected build=%s", mt5.version())
        log.info("[INIT] Timeframe=%s | Bars=%d | Lots=[%.2f–%.2f]",
                 self.timeframe, self.bar_history, self.min_lots, self.max_lots)

    # ------------------------------------------------------------
    # Run one symbol (fetch → compute → decide → execute)
    # ------------------------------------------------------------
    def _run_symbol(self, sym: str, summary: dict):
        try:
            bars = get_bars(sym, timeframe=self.timeframe, limit=self.bar_history)
            if len(bars) < 50:
                log.warning("[DATA] %s insufficient bars (%d < 50)", sym, len(bars))
                summary["errors"] += 1
                return

            feats = compute_features(sym)
            if not feats:
                log.warning("[DATA] %s feature extraction failed", sym)
                summary["errors"] += 1
                return

            decision = decide_signal(feats, ENV)
            preview = decision.get("preview", {})
            side = preview.get("side", "")

            # Skip neutral or missing signals
            if not side:
                log.info("[SKIP] %s no trade (conf=%.2f, reason=%s)",
                         sym,
                         preview.get("confidence", 0.0),
                         preview.get("why", []))
                summary["skipped"] += 1
                return

            # Execute
            res = execute_trade(
                symbol=sym,
                side=side,
                base_lot=float(preview.get("base_lot", self.min_lots)),
                sl_points=float(preview.get("sl_points", 300)),
                tp_points=float(preview.get("tp_points", 600)),
                confidence=float(preview.get("confidence", 0.5)),
                atr_pct=float(feats.get("atr_pct", 0.0)),
            )

            if res.get("ok"):
                update_trust(sym, True)
                summary["executed"] += 1
            elif res.get("blocked"):
                log.info("[BLOCKED] %s blocked by guardrails.", sym)
                summary["blocked"] += 1
            else:
                log.warning("[FAILED] %s %s -> %s", sym, side, res)
                update_trust(sym, False)
                summary["errors"] += 1

        except Exception as e:
            log.exception("[ERROR] %s failed: %s", sym, e)
            summary["errors"] += 1

    # ------------------------------------------------------------
    # Run one batch of all configured symbols
    # ------------------------------------------------------------
    def run_once(self):
        summary = {"executed": 0, "skipped": 0, "blocked": 0, "errors": 0}
        for sym in self.symbols:
            self._run_symbol(sym, summary)
        log.info("[SUMMARY] Executed=%d | Skipped=%d | Blocked=%d | Errors=%d",
                 summary["executed"], summary["skipped"], summary["blocked"], summary["errors"])

    # ------------------------------------------------------------
    # Continuous loop
    # ------------------------------------------------------------
    def run_forever(self, interval: int | None = None):
        loop_interval = interval or self.loop_delay
        log.info("[LOOP] Starting continuous loop (interval=%ds)", loop_interval)
        while True:
            self.run_once()
            time.sleep(loop_interval)
