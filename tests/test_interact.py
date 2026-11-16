"""Interact with Local sends tasks one way and command results back."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6")

from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from monitor_maid.context import build_local_messages
from monitor_maid.db import Database
from monitor_maid.grok_build import RunResult
from monitor_maid.models import LLMReply
from monitor_maid.ollama_client import OllamaClient
from monitor_maid.prompts import INTERACT_ASK
from monitor_maid.ui.dialogs import LocalSessionDialog
from monitor_maid.ui.main_window import MainWindow


@pytest.fixture
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


def _close(window, db, qapp) -> None:
    for session in db.list_sessions():
        if session.status != "closed":
            db.mark_closed(session.id)
    window.close()
    for _ in range(40):
        qapp.processEvents()
        if window._quit_done and not window.isVisible():
            break


def _wait_idle(window, qapp) -> None:
    for _ in range(400):
        qapp.processEvents()
        if not window.orchestrator.busy and not window.orchestrator.queue:
            return
        QTest.qWait(50)
    raise AssertionError(window.status_label.text())


def test_launch_has_no_session_and_interact_needs_one(tmp_path, qapp):
    db = Database(tmp_path / "maid.db")
    db.migrate()
    window = MainWindow(db)
    window.show()
    qapp.processEvents()
    assert window.orchestrator.current() is None
    assert window.orchestrator.submit_interact() is False
    assert "Open a local session" in window.status_label.text()
    _close(window, db, qapp)
    db.close()


def test_local_session_dialog_requires_a_real_folder(tmp_path, qapp):
    dialog = LocalSessionDialog(["llama3.2"], "llama3.2", str(tmp_path))
    dialog.folder.setText(str(tmp_path / "missing-folder"))
    dialog.start_button.click()
    assert dialog.choice_folder == ""
    dialog.folder.setText(str(tmp_path))
    dialog.start_button.click()
    assert dialog.choice_model == "llama3.2"
    assert dialog.choice_folder == str(tmp_path.resolve())


def test_interact_returns_command_results_and_stops_at_the_live_limit(tmp_path, qapp, monkeypatch):
    prompts: list[str] = []
    local_calls: list[str] = []

    def fake_run(spec, on_update=None, *, binary=None):
        prompts.append(spec.prompt)
        if len(prompts) == 1:
            text = "```maid-to-local\nPrint hello-from-local with the shell.\n```"
        else:
            text = "```maid-to-local\nDo it again.\n```"
        return RunResult(
            exit_code=0,
            session_id="11111111-1111-4111-8111-111111111111",
            text=text,
            display=text,
            stderr="",
            refused=False,
        )

    def fake_chat(self, model, messages, timeout):
        local_calls.append(messages[-1]["content"])
        return LLMReply(
            text=(
                "local-only-prose\n"
                "```maid-exec\n"
                '{"op":"shell","command":"echo hello-from-local"}\n'
                "```"
            )
        )

    monkeypatch.setattr("monitor_maid.orchestrator.run_prompt", fake_run)
    monkeypatch.setattr("monitor_maid.orchestrator.grok_available", lambda: True)
    monkeypatch.setattr(OllamaClient, "chat", fake_chat)
    db = Database(tmp_path / "maid.db")
    db.migrate()
    window = MainWindow(db)
    window.show()
    qapp.processEvents()
    window.orchestrator.new_session(cwd=str(tmp_path))
    qapp.processEvents()
    session = window.orchestrator.current()
    assert session is not None
    assert session.cwd == str(tmp_path)
    assert window.orchestrator.cwd == str(tmp_path)
    assert session.grok_relay_limit == 4
    db.add_turn(session.id, actor="user", kind="message", content="secret-local-fact")
    window.orchestrator.set_execution_mode("automatic", 4)

    dropped = {"done": False}

    def shrink_limit(text: str) -> None:
        if dropped["done"] or "Local model is working" not in text:
            return
        dropped["done"] = True
        window.orchestrator.set_relay_mode("manual", 1)

    window.orchestrator.status_message.connect(shrink_limit)
    assert window.orchestrator.submit_interact()
    _wait_idle(window, qapp)

    assert dropped["done"]
    assert len(local_calls) == 1
    assert len(prompts) == 2
    assert prompts[0] == INTERACT_ASK
    assert "secret-local-fact" not in prompts[0]
    assert "secret-local-fact" not in prompts[1]
    assert "local-only-prose" not in prompts[1]
    assert "hello-from-local" in prompts[1]
    assert "exit code: 0" in prompts[1]
    assert "Do not assign another task" in prompts[1]
    assert window.orchestrator.current().grok_relay_used == 0
    local = "\n".join(message["content"] for message in build_local_messages(db.list_turns(session.id)))
    assert "secret-local-fact" in local
    assert "Print hello-from-local with the shell." in local
    assert "Interact with Local handoff" not in local
    _close(window, db, qapp)
    db.close()


def test_interact_limit_zero_does_not_call_grok(tmp_path, qapp, monkeypatch):
    def fail_run(*_args, **_kwargs):
        raise AssertionError("Grok was called even though Forward N is 0")

    monkeypatch.setattr("monitor_maid.orchestrator.run_prompt", fail_run)
    monkeypatch.setattr("monitor_maid.orchestrator.grok_available", lambda: True)
    db = Database(tmp_path / "maid.db")
    db.migrate()
    window = MainWindow(db)
    window.show()
    qapp.processEvents()
    window.orchestrator.new_session()
    qapp.processEvents()
    window.orchestrator.set_relay_mode("manual", 0)
    assert window.orchestrator.submit_interact()
    _wait_idle(window, qapp)
    assert "limit is 0" in window.status_label.text()
    _close(window, db, qapp)
    db.close()
