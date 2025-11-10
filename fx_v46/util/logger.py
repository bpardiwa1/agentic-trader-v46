"""
Agentic Trader FX v4 — Unified Logging Utility
----------------------------------------------
Provides color-coded, structured, and daily-rotating log setup.
"""

import logging
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

def setup_logger(name: str, log_dir: str = "logs", level: str = "INFO") -> logging.Logger:
    """Set up a structured logger with daily rotation."""
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    logfile = log_dir / f"{name}.log"

    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Clean up old handlers (avoid duplicates)
    if logger.hasHandlers():
        logger.handlers.clear()

    # --- Formatter ---
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-7s | %(name)-10s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # --- File handler (daily rotation) ---
    file_handler = TimedRotatingFileHandler(logfile, when="midnight", backupCount=7, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # --- Console handler (colored output) ---
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    logger.addHandler(console)

    return logger
