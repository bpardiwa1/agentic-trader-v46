# ============================================================
# Agentic Trader IDX v4.6 — Main Entry Point (Dual Mode)
# ------------------------------------------------------------
# • CLI Mode  : python -m idx_v46.idx_main_v46 --loop
# • Server Mode: python -m uvicorn idx_v46.idx_main_v46:app
# ============================================================

from __future__ import annotations
import argparse
from fastapi import FastAPI
from idx_v46.idx_agent_v46 import IdxAgentV46
from idx_v46.app.idx_env_v46 import ENV
from idx_v46.util.logger import setup_logger
from core.mt5_connect_v46 import ensure_mt5_initialized, safe_shutdown

log = setup_logger("idx_main_v46", level=ENV.get("LOG_LEVEL", "INFO"))
app = FastAPI(title="Agentic Trader IDX v4.6")

# ------------------------------------------------------------
# CLI ENTRYPOINT
# ------------------------------------------------------------
def main():
    """Main entry point for running the IDX agent."""
    p = argparse.ArgumentParser(description="Agentic Trader IDX v4.6 runner")
    p.add_argument("--loop", action="store_true", help="Enable continuous mode")
    p.add_argument("--interval", type=int, default=int(ENV.get("IDX_INTERVAL", 60)))
    args = p.parse_args()

    log.info("=" * 60)
    log.info("Launching Agentic Trader IDX v4.6")
    log.info("=" * 60)

    symbols = [s.strip() for s in ENV.get("AGENT_SYMBOLS", "NAS100.s,HK50.s,UK100.s").split(",") if s.strip()]
    timeframe = ENV.get("IDX_TIMEFRAME", "M15")

    log.info(f"Symbols     : {', '.join(symbols)}")
    log.info(f"Interval    : {args.interval}s")
    log.info(f"Loop Mode   : {args.loop}")
    log.info(f"Log Level   : {ENV.get('LOG_LEVEL', 'INFO')}")
    log.info(f"Timeframe   : {timeframe}")

    # Ensure MT5 is up and running
    if not ensure_mt5_initialized(ENV):
        raise RuntimeError("MT5 initialization failed")

    # Initialize agent (reads everything else from ENV)
    agent = IdxAgentV46()

    try:
        if args.loop:
            log.info("[LOOP] Continuous run mode enabled.")
            agent.run_forever()
        else:
            log.info("[ONCE] Single run mode.")
            agent.run_once()
    except KeyboardInterrupt:
        log.warning("[INTERRUPT] Graceful shutdown requested.")
        safe_shutdown()
    except Exception as e:
        log.error(f"[FATAL] Unhandled error: {e}")
        safe_shutdown()
        raise
    else:
        if not args.loop:
            safe_shutdown()


# ------------------------------------------------------------
# FASTAPI ENTRYPOINT
# ------------------------------------------------------------
@app.get("/")
def root():
    """Health check endpoint."""
    return {
        "status": "Agentic Trader IDX v4.6 API running",
        "symbols": ENV.get("AGENT_SYMBOLS", "NAS100.s,HK50.s,UK100.s"),
    }


@app.get("/run")
def run_once():
    """API trigger to run one cycle."""
    try:
        if not ensure_mt5_initialized(ENV):
            raise RuntimeError("MT5 initialization failed")
        agent = IdxAgentV46()
        agent.run_once()
        safe_shutdown()
        return {"status": "IDX run completed"}
    except Exception as e:
        log.error(f"[API ERROR] {e}")
        return {"status": "error", "message": str(e)}


# ------------------------------------------------------------
# ENTRYPOINT GUARD
# ------------------------------------------------------------
if __name__ == "__main__":
    main()
