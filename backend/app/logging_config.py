from __future__ import annotations

import logging
from pathlib import Path

from app.settings import BACKEND_DIR


LOG_DIR = BACKEND_DIR / "logs"
LOG_FILE = LOG_DIR / "trading.log"


def configure_logging(level: str = "INFO") -> Path:
    """Configure console and file logging for trading runs."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    root_logger.addHandler(stream_handler)

    file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    return LOG_FILE
