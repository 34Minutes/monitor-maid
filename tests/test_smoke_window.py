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


def test_chat_is_on_the_left_and_history_and_input_are_on_the_right(tmp_path, qapp):
    db = Database(tmp_path / "maid.db")
    db.migrate()
    window = MainWindow(db)
    window.resize(1240, 800)
    window.show()
    qapp.processEvents()

    def left_edge(widget) -> int:
        return widget.mapTo(window, widget.rect().topLeft()).x()

    def top_edge(widget) -> int:
        return widget.mapTo(window, widget.rect().topLeft()).y()

    assert left_edge(window.transcript) < left_edge(window.history)
    assert abs(left_edge(window.composer) - left_edge(window.history)) < 40
    assert top_edge(window.composer) > top_edge(window.history)
    assert window.composer.width() < window.width() * 0.7
    # The hidden Grok column must not reserve the space to the right of the input.
    assert window.composer.local_edit.width() > window.composer.width() * 0.6
    window.monitor_button.setChecked(True)
    qapp.processEvents()
    assert window.composer.grok_edit.isVisible()
    total = max(window.composer.input_split.width(), 2)
    window.composer.input_split.setSizes([int(total * 0.75), int(total * 0.25)])
    qapp.processEvents()
    assert window.composer.local_edit.width() > window.composer.grok_edit.width()
    window.close()
    for _ in range(30):
        qapp.processEvents()
        if window._quit_done and not window.isVisible():
            break
    db.close()
