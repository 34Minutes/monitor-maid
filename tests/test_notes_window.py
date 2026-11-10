"""Manual summaries, the session browser, and seeding a shorter session."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6")

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QMessageBox

from monitor_maid.context import build_history_items, build_local_messages
from monitor_maid.db import Database
from monitor_maid.models import NO_SUBSTANTIAL, HistoryItem, LLMReply
from monitor_maid.ollama_client import OllamaClient
from monitor_maid.prompts import PROMPT_INSTRUCTION, SUMMARY_INSTRUCTION
from monitor_maid.xai_client import XAIClient
from monitor_maid.ui.dialogs import EndSessionDialog, NoteDialog, NotesBrowser, SeedModelDialog
from monitor_maid.ui.main_window import MainWindow


@pytest.fixture(scope="module")
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


def _wait_idle(window, qapp, steps: int = 150) -> None:
    for _ in range(steps):
        qapp.processEvents()
        if not window.orchestrator.busy and not window.orchestrator.queue:
            return
        assert not window.progress.isVisible()
        QTest.qWait(20)
    raise AssertionError(window.status_label.text())


def _menu_texts(window) -> list[str]:
    menu = window.menuBar().actions()[0].menu()
    return [action.text() for action in menu.actions()]


def test_browse_deletes_a_session_and_keeps_the_rest(tmp_path, qapp):
    db = Database(tmp_path / "maid.db")
    db.migrate()
    window = MainWindow(db)
    window.show()
    qapp.processEvents()
    current = window.orchestrator.session_id
    other = db.create_session(
        title="Discard me",
        execution_mode="manual",
        auto_limit=4,
        grok_relay_mode="manual",
        grok_relay_limit=4,
        monitor_mode=False,
        ollama_model="llama3.2",
    )
    db.add_turn(other.id, actor="user", kind="message", content="temporary")
    db.add_summary(other.id, "temporary summary")
    browser = NotesBrowser(db, window, current_id=current)
    assert browser.delete_button.text() == "Delete session"
    assert browser.delete_button.isEnabled()

    def cancel_box():
        for widget in qapp.topLevelWidgets():
            if isinstance(widget, QMessageBox) and widget.isVisible():
                for button in widget.buttons():
                    if button.text() == "Cancel":
                        button.click()
                        return
        raise AssertionError("delete confirmation did not open")

    QTimer.singleShot(0, cancel_box)
    browser.delete_button.click()
    assert db.get_session(current) is not None
    assert browser.deleted_ids == []

    blocked = NotesBrowser(db, window, current_id=current, protected_id=current)
    blocked.sessions.setCurrentRow(0)
    # The protected session is whichever row is current after selecting its id.
    for row in range(blocked.sessions.count()):
        blocked.sessions.setCurrentRow(row)
        if blocked.sessions.item(row).data(Qt.ItemDataRole.UserRole) == current:
            assert not blocked.delete_button.isEnabled()
    blocked.close()

    browser._remove_session(other.id)
    assert db.get_session(other.id) is None
    assert db.list_summaries(other.id) == []
    assert other.id in browser.deleted_ids
    assert db.get_session(current) is not None
    browser._remove_session(current)
    window._apply_deleted_sessions(browser.deleted_ids)
    assert db.get_session(current) is None
    assert window.orchestrator.session_id not in {None, current}
    assert window.orchestrator.current() is not None
    browser.close()
    _close(window, db, qapp)
    db.close()


def test_end_no_closes_without_storing_a_summary(tmp_path, qapp):
    db = Database(tmp_path / "maid.db")
    db.migrate()
    window = MainWindow(db)
    window.show()
    qapp.processEvents()
    dialog = EndSessionDialog(window)
    assert [dialog.cancel_button.text(), dialog.yes_button.text(), dialog.no_button.text()] == [
        "Cancel",
        "Yes",
        "No",
    ]
    dialog.cancel_button.click()
    assert dialog.choice == "cancel"
    sid = window.orchestrator.session_id
    db.add_turn(
        sid,
        actor="user",
        kind="message",
        content="Please explain how the session summary should be stored " * 3,
    )
    db.add_summary(sid, "Already stored")
    window.orchestrator.request_end(window._after_end, store_notes=False)
    _wait_idle(window, qapp)
    assert db.get_session(sid).status == "closed"
    assert [item.content for item in db.list_summaries(sid)] == ["Already stored"]
    assert db.list_prompts(sid) == []
    assert not any(turn.kind in {"summary", "prompt"} for turn in db.list_turns(sid))
    assert not window.progress.isVisible()
    _close(window, db, qapp)
    db.close()


def test_session_menu_stores_a_short_note_without_closing(tmp_path, qapp):
    db = Database(tmp_path / "maid.db")
    db.migrate()
    window = MainWindow(db)
    window.show()
    qapp.processEvents()
    assert _menu_texts(window) == [
        "Rename session…",
        "",
        "Create summary",
        "Create prompt",
        "Create prompt with Grok",
        "",
        "Browse sessions…",
        "View summary",
        "View prompt",
        "",
        "New session from checked items",
    ]
    assert window.rename_button.text() == "Rename"
    assert window.history.table.contextMenuPolicy() == Qt.ContextMenuPolicy.CustomContextMenu

    sid = window.orchestrator.session_id
    assert window.orchestrator.rename_session("  Kept name  ")
    qapp.processEvents()
    assert db.get_session(sid).title == "Kept name"
    assert "Kept name" in window.session_combo.currentText()
    assert window.windowTitle().endswith("Kept name")
    assert window.orchestrator.rename_session("   ") is False
    assert db.get_session(sid).title == "Kept name"
    window.act_create_summary.trigger()
    qapp.processEvents()
    assert db.list_summaries(sid) == []
    assert "nothing in this session" in window.status_label.text()

    db.add_turn(sid, actor="user", kind="message", content="hi")
    qapp.processEvents()
    window.act_create_summary.trigger()
    qapp.processEvents()
    assert db.get_session(sid).status == "open"
    assert db.list_summaries(sid)[0].content == NO_SUBSTANTIAL
    assert not window.progress.isVisible()

    window.history.all_button.click()
    summary = next(item for item in window.history._items if item.kind == "summary")
    menu = window.history.build_context_menu(summary)
    labels = [action.text() for action in menu.actions()]
    assert "New session from checked items" in labels
    assert "View summary" in labels
    assert "View prompt" in labels
    by_text = {action.text(): action for action in menu.actions()}
    assert by_text["View summary"].isEnabled()
    assert not by_text["View prompt"].isEnabled()
    assert by_text["New session from checked items"].isEnabled()
    window.history._boxes["summary"].setChecked(False)
    assert all(item.kind != "summary" for item in window.history.selected_items())
    assert any(item.kind == "summary" for item in window.history.checked_items())
    hidden = window.history.build_context_menu(None)
    hidden_actions = {action.text(): action for action in hidden.actions()}
    assert hidden_actions["New session from checked items"].isEnabled()
    window.history._boxes["summary"].setChecked(True)
    question_menu = window.history.build_context_menu(
        next(item for item in window.history._items if item.kind == "question")
    )
    question_actions = {action.text(): action for action in question_menu.actions()}
    assert not question_actions["View summary"].isEnabled()
    assert not question_actions["View prompt"].isEnabled()

    browser = NotesBrowser(db, window, current_id=sid)
    assert browser.sessions.count() == 1
    assert browser.notes.count() == 1
    assert "Summary" in browser.notes.item(0).text()
    browser.close()

    dialog = NoteDialog(
        "Summary",
        "Latest summary in this session.",
        summary.content,
        "Start a new session with this summary",
        window,
    )
    dialog.seed_button.click()
    assert dialog.seed is True

    _close(window, db, qapp)
    assert window._quit_done
    db.close()


def test_model_notes_seed_a_new_session_from_the_database(tmp_path, qapp, monkeypatch):
    calls = []

    def fake_chat(self, model, messages, timeout):
        last = messages[-1]["content"]
        calls.append(last)
        if "standalone prompt" in last:
            return LLMReply(text="```prompt\nKeep the carried summary.\n```")
        return LLMReply(text="They discussed storing a session summary.")

    monkeypatch.setattr(OllamaClient, "chat", fake_chat)
    db = Database(tmp_path / "maid.db")
    db.migrate()
    window = MainWindow(db)
    window.show()
    qapp.processEvents()
    sid = window.orchestrator.session_id
    db.add_turn(
        sid,
        actor="user",
        kind="message",
        content="Please explain how the session summary should be stored " * 3,
    )
    db.add_turn(
        sid,
        actor="grok",
        kind="message",
        content="Grok: hidden remark",
        visible_to_local=True,
    )
    qapp.processEvents()

    window.act_create_summary.trigger()
    _wait_idle(window, qapp)
    assert db.get_session(sid).status == "open"
    assert db.list_summaries(sid)[0].content == "They discussed storing a session summary."
    assert SUMMARY_INSTRUCTION not in "\n".join(turn.content for turn in db.list_turns(sid))
    assert not window.progress.isVisible()
    stored = build_local_messages(db.list_turns(sid))
    assert "They discussed storing a session summary." not in "\n".join(
        message["content"] for message in stored
    )

    window.act_create_prompt.trigger()
    _wait_idle(window, qapp)
    assert db.get_session(sid).status == "open"
    stored_prompts = db.list_prompts(sid)
    assert [item.content for item in stored_prompts] == ["Keep the carried summary."]
    assert stored_prompts[0].author == "local_llm"
    prompt_turns = [turn for turn in db.list_turns(sid) if turn.kind == "prompt"]
    assert len(prompt_turns) == 1
    assert prompt_turns[0].actor == "local_llm"
    assert prompt_turns[0].content == "```prompt\nKeep the carried summary.\n```"
    assert PROMPT_INSTRUCTION not in "\n".join(turn.content for turn in db.list_turns(sid))
    assert any("standalone prompt" in text for text in calls)
    local_after = build_local_messages(db.list_turns(sid))
    assert "Keep the carried summary." in "\n".join(
        message["content"] for message in local_after
    )

    items = build_history_items(
        db.list_turns(sid),
        db.list_summaries(sid),
        db.list_prompts(sid),
    )
    summary = next(item for item in items if item.kind == "summary")
    prompt = next(item for item in items if item.kind == "prompt")
    grok = next(item for item in items if item.kind == "grok")
    stale = HistoryItem(
        summary.key,
        "summary",
        "local_llm",
        summary.created_at,
        "STALE COPY",
        summary.source_id,
    )
    window.orchestrator.new_session()
    qapp.processEvents()
    fresh = window.orchestrator.session_id
    assert fresh != sid

    def accept_seed_model():
        for widget in qapp.topLevelWidgets():
            if isinstance(widget, SeedModelDialog) and widget.isVisible():
                widget.start_button.click()
                return
        raise AssertionError("model dialog did not open")

    QTimer.singleShot(0, accept_seed_model)
    window._start_from_items([stale, prompt, grok])
    qapp.processEvents()
    assert window.orchestrator.session_id == fresh
    assert db.list_turns(fresh) == []
    draft = window.composer.local_edit.toPlainText()
    assert "STALE COPY" not in draft
    assert "They discussed storing a session summary." in draft
    assert "Keep the carried summary." in draft
    assert "hidden remark" in draft
    assert "Grok:" not in draft
    assert "Summary\n" in draft
    assert "Prompt\n" in draft
    assert "Note\n" in draft
    assert "in the input box" in window.status_label.text()
    assert db.get_session(sid).status == "open"

    _close(window, db, qapp)
    assert window._quit_done
    db.close()


def test_a_chat_reply_stores_a_prompt_only_when_marked(tmp_path, qapp, monkeypatch):
    def fake_chat(self, model, messages, timeout):
        last = messages[-1]["content"]
        if "mark this one" in last:
            return LLMReply(text="```prompt\nKeep going with manual approval.\n```")
        return LLMReply(text="An ordinary answer that mentions the word prompt.\n```prompt\nnot closed")

    monkeypatch.setattr(OllamaClient, "chat", fake_chat)
    db = Database(tmp_path / "maid.db")
    db.migrate()
    window = MainWindow(db)
    window.show()
    qapp.processEvents()
    sid = window.orchestrator.session_id
    assert window.orchestrator.submit_local("Say something about a prompt.")
    _wait_idle(window, qapp)
    assert db.list_prompts(sid) == []
    assert not any(turn.kind == "prompt" for turn in db.list_turns(sid))

    assert window.orchestrator.submit_local("Please mark this one.")
    _wait_idle(window, qapp)
    prompts = db.list_prompts(sid)
    assert [item.content for item in prompts] == ["Keep going with manual approval."]
    assert prompts[0].author == "local_llm"
    marked = [turn for turn in db.list_turns(sid) if turn.kind == "prompt"]
    assert len(marked) == 1
    assert marked[0].content == "```prompt\nKeep going with manual approval.\n```"
    local = "\n".join(
        message["content"] for message in build_local_messages(db.list_turns(sid))
    )
    assert "Keep going with manual approval." in local
    assert "An ordinary answer" in local
    assert "Prompt stored." in window.status_label.text()

    _close(window, db, qapp)
    db.close()


def test_grok_prompt_is_stored_and_a_refusal_is_not(tmp_path, qapp, monkeypatch):
    replies = [
        LLMReply(text="```prompt\nContinue the listing work with manual approval.\n```"),
        LLMReply(text="An ordinary answer with no fence."),
        LLMReply(text="No.", refused=True),
    ]

    def fake_complete(self, model, messages, timeout):
        assert any("standalone prompt" in message["content"] for message in messages)
        return replies.pop(0)

    monkeypatch.setattr(XAIClient, "complete", fake_complete)
    monkeypatch.setattr(
        "monitor_maid.orchestrator.effective_api_key",
        lambda _settings: "test-key",
    )
    db = Database(tmp_path / "maid.db")
    db.migrate()
    window = MainWindow(db)
    window.show()
    qapp.processEvents()
    sid = window.orchestrator.session_id
    db.add_turn(
        sid,
        actor="user",
        kind="message",
        content="Please explain how a stored prompt should be written " * 3,
    )
    qapp.processEvents()
    assert window.orchestrator.request_prompt("grok")
    _wait_idle(window, qapp)
    prompts = db.list_prompts(sid)
    assert [item.content for item in prompts] == [
        "Continue the listing work with manual approval."
    ]
    assert prompts[0].author == "grok"
    prompt_turns = [turn for turn in db.list_turns(sid) if turn.kind == "prompt"]
    assert len(prompt_turns) == 1
    assert prompt_turns[0].actor == "grok"
    assert prompt_turns[0].visible_to_local is False
    assert prompt_turns[0].content.startswith("```prompt")
    hidden = build_local_messages(db.list_turns(sid))
    assert "Continue the listing work" not in "\n".join(
        message["content"] for message in hidden
    )

    assert window.orchestrator.request_prompt("grok")
    _wait_idle(window, qapp)
    assert len(db.list_prompts(sid)) == 1
    assert "No prompt was stored" in window.status_label.text()
    assert any(
        turn.actor == "grok" and turn.kind == "message" and "ordinary answer" in turn.content
        for turn in db.list_turns(sid)
    )

    assert window.orchestrator.request_prompt("grok")
    _wait_idle(window, qapp)
    assert len(db.list_prompts(sid)) == 1
    assert "Nothing was stored" in window.status_label.text()

    _close(window, db, qapp)
    db.close()
