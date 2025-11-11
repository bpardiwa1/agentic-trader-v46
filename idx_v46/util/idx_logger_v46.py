#Agentic Trader IDX v4.6 — Unified Logging Utility
#-------------------------------------------------
#Provides color-coded, structured, and daily-rotating log setup.

import logging
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

def setup_logger(name: str, log_dir: str = "logs", level: str = "INFO") -> logging.Logger:
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    logfile = log_dir / f"{name}.log"

    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    if logger.hasHandlers():
        logger.handlers.clear()

    fmt = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-7s | %(name)-14s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    fh = TimedRotatingFileHandler(logfile, when="midnight", backupCount=7, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    return logger
