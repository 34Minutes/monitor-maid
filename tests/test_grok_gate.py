"""Grok controls stay off without an API key, and Ollama downtime is explained."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6")

from PyQt6.QtWidgets import QApplication

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


def test_no_api_key_disables_grok_controls(tmp_path, qapp, monkeypatch):
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    db = Database(tmp_path / "maid.db")
    db.migrate()
    window = MainWindow(db)
    window.show()
    qapp.processEvents()
    assert window.composer.grok_hint.isVisible()
    assert "API key" in window.composer.grok_hint.text()
    assert not window.monitor_button.isEnabled()
    assert not window.composer.grok_button.isEnabled()
    assert not window.composer.both_button.isEnabled()
    assert not window.history.ask_grok.isEnabled()
    assert not window.history.verify.isEnabled()
    assert not window.relay_mode.isEnabled()
    assert window.history.ask_local.isEnabled()
    assert window.composer.local_button.isEnabled()
    window._show_ollama("offline")
    assert window.ollama_hint.isVisible()
    assert "ollama serve" in window.ollama_hint.text()
    dialog = SettingsDialog(db, window)
    assert not dialog.test_xai.isEnabled()
    assert not dialog.review_first.isEnabled()
    dialog.xai_key.setText("test-key")
    assert dialog.test_xai.isEnabled()
    assert dialog.review_first.isEnabled()
    dialog.close()
    _close(window, qapp)
    db.close()


def test_api_key_enables_grok_controls(tmp_path, qapp, monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "test-key")
    db = Database(tmp_path / "maid.db")
    db.migrate()
    window = MainWindow(db)
    window.show()
    qapp.processEvents()
    assert window.monitor_button.isEnabled()
    assert window.history.ask_grok.isEnabled()
    assert window.history.verify.isEnabled()
    assert "turn on Monitor mode" in window.composer.grok_hint.text()
    _close(window, qapp)
    db.close()
