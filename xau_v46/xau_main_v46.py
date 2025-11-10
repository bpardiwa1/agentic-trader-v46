# ============================================================
# Agentic Trader XAU v4.6 — Main Entry Point
# ------------------------------------------------------------
# Purpose:
#   • Reads environment (xau_v46.env)
#   • Launches XAUUSD agent with dynamic symbol/timeframe setup
#   • Supports both CLI overrides and .env defaults
# ============================================================

from __future__ import annotations
import argparse
import os
from xau_v46.xau_agent_v46 import XauAgentV46
from xau_v46.app.xau_env_v46 import ENV
from xau_v46.util.logger import setup_logger
from core.mt5_connect_v46 import ensure_mt5_initialized, safe_shutdown


def main():
    # ------------------------------
    # Argument parsing
    # ------------------------------
    p = argparse.ArgumentParser(description="Agentic Trader XAU v4.6 runner")
    p.add_argument("--symbols", help="Comma-separated symbols (optional override)")
    p.add_argument("--interval", type=int, default=int(ENV.get("LOOP_INTERVAL", 60)))
    p.add_argument("--loop", action="store_true", help="Enable continuous mode")
    p.add_argument("--loglevel", type=str, default=ENV.get("LOG_LEVEL", "INFO"))
    args = p.parse_args()

    # ------------------------------
    # Logging setup
    # ------------------------------
    log = setup_logger("xau_main_v46", level=args.loglevel.upper())

    # ------------------------------
    # Resolve symbols from ENV or CLI
    # ------------------------------
    env_syms = (
        ENV.get("AGENT_SYMBOLS")
        or ENV.get("XAU_AGENT_SYMBOLS")
        or "XAUUSD-ECNc"
    )
    syms = [s.strip() for s in (args.symbols or env_syms).split(",") if s.strip()]

    # ------------------------------
    # Resolve timeframe from ENV
    # ------------------------------
    timeframe = ENV.get("XAU_TIMEFRAME", "M15")

    log.info("Launching Agentic Trader XAU v4.6")
    log.info("Symbols     : %s", ", ".join(syms))
    log.info("Interval    : %ds", args.interval)
    log.info("Loop Mode   : %s", args.loop)
    log.info("Log Level   : %s", args.loglevel)
    log.info("Timeframe   : %s", timeframe)

    # ------------------------------
    # Initialize MT5 connection
    # ------------------------------
    if not ensure_mt5_initialized(ENV):
        raise RuntimeError("MT5 initialization failed")

    # ------------------------------
    # Launch agent
    # ------------------------------
    agent = XauAgentV46(symbols=syms, timeframe=timeframe)

    try:
        if args.loop:
            log.info("[LOOP] Continuous run mode enabled.")
            agent.run_forever(args.interval)
        else:
            log.info("[ONCE] Single run mode.")
            agent.run_once()
    except KeyboardInterrupt:
        log.warning("[INTERRUPT] Stopping loop gracefully...")
    finally:
        safe_shutdown()


if __name__ == "__main__":
    main()
