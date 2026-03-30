"""
Agentic Trader v4.6 — Unified Logging Utility
---------------------------------------------
Provides color-coded, structured, and daily-rotating log setup.
"""

import logging
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path


def setup_logger(name: str, log_dir: str = "logs", level: str = "INFO") -> logging.Logger:
    """
    Set up a structured logger with daily rotation.

    Args:
        name:      Logical logger / file base name (without ".log").
        log_dir:   Directory where the log file will be stored.
        level:     Log level string ("DEBUG", "INFO", "WARNING", etc.).

    Returns:
        logging.Logger instance configured with a file + console handler.
    """
    log_dir_path = Path(log_dir)
    log_dir_path.mkdir(parents=True, exist_ok=True)
    logfile = log_dir_path / f"{name}.log"

    logger = logging.getLogger(name)
    # Avoid duplicate handlers if called multiple times for the same logger
    if logger.handlers:
        logger.setLevel(getattr(logging, level.upper(), logging.INFO))
        return logger

    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # File handler with daily rotation, keeping last 7 days
    file_handler = TimedRotatingFileHandler(
        logfile,
        when="midnight",
        backupCount=7,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # Console handler (stdout)
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    logger.addHandler(console)

    return logger
