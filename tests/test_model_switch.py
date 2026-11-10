"""Choosing an Ollama model unloads the others and loads that one."""

import json
import os

import httpx
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6")

from PyQt6.QtCore import QTimer
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QLabel, QMessageBox

from monitor_maid.config import Settings
from monitor_maid.db import Database
from monitor_maid.models import HistoryItem
from monitor_maid.ollama_client import (
    OllamaClient,
    OllamaError,
    apply_model_switch,
    model_needs_start,
    models_to_stop,
    same_model,
)
from monitor_maid.ui.dialogs import SeedModelDialog, model_choices
from monitor_maid.ui.main_window import MainWindow


@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


def test_same_model_ignores_the_latest_tag():
    assert same_model("llama3.2", "llama3.2:latest")
    assert not same_model("llama3.2", "llama3.2:q4")
    assert not same_model("", "llama3.2")
    assert models_to_stop(["llama3.2:latest", "arete-27b:latest"], "arete-27b") == [
        "llama3.2:latest"
    ]
    assert not model_needs_start(["arete-27b:latest"], "arete-27b")
    assert model_needs_start(["llama3.2:latest"], "arete-27b")


def test_model_choices_keep_the_session_model_when_ollama_is_quiet():
    chosen, names = model_choices("llama3.2", [])
    assert chosen == "llama3.2"
    assert names == ["llama3.2"]
    chosen, names = model_choices("llama3.2", ["arete-27b:latest", "llama3.2:latest"])
    assert chosen == "llama3.2:latest"
    assert names == ["arete-27b:latest", "llama3.2:latest"]


def test_stop_and_start_are_generate_requests():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.content:
            seen.append(json.loads(request.content))
        return httpx.Response(200, json={})

    client = OllamaClient("http://ollama.test")
    transport = httpx.MockTransport(handler)
    client._client = lambda timeout: httpx.Client(transport=transport)  # type: ignore[method-assign]
    client.stop_model("old:latest")
    client.start_model("new:latest")
    assert seen[0] == {"model": "old:latest", "keep_alive": 0, "stream": False}
    assert seen[1] == {"model": "new:latest", "stream": False}


class _FakeOllama:
    def __init__(self, running: list[str], *, ps_error: bool = False, stop_error: bool = False):
        self.running = list(running)
        self.stopped: list[str] = []
        self.started: list[str] = []
        self.ps_error = ps_error
        self.stop_error = stop_error

    def running_models(self) -> list[str]:
        if self.ps_error:
            raise OllamaError("no ps", "http")
        return list(self.running)

    def stop_model(self, name: str) -> None:
        self.stopped.append(name)
        if self.stop_error:
            raise OllamaError("offline", "offline")
        self.running = [item for item in self.running if not same_model(item, name)]

    def start_model(self, name: str) -> None:
        self.started.append(name)
        self.running.append(name)


def test_switch_stops_other_loaded_models_and_starts_the_target():
    client = _FakeOllama(["llama3.2:latest", "other:latest"])
    apply_model_switch(client, "arete-27b:latest", "llama3.2")
    assert client.stopped == ["llama3.2:latest", "other:latest"]
    assert client.started == ["arete-27b:latest"]


def test_switch_leaves_a_model_that_is_already_loaded():
    client = _FakeOllama(["arete-27b:latest"])
    apply_model_switch(client, "arete-27b", "llama3.2")
    assert client.stopped == []
    assert client.started == []


def test_switch_falls_back_when_the_running_list_is_unavailable():
    client = _FakeOllama([], ps_error=True, stop_error=True)
    apply_model_switch(client, "arete-27b:latest", "llama3.2")
    assert client.stopped == ["llama3.2"]
    assert client.started == ["arete-27b:latest"]


def _close(window, db, qapp) -> None:
    for session in db.list_sessions():
        if session.status != "closed":
            db.mark_closed(session.id)
    window.close()
    for _ in range(40):
        qapp.processEvents()
        if window._quit_done and not window.isVisible():
            break


