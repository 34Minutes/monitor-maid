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
    assert window.session_combo.count() == 0
    assert window.orchestrator.current() is None
    assert window.new_button.text() == "New Local"
    assert window.new_grok_button.text() == "New Grok"
    assert window.reopen_button.text() == "Reopen Local"
    assert window.reopen_grok_button.text() == "Reopen Grok"
    assert not hasattr(window, "monitor_button")
    assert window.composer.local_button.text() == "Ask Local"
    assert window.composer.ask_grok_button.text() == "Ask Grok"
    assert window.composer.interact_button.text() == "Interact with Local"
    assert not hasattr(window.history, "verify")
    assert not hasattr(window.history, "interact")
    assert window.composer.local_edit.objectName() == "localInput"
    assert window.composer.grok_edit.isVisible()
    assert not window.composer.local_button.isEnabled()
    window.orchestrator.new_session()
    qapp.processEvents()
    assert window.session_combo.count() == 1
    assert window.orchestrator.current() is not None
    assert window.orchestrator.current().execution_mode == "manual"
    assert window.orchestrator.current().grok_relay_limit == 4
    assert window.composer.grok_edit.isVisible()
    assert window.composer.local_button.text() == "Ask Local"
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
    window.orchestrator.new_session()
    qapp.processEvents()

    def left_edge(widget) -> int:
        return widget.mapTo(window, widget.rect().topLeft()).x()

    def top_edge(widget) -> int:
        return widget.mapTo(window, widget.rect().topLeft()).y()

    assert left_edge(window.transcript) < left_edge(window.history)
    assert abs(left_edge(window.composer) - left_edge(window.history)) < 40
    assert top_edge(window.composer) > top_edge(window.history)
    assert window.composer.width() < window.width() * 0.7
    assert window.composer.grok_edit.isVisible()
    total = max(window.composer.input_split.width(), 2)
    window.composer.input_split.setSizes([int(total * 0.75), int(total * 0.25)])
    qapp.processEvents()
    assert window.composer.local_edit.width() > window.composer.grok_edit.width()
    assert left_edge(window.composer.ask_grok_button) > left_edge(window.composer.local_button)
    assert left_edge(window.composer.interact_button) > left_edge(window.composer.local_edit)
    assert top_edge(window.composer.ask_grok_button) > top_edge(window.composer.grok_edit)
    assert top_edge(window.composer.enter_sends) > top_edge(window.composer.local_button)
    assert abs(top_edge(window.composer.local_edit) - top_edge(window.composer.grok_edit)) <= 2
    assert abs(window.composer.local_edit.height() - window.composer.grok_edit.height()) <= 2
    assert left_edge(window.new_grok_button) > left_edge(window.new_button)
    assert left_edge(window.reopen_grok_button) > left_edge(window.reopen_button)
    window.close()
    for _ in range(30):
        qapp.processEvents()
        if window._quit_done and not window.isVisible():
            break
    db.close()
