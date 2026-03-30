"""
Agentic Trader v4.6 — Unified IDX Logging Utility
-------------------------------------------------
Provides structured, safe, daily-rotating logging for the IDX module.
Mirrors the FX & XAU logger implementations to ensure consistent behavior.
"""

import logging
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path


def setup_logger(name: str, log_dir: str = "logs", level: str = "INFO") -> logging.Logger:
    """
    Set up a structured, rotating logger for IDX.

    Args:
        name (str): Logger + log file base name (without .log)
        log_dir (str): Directory where the log will be written
        level (str): Log level ("DEBUG", "INFO", etc.)

    Returns:
        logging.Logger
    """

    # 1. Ensure directory exists
    log_dir_path = Path(log_dir)
    log_dir_path.mkdir(parents=True, exist_ok=True)

    # 2. Final log file path
    logfile = log_dir_path / f"{name}.log"

    # 3. Reuse existing logger if already configured
    logger = logging.getLogger(name)
    if logger.handlers:
        logger.setLevel(getattr(logging, level.upper(), logging.INFO))
        return logger

    # 4. Set level
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # 5. Common formatter
    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 6. File handler (daily rotation)
    file_handler = TimedRotatingFileHandler(
        logfile,
        when="midnight",
        backupCount=7,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # 7. Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    return logger
