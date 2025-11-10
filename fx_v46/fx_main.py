"""
Agentic Trader FX v4 — Main Entrypoint
--------------------------------------
Runs the FX trading agent either once or in continuous loop mode.

Usage:
    python -m fx_v4.fx_main
    python -m fx_v4.fx_main --loop --interval 30
"""

# --- Load .env before any module reads env vars ---
from dotenv import load_dotenv
import pathlib
# import os


env_path = pathlib.Path(__file__).resolve().parent / "app" / "fx_v4.env"
print(f"[DEBUG] Looking for env file at: {env_path}")
if env_path.exists():
    load_dotenv(env_path)
    print(f"[FX_MAIN] Environment loaded from {env_path}")
else:
    print(f"[FX_MAIN] No environment file found at {env_path}")

# Now import the rest
from fx_v4.fx_agent import FxAgent  # noqa: E402
from fx_v4.util.logger import setup_logger # noqa: E402
import argparse # noqa: E402
import sys # noqa: E402

# import logging



# log = setup_logger("fx_main", level="INFO")

def main():
    parser = argparse.ArgumentParser(description="Run Agentic Trader FX v4 module")
    parser.add_argument("--loop", action="store_true", help="Run continuously (infinite loop)")
    parser.add_argument("--interval", type=int, default=30, help="Loop interval in seconds (default=30)")
    parser.add_argument("--loglevel", default="INFO", help="Logging level: DEBUG, INFO, WARNING, ERROR")

    args = parser.parse_args()

    # --- Configure logging ---


    # --- Configure logging ---
    log = setup_logger("fx_main", level=args.loglevel)
    log.info("▶ Starting Agentic Trader FX v4 (loop=%s, interval=%ss)", args.loop, args.interval)

    # --- Initialize and run agent ---
    try:
        agent = FxAgent()
        if args.loop:
            log.info("Running continuous mode (Ctrl+C to stop)...")
            agent.run_forever(args.interval)
        else:
            log.info("Running single cycle...")
            agent.run_once()
        log.info("FX Agent finished.")
    except KeyboardInterrupt:
        log.warning("FX Agent stopped manually by user.")
        sys.exit(0)
    except Exception as e:
        log.exception("Fatal error in fx_main: %s", e)
        sys.exit(1)



if __name__ == "__main__":
    main()
