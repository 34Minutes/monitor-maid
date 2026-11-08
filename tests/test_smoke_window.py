"""Offscreen startup: an empty session closes without calling a model."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6")

from PyQt6.QtWidgets import QApplication

from monitor_maid.db import Database
from monitor_maid.ui.main_window import MainWindow


@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


def test_empty_session_shuts_down_cleanly(tmp_path, qapp):
    db = Database(tmp_path / "maid.db")
    db.migrate()
    window = MainWindow(db)
    window.show()
    qapp.processEvents()
    assert window.session_combo.count() == 1
    assert window.composer.local_edit.objectName() == "localInput"
    assert window.orchestrator.current() is not None
    assert window.orchestrator.current().execution_mode == "manual"
    assert window.composer.grok_edit.isHidden()
    window.monitor_button.setChecked(True)
    qapp.processEvents()
    assert window.composer.grok_edit.isVisible()
    assert window.orchestrator.current().monitor_mode is True
    window.close()
    for _ in range(30):
        qapp.processEvents()
        if window._quit_done and not window.isVisible():
            break
    assert window._quit_done
    assert db.get_setting("app_running") == "0"
    # A session with no turns is removed instead of storing an empty summary.
    assert db.list_sessions() == []
    db.close()
