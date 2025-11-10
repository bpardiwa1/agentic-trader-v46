"""
Agentic Trader FX v4 — Unified Logging Utility
----------------------------------------------
Provides color-coded, structured, and daily-rotating log setup.
"""

import logging
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

import os
from datetime import datetime

def setup_logger(name: str, level: str = "INFO"):
    """Create independent file+console logger to avoid WinError32 conflicts."""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger  # already configured

    logger.setLevel(level.upper())
    formatter = logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)s | %(message)s")

    # Ensure logs directory exists
    base_dir = os.path.join(os.getcwd(), "logs")
    os.makedirs(base_dir, exist_ok=True)

    # Each module gets its own dated logfile
    log_path = os.path.join(base_dir, f"{name}_{datetime.now():%Y-%m-%d}.log")

    fh = logging.FileHandler(log_path, encoding="utf-8", delay=True)
    fh.setFormatter(formatter)
    fh.setLevel(level.upper())

    ch = logging.StreamHandler()
    ch.setFormatter(formatter)
    ch.setLevel(level.upper())

    logger.addHandler(fh)
    logger.addHandler(ch)
    logger.propagate = False

    return logger

