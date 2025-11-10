# fx_main_v46.py
# ============================================================
# Agentic Trader v4.6 FX Main Entry (auto-loads AGENT_SYMBOLS from .env)
# ============================================================

from __future__ import annotations
import argparse
from fx_v46.fx_agent_v46 import FxAgentV46
from fx_v46.app.fx_env_v46 import ENV
from fx_v46.util.logger import setup_logger


def main():
    # ------------------------------
    # Argument parsing
    # ------------------------------
    p = argparse.ArgumentParser(description="Agentic Trader FX v4.6 runner")
    p.add_argument(
        "--symbols",
        default="",
        help="Comma-separated logical symbols (overrides AGENT_SYMBOLS in .env)",
    )
    p.add_argument("--interval", type=int, default=60, help="Loop interval (seconds)")
    p.add_argument("--loop", action="store_true", help="Enable continuous loop mode")
    p.add_argument(
        "--loglevel",
        type=str,
        default="INFO",
        help="Logging level (DEBUG, INFO, WARNING, ERROR)",
    )
    args = p.parse_args()

    # ------------------------------
    # Logging setup (keeps your util.logger)
    # ------------------------------
    log = setup_logger("fx_main_v46", level=args.loglevel.upper())

    # ------------------------------
    # Symbol resolution
    # ------------------------------
    if args.symbols:
        syms = [s.strip() for s in args.symbols.split(",") if s.strip()]
    else:
        env_syms = ENV.get("AGENT_SYMBOLS", "")
        syms = [s.strip() for s in env_syms.split(",") if s.strip()]

    log.info("Starting FX Agent v4.6")
    log.info("Symbols        : %s", ", ".join(syms))
    log.info("Interval       : %d sec", args.interval)
    log.info("Loop Mode      : %s", args.loop)
    log.info("Log Level      : %s", args.loglevel)

    # ------------------------------
    # Agent initialization
    # ------------------------------
    agent = FxAgentV46(symbols=syms)

    # ------------------------------
    # Execution mode
    # ------------------------------
    if args.loop:
        log.info("[LOOP] Continuous run mode enabled.")
        agent.run_forever(args.interval)
    else:
        log.info("[ONCE] Single cycle run.")
        agent.run_once()

    log.info("[END] FX Agent v4.6 completed.")


if __name__ == "__main__":
    main()
