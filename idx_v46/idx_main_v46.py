# ============================================================
# Agentic Trader idx_v46 — Entry Point
# ============================================================

from __future__ import annotations
import argparse

from idx_v46.app.idx_env_v46 import ENV
from idx_v46.idx_agent_v46 import IdxAgentV46, _symbols_from_env
from idx_v46.util.idx_logger_v46 import setup_logger

def main():
    p = argparse.ArgumentParser(description="Agentic Trader idx_v46 runner")
    p.add_argument("--symbols", help="Comma-separated symbols (override)")
    p.add_argument("--interval", type=int, default=int(ENV.get("LOOP_INTERVAL", 60)))
    p.add_argument("--loop", action="store_true")
    p.add_argument("--loglevel", type=str, default=str(ENV.get("LOG_LEVEL", "INFO")))
    args = p.parse_args()

    log = setup_logger("idx_main_v46", level=args.loglevel)

    syms = [s.strip() for s in (args.symbols or ",".join(_symbols_from_env())).split(",") if s.strip()]
    timeframe = ENV.get("IDX_TIMEFRAME", "M15")

    log.info("Launching idx_v46 | symbols=%s | tf=%s | loop=%s | interval=%ds",
             ", ".join(syms), timeframe, args.loop, args.interval)

    agent = IdxAgentV46(symbols=syms, timeframe=timeframe)
    try:
        if args.loop:
            agent.run_forever(args.interval)
        else:
            agent.run_once()
    except KeyboardInterrupt:
        log.warning("[INTERRUPT] stopping loop")

if __name__ == "__main__":
    main()
