"""Application entry point."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PyQt6.QtWidgets import QApplication

from monitor_maid.db import Database
from monitor_maid.logging_setup import setup_logging
from monitor_maid.paths import db_path, log_path
from monitor_maid.ui.main_window import MainWindow
from monitor_maid.ui.theme import apply_theme


def main() -> None:
    # Finder launches a .app with the working directory set to "/".
    if getattr(sys, "frozen", False):
        os.chdir(Path.home())
    setup_logging(log_path())
    db = Database(db_path())
    db.migrate()
    app = QApplication(sys.argv)
    app.setApplicationName("Monitor Maid")
    app.setOrganizationName("MonitorMaid")
    apply_theme(app)
    window = MainWindow(db)
    window.show()
    code = app.exec()
    db.close()
    raise SystemExit(code)
