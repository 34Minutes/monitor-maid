"""File and stderr logging. Chat text and API keys are not written here."""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


def setup_logging(path: Path) -> logging.Logger:
    logger = logging.getLogger("monitor_maid")
    logger.setLevel(logging.INFO)
    if logger.handlers:
        return logger
    path.parent.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        path, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    logger.addHandler(file_handler)
    # A windowed bundle has no console; stderr is None and a stream handler would crash.
    if sys.stderr is not None:
        stream = logging.StreamHandler()
        stream.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        logger.addHandler(stream)
    logger.propagate = False
    return logger
