# ============================================================
# Agentic Trader idx_v46 — Self Test (no trading)
# ============================================================

from __future__ import annotations
from idx_v46.app.idx_env_v46 import ENV
from idx_v46.util.idx_logger_v46 import setup_logger
from idx_v46.util.idx_mt5_bars_v46 import get_bars
from idx_v46.idx_features_v46 import compute_features
from idx_v46.idx_decider_v46 import decide_signal
from idx_v46.util.idx_indicators_v46 import ema, rsi, atr


log = setup_logger("idx_selftest_v46", level=str(ENV.get("LOG_LEVEL", "INFO")))

def main():
    symbols = [s.strip() for s in str(ENV.get("AGENT_SYMBOLS", "NAS100.s,UK100.s,HK50.s")).split(",") if s.strip()]
    log.info("[SELFTEST] symbols=%s", ", ".join(symbols))
    for sym in symbols:
        try:
            bars = get_bars(sym, timeframe=str(ENV.get("IDX_TIMEFRAME", "M15")), limit=int(ENV.get("IDX_HISTORY_BARS", 240)))
            log.info("[BARS] %s -> %s rows", sym, len(bars))
            feats = compute_features(sym)
            if not feats:
                log.info("[FEAT] %s -> None", sym); continue
            log.info("[FEAT] %s -> %s", sym, {k: feats[k] for k in ("price","ema_fast","ema_slow","rsi","atr_pct","adj_conf")})
            dec = decide_signal(feats)
            log.info("[DECIDE] %s -> %s", sym, dec.get("preview", {}))
        except Exception as e:
            log.exception("[SELFTEST] %s failed: %s", sym, e)

if __name__ == "__main__":
    main()

log.info('[SELFTEST] Indicator import OK — ema, rsi, atr ready')