def test_model_list_sits_left_of_session_and_a_choice_loads_that_model(tmp_path, qapp, monkeypatch):
    monkeypatch.setattr(
        OllamaClient,
        "list_models",
        lambda self: ["llama3.2:latest", "arete-27b:latest"],
    )
    monkeypatch.setattr(OllamaClient, "running_models", lambda self: ["llama3.2:latest"])
    stopped: list[str] = []
    started: list[str] = []
    monkeypatch.setattr(OllamaClient, "stop_model", lambda self, name: stopped.append(name))
    monkeypatch.setattr(OllamaClient, "start_model", lambda self, name: started.append(name))
    db = Database(tmp_path / "maid.db")
    db.migrate()
    window = MainWindow(db)
    window.show()
    for _ in range(50):
        qapp.processEvents()
        if window.model_combo.findText("arete-27b:latest") >= 0:
            break
    strip = window.model_combo.parentWidget()
    row = strip.layout().itemAt(0).layout()
    labels = []
    for index in range(4):
        widget = row.itemAt(index).widget()
        labels.append(widget.text() if isinstance(widget, QLabel) else widget.objectName())
    assert labels == ["Model", "modelCombo", "Session", "sessionCombo"]
    index = window.model_combo.findText("arete-27b:latest")
    assert index >= 0
    window.model_combo.setCurrentIndex(index)
    window._model_activated(index)
    for _ in range(50):
        qapp.processEvents()
        if started:
            break
        QTest.qWait(20)
    assert stopped == ["llama3.2:latest"]
    assert started == ["arete-27b:latest"]
    assert window.orchestrator.current().ollama_model == "arete-27b:latest"
    assert Settings.load(db).ollama_model == "arete-27b:latest"
    assert "arete-27b:latest is loaded." in window.status_label.text()
    _close(window, db, qapp)
    db.close()


def test_seed_cancel_keeps_the_session_and_a_choice_starts_that_model(tmp_path, qapp, monkeypatch):
    monkeypatch.setattr(
        OllamaClient,
        "list_models",
        lambda self: ["llama3.2:latest", "arete-27b:latest"],
    )
    monkeypatch.setattr(OllamaClient, "running_models", lambda self: ["llama3.2:latest"])
    started: list[str] = []
    monkeypatch.setattr(OllamaClient, "stop_model", lambda self, name: None)
    monkeypatch.setattr(OllamaClient, "start_model", lambda self, name: started.append(name))
    db = Database(tmp_path / "maid.db")
    db.migrate()
    window = MainWindow(db)
    window.show()
    qapp.processEvents()
    sid = window.orchestrator.session_id
    db.add_turn(sid, actor="user", kind="message", content="keep me")
    qapp.processEvents()
    window._model_names = ["llama3.2:latest", "arete-27b:latest"]
    item = HistoryItem("k", "question", "user", "t", "keep me", "missing")

    def cancel():
        for widget in qapp.topLevelWidgets():
            if isinstance(widget, SeedModelDialog) and widget.isVisible():
                widget.reject()
                return
        raise AssertionError("model dialog did not open")

    QTimer.singleShot(0, cancel)
    window._start_from_items([item])
    assert window.orchestrator.session_id == sid
    assert [turn.content for turn in db.list_turns(sid)] == ["keep me"]

    def choose_then_leave():
        for widget in qapp.topLevelWidgets():
            if isinstance(widget, SeedModelDialog) and widget.isVisible():
                widget.model_combo.setCurrentText("arete-27b:latest")
                QTimer.singleShot(0, choose_then_leave)
                widget.start_button.click()
                return
            if isinstance(widget, QMessageBox) and widget.isVisible():
                for button in widget.buttons():
                    if button.text() == "Leave it open":
                        button.click()
                        return
        raise AssertionError("expected the model dialog or the open-session question")

    QTimer.singleShot(0, choose_then_leave)
    window._start_from_items([item])
    for _ in range(50):
        qapp.processEvents()
        if started:
            break
        QTest.qWait(20)
    fresh = window.orchestrator.current()
    assert fresh is not None
    assert fresh.id != sid
    assert fresh.ollama_model == "arete-27b:latest"
    assert db.get_session(sid).ollama_model in {"llama3.2", "llama3.2:latest"}
    assert started == ["arete-27b:latest"]
    _close(window, db, qapp)
    db.close()
