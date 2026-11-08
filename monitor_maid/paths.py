"""Filesystem locations for the database, log, and process output."""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_DIR_NAME = "MonitorMaid"


def data_dir() -> Path:
    override = os.environ.get("MONITOR_MAID_DATA_DIR", "").strip()
    if override:
        path = Path(override).expanduser()
    elif sys.platform == "darwin":
        path = Path.home() / "Library" / "Application Support" / APP_DIR_NAME
    elif os.name == "nt":
        root = os.environ.get("APPDATA", str(Path.home()))
        path = Path(root) / APP_DIR_NAME
    else:
        path = Path.home() / ".local" / "share" / "monitor-maid"
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
    return path


def db_path() -> Path:
    return data_dir() / "monitor-maid.db"


def log_path() -> Path:
    return data_dir() / "monitor-maid.log"


def process_log_dir() -> Path:
    path = data_dir() / "process-logs"
    path.mkdir(parents=True, exist_ok=True)
    return path
