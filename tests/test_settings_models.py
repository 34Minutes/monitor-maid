"""Installed-model selection for the Settings combo."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6")

from PyQt6.QtWidgets import QApplication

from monitor_maid.db import Database
from monitor_maid.ui.dialogs import SettingsDialog, select_installed_model


def test_missing_default_snaps_to_installed_model():
    chosen, names, note = select_installed_model("llama3.2", ["arete-27b:latest"])
    assert chosen == "arete-27b:latest"
    assert names == ["arete-27b:latest"]
    assert "llama3.2 is not installed" in note
    assert "Save" in note


def test_bare_name_matches_latest_tag():
    chosen, names, note = select_installed_model("arete-27b", ["arete-27b:latest"])
    assert chosen == "arete-27b:latest"
    assert names == ["arete-27b:latest"]
    assert note.startswith("Installed:")


def test_empty_list_keeps_typed_name():
    chosen, names, note = select_installed_model("arete-27b", [])
    assert chosen == "arete-27b"
    assert names == []
    assert "no installed models" in note


@pytest.fixture
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


def test_refresh_result_replaces_combo_text(tmp_path, qapp):
    db = Database(tmp_path / "maid.db")
    db.migrate()
    dialog = SettingsDialog(db)
    dialog._listed = True
    dialog.show()
    qapp.processEvents()
    dialog._show_probe(dialog._probe_gen, True, "ollama-list", "MODELS\narete-27b:latest")
    assert dialog.ollama_model.count() == 1
    assert dialog.ollama_model.currentText() == "arete-27b:latest"
    assert "llama3.2 is not installed" in dialog.model_note.text()
    dialog.close()
    db.close()
