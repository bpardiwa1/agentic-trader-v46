# ============================================================
# NAS100 Scalper v1 — Agent (independent bot)
# ============================================================

from __future__ import annotations

import time
from datetime import datetime
from zoneinfo import ZoneInfo

import MetaTrader5 as mt5  # type: ignore

from nas100_scalp_v1.app.nas100_env_v1 import ENV
from nas100_scalp_v1.util.nas100_logger_v1 import setup_logger
from nas100_scalp_v1.util.nas100_event_sink_v1 import emit_event
from nas100_scalp_v1.nas100_features_v1 import compute_features
from nas100_scalp_v1.nas100_decider_v1 import decide
from nas100_scalp_v1.nas100_executor_v1 import execute_trade

MY_TZ = ZoneInfo("Asia/Kuala_Lumpur")


def _day_key() -> str:
    return datetime.now(MY_TZ).strftime("%Y-%m-%d")


def main() -> None:
    bot = str(ENV.get("BOT_NAME", "nas100_scalp_v1"))
    log_dir = "logs/nas100_scalp_v1"
    lvl = str(ENV.get("IDX_LOG_LEVEL", "INFO")).upper()
    log = setup_logger(f"{bot}_{datetime.now():%Y-%m-%d}", log_dir=log_dir, level=lvl)

    symbols = [s.strip() for s in str(ENV.get("AGENT_SYMBOLS", "NAS100.s")).split(",") if s.strip()]
    poll_sec = int(ENV.get("SCALP_POLL_SEC", 5))

    if not mt5.initialize():
        log.error("[MT5] initialize failed")
        return

    log.info("[START] %s symbols=%s poll=%ss", bot, symbols, poll_sec)
    emit_event("BOOT", {"symbols": symbols}, log=log, asset="INDEX")

    state = {
        "day": _day_key(),
        "trades_today": 0,
        "last_entry_bar_idx": -10_000,
        "bar_idx": 0,
        "last_bar_time_m1": "",
    }

    while True:
        try:
            # reset daily counters
            dk = _day_key()
            if state["day"] != dk:
                state["day"] = dk
                state["trades_today"] = 0
                state["last_entry_bar_idx"] = -10_000
                state["bar_idx"] = 0
                state["last_bar_time_m1"] = ""
                emit_event("NEW_DAY", {"day": dk}, log=log, asset="INDEX")

            for sym in symbols:
                feats = compute_features(sym)
                if not feats:
                    emit_event("SKIP", {"symbol": sym, "why": ["no_features"]}, log=log, asset="INDEX")
                    continue

                bt = str(feats.get("bar_time_m1", "") or "")
                if bt and bt != state.get("last_bar_time_m1", ""):
                    state["bar_idx"] = int(state.get("bar_idx", 0)) + 1
                    state["last_bar_time_m1"] = bt

                decision = decide(feats, state)
                side = str(decision.get("side", "") or "")
                why = decision.get("why", [])

                emit_event(
                    "DECISION",
                    {
                        "module": "agent",
                        "symbol": sym,
                        "side": side,
                        "why": why,
                        "bias": feats.get("bias_side"),
                        "ema_gap_m1": feats.get("ema_gap_m1"),
                        "rsi_m1": feats.get("rsi_m1"),
                        "atr_pct_m1": feats.get("atr_pct_m1"),
                        "bar_idx": state.get("bar_idx", 0),
                        "trades_today": state.get("trades_today", 0),
                    },
                    log=log,
                    asset="INDEX",
                )

                if not side:
                    continue

                sl_points = float(decision.get("sl_points", ENV.get("SCALP_SL_POINTS", 80.0)))
                tp_points = float(decision.get("tp_points", ENV.get("SCALP_TP_POINTS", 120.0)))
                atr_pct = float(feats.get("atr_pct_m1", 0.0) or 0.0)

                emit_event(
                    "TRADE_START",
                    {"module": "agent", "symbol": sym, "side": side, "sl_points": sl_points, "tp_points": tp_points},
                    log=log,
                    asset="INDEX",
                )

                # confidence: keep simple for v1 (you can later add a confidence model)
                res = execute_trade(
                    symbol=sym,
                    side=side,
                    sl_points=sl_points,
                    tp_points=tp_points,
                    confidence=0.55,
                    atr_pct=atr_pct,
                    reason=why,
                )

                if res.get("ok"):
                    state["trades_today"] = int(state.get("trades_today", 0)) + 1
                    state["last_entry_bar_idx"] = int(state.get("bar_idx", 0))

            time.sleep(poll_sec)

        except Exception as e:
            log.exception("[LOOP] error: %s", e)
            emit_event("ERROR", {"error": str(e)}, log=log, asset="INDEX")
            time.sleep(max(2, poll_sec))


if __name__ == "__main__":
    main()