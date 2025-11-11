# ============================================================
# Agentic Trader idx_v46 — Agent (Diagnostics + Guardrail Summary)
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

log = setup_logger("idx_agent_v46", level=str(ENV.get("LOG_LEVEL", "INFO")))

KL = ZoneInfo("Asia/Kuala_Lumpur")


def _symbols_from_env() -> list[str]:
    s = ENV.get("AGENT_SYMBOLS", "NAS100.s,UK100.s,HK50.s")
    return [x.strip() for x in str(s).split(",") if x.strip()]


def _in_session_kl(symbol: str) -> bool:
    """Return True if symbol is within its defined KL trading window."""
    now = datetime.now(KL)
    base = symbol.upper().split(".")[0]  # e.g. NAS100 from NAS100.s

    start_s = str(ENV.get(f"IDX_TRADE_START_{base}", ENV.get("IDX_TRADE_START", "00:00")))
    end_s = str(ENV.get(f"IDX_TRADE_END_{base}", ENV.get("IDX_TRADE_END", "23:59")))
    days_csv = str(ENV.get(f"IDX_TRADE_DAYS_{base}", ENV.get("IDX_TRADE_DAYS", "1,2,3,4,5")))
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
            "[SESSION] %s active (KL %s within %s–%s days=%s)",
            symbol, now.strftime("%H:%M"), start_s, end_s, ",".join(map(str, sorted(days))),
        )
    else:
        log.info(
            "[SESSION] %s skipped (KL %s outside %s–%s)",
            symbol, now.strftime("%H:%M"), start_s, end_s,
        )

    return in_session


class IdxAgentV46:
    def __init__(self, symbols: list[str], timeframe: str | None = None):
        self.symbols = symbols
        self.timeframe = timeframe or ENV.get("IDX_TIMEFRAME", "M15")
        self.loop_delay = int(ENV.get("LOOP_INTERVAL", 60))

        if not mt5.initialize():
            raise RuntimeError("MT5 initialization failed")
        v = mt5.version()
        log.info("[MT5] Connected build=%s", v)
        log.info("[INIT] TF=%s Symbols=%s", self.timeframe, ", ".join(self.symbols))

    def _run_symbol(self, sym: str, summary: dict):
        try:
            if not _in_session_kl(sym):
                summary["skipped"] += 1
                return

            feats = compute_features(sym)
            if not feats:
                log.info("[SKIP] %s (no features)", sym)
                summary["skipped"] += 1
                return

            decision = decide_signal(feats)
            p = decision.get("preview", {})
            side = p.get("side", "")
            conf = float(p.get("confidence", 0.0))

            # === Diagnostic Logging =====================================
            ema_gap = feats.get("ema_gap", 0.0)
            atr_pct = feats.get("atr_pct", 0.0)
            conf_raw = feats.get("raw_conf", 0.0)
            adj_conf = feats.get("adj_conf", 0.0)
            sl_points = p.get("sl_points")
            tp_points = p.get("tp_points")

            lot_preview = compute_lot(sym, confidence=adj_conf, atr_pct=atr_pct)
            log.info(
                "[PREVIEW] %s side=%s conf=%.2f raw=%.2f gap=%.2f ATR%%=%.4f SL=%.1f TP=%.1f → lot=%.2f",
                sym, side or "–", adj_conf, conf_raw, ema_gap, atr_pct, sl_points, tp_points, lot_preview,
            )

            # === Decision Filters ======================================
            if not side:
                log.info("[SKIP] %s no side (why=%s)", sym, p.get("why", []))
                summary["skipped"] += 1
                return

            min_conf = float(ENV.get("IDX_MIN_CONFIDENCE", 0.55))
            if conf < min_conf:
                log.info("[SKIP] %s conf=%.2f<%.2f (why=%s)", sym, conf, min_conf, p.get("why", []))
                summary["skipped"] += 1
                return

            # === Execute Trade =========================================
            res = execute_trade(
                symbol=sym,
                side=side,
                sl_points=float(p.get("sl_points", float(ENV.get("IDX_SL_POINTS_BASE", 100.0)))),
                tp_points=float(p.get("tp_points", float(ENV.get("IDX_TP_POINTS_BASE", 200.0)))),
                confidence=conf,
                atr_pct=float(feats.get("atr_pct", 0.0)),
            )

            if res.get("ok"):
                summary["executed"] += 1
            elif res.get("blocked"):
                summary["guardrail"] += 1
                log.info("[GUARDRAIL] %s trade blocked by guardrail", sym)
            else:
                summary["errors"] += 1

        except Exception as e:
            log.exception("[ERROR] %s run failed: %s", sym, e)
            summary["errors"] += 1

    def run_once(self):
        summary = {"executed": 0, "skipped": 0, "blocked": 0, "guardrail": 0, "errors": 0}
        for s in self.symbols:
            self._run_symbol(s, summary)

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
