"""Grok controls stay off without the grok command, and Ollama downtime is explained."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6")

from PyQt6.QtWidgets import QApplication, QLabel

from monitor_maid.db import Database
from monitor_maid.ui.dialogs import SettingsDialog
from monitor_maid.ui.main_window import MainWindow


@pytest.fixture
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


def _close(window, qapp) -> None:
    window.close()
    for _ in range(30):
        qapp.processEvents()
        if window._quit_done and not window.isVisible():
            break


def _hide_grok(monkeypatch) -> None:
    monkeypatch.setattr("monitor_maid.ui.main_window.grok_available", lambda: False)
    monkeypatch.setattr("monitor_maid.ui.dialogs.grok_available", lambda: False)
    monkeypatch.setattr("monitor_maid.orchestrator.grok_available", lambda: False)


def test_no_grok_binary_disables_grok_controls(tmp_path, qapp, monkeypatch):
    _hide_grok(monkeypatch)
    db = Database(tmp_path / "maid.db")
    db.migrate()
    window = MainWindow(db)
    window.show()
    qapp.processEvents()
    window.orchestrator.new_session()
    qapp.processEvents()
    labels = [label.text() for label in window.composer.findChildren(QLabel)]
    assert not any("PATH" in text for text in labels)
    assert not window.new_grok_button.isEnabled()
    assert not window.reopen_grok_button.isEnabled()
    assert not window.composer.ask_grok_button.isEnabled()
    assert not window.composer.interact_button.isEnabled()
    assert not window.relay_mode.isEnabled()
    assert window.history.ask_local.isEnabled()
    assert window.composer.local_button.isEnabled()
    window._show_ollama("offline")
    assert window.ollama_hint.isVisible()
    assert "ollama serve" in window.ollama_hint.text()
    dialog = SettingsDialog(db, window)
    assert dialog.test_grok.isEnabled()
    assert not dialog.review_first.isEnabled()
    dialog.close()
    _close(window, qapp)
    db.close()


def test_grok_reply_returns_the_cursor_without_a_dialog(tmp_path, qapp, monkeypatch):
    monkeypatch.setattr("monitor_maid.ui.main_window.grok_available", lambda: True)
    monkeypatch.setattr("monitor_maid.orchestrator.grok_available", lambda: True)

    def fail_question(*_args, **_kwargs):
        raise AssertionError("follow-up dialog was shown")

    monkeypatch.setattr("monitor_maid.ui.main_window.QMessageBox.question", fail_question)
    db = Database(tmp_path / "maid.db")
    db.migrate()
    window = MainWindow(db)
    window.show()
    qapp.processEvents()
    window.orchestrator.new_session()
    qapp.processEvents()

    window._offer_follow_up("01a1cf51-66d3-78c2-9994-61b96654d37c")
    qapp.processEvents()
    assert window.composer.grok_edit.hasFocus()
    assert window.status_label.text() == (
        "Type a follow-up in the Grok box, then click Ask Grok."
    )
    _close(window, qapp)
    db.close()


def test_new_and_reopen_grok_dialogs_start_on_the_matching_choice(qapp):
    from monitor_maid.grok_build import GrokBuildConfig
    from monitor_maid.ui.grok_dialog import GrokSessionDialog

    config = GrokBuildConfig(cwd="/tmp", session_id="abc", bound=True)
    fresh = GrokSessionDialog(config, intent="new")
    assert fresh.windowTitle() == "New Grok"
    assert fresh.new_radio.isChecked()
    again = GrokSessionDialog(config, intent="resume")
    assert again.windowTitle() == "Reopen Grok"
    assert again.resume_radio.isChecked()
    fresh.close()
    again.close()


def test_grok_binary_enables_grok_controls(tmp_path, qapp, monkeypatch):
    monkeypatch.setattr("monitor_maid.ui.main_window.grok_available", lambda: True)
    monkeypatch.setattr("monitor_maid.orchestrator.grok_available", lambda: True)
    db = Database(tmp_path / "maid.db")
    db.migrate()
    window = MainWindow(db)
    window.show()
    qapp.processEvents()
    window.orchestrator.new_session()
    qapp.processEvents()
    assert window.new_grok_button.isEnabled()
    assert window.reopen_grok_button.isEnabled()
    assert window.composer.ask_grok_button.isEnabled()
    assert window.composer.interact_button.isEnabled()
    labels = [label.text() for label in window.composer.findChildren(QLabel)]
    assert not any("right box" in text for text in labels)
    _close(window, qapp)
    db.close()
