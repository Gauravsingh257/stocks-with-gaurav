"""
utils/logging_config.py — Centralized logging configuration.

Provides structured logging with rotating file handlers, console output,
and trade-specific log channels.

Usage:
    from utils.logging_config import setup_logging
    setup_logging()
    # Then use standard logging:
    import logging
    logger = logging.getLogger("engine")
    logger.info("Engine started")
"""

import logging
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler, TimedRotatingFileHandler
from pathlib import Path

LOG_DIR = Path(__file__).parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)


def setup_logging(level: str = "INFO", log_dir: Path | None = None):
    """
    Configure logging for the entire application.

    Creates separate log files for:
      - app.log:       General application log (rotating, 10MB)
      - trading.log:   Trade entries/exits/signals (daily rotation)
      - errors.log:    ERROR+ only (rotating, 5MB)
      - debug.log:     DEBUG level (rotating, 20MB, only if level=DEBUG)
    """
    log_path = log_dir or LOG_DIR
    log_path.mkdir(exist_ok=True)

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Clear existing handlers to avoid duplicates on re-init
    root.handlers.clear()

    formatter = logging.Formatter(
        "%(asctime)s  %(levelname)-8s  [%(name)s]  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    compact_formatter = logging.Formatter(
        "%(asctime)s  %(levelname)s  %(message)s",
        datefmt="%H:%M:%S",
    )

    # Console handler (compact)
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(getattr(logging, level.upper(), logging.INFO))
    console.setFormatter(compact_formatter)
    root.addHandler(console)

    # Main app log (rotating 10MB, keep 5)
    app_handler = RotatingFileHandler(
        log_path / "app.log", maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8",
    )
    app_handler.setLevel(logging.INFO)
    app_handler.setFormatter(formatter)
    root.addHandler(app_handler)

    # Trading log (daily rotation, keep 30 days)
    trade_handler = TimedRotatingFileHandler(
        log_path / "trading.log", when="midnight", backupCount=30, encoding="utf-8",
    )
    trade_handler.setLevel(logging.INFO)
    trade_handler.setFormatter(formatter)
    trade_logger = logging.getLogger("trading")
    trade_logger.addHandler(trade_handler)
    trade_logger.propagate = False

    # Error log (5MB, keep 3)
    error_handler = RotatingFileHandler(
        log_path / "errors.log", maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8",
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(formatter)
    root.addHandler(error_handler)

    # Debug log (only if debug level)
    if level.upper() == "DEBUG":
        debug_handler = RotatingFileHandler(
            log_path / "debug.log", maxBytes=20 * 1024 * 1024, backupCount=2, encoding="utf-8",
        )
        debug_handler.setLevel(logging.DEBUG)
        debug_handler.setFormatter(formatter)
        root.addHandler(debug_handler)

    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("websockets").setLevel(logging.WARNING)

    root.info("Logging initialized — level=%s, log_dir=%s", level, log_path)
