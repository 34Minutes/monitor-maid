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
from monitor_maid.prompts import CONCLUSION_INSTRUCTION, SUMMARY_INSTRUCTION
from monitor_maid.ui.dialogs import EndSessionDialog, NoteDialog, NotesBrowser
from monitor_maid.ui.main_window import MainWindow
from monitor_maid.ui.transcript import Bubble


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
    assert db.list_conclusions(sid) == []
    assert not any(turn.kind in {"summary", "conclusion"} for turn in db.list_turns(sid))
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
        "Create conclusions",
        "",
        "Browse sessions…",
        "View summary",
        "View conclusion",
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
    assert "View conclusion" in labels
    by_text = {action.text(): action for action in menu.actions()}
    assert by_text["View summary"].isEnabled()
    assert not by_text["View conclusion"].isEnabled()
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
    assert not question_actions["View conclusion"].isEnabled()

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
        if "JSON array" in last:
            return LLMReply(text='["Keep the carried summary."]')
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

    window.act_create_conclusions.trigger()
    _wait_idle(window, qapp)
    assert db.get_session(sid).status == "open"
    assert [item.content for item in db.list_conclusions(sid)] == ["Keep the carried summary."]
    assert CONCLUSION_INSTRUCTION not in "\n".join(turn.content for turn in db.list_turns(sid))
    assert any("JSON array" in text for text in calls)

    items = build_history_items(
        db.list_turns(sid),
        db.list_summaries(sid),
        db.list_conclusions(sid),
    )
    summary = next(item for item in items if item.kind == "summary")
    conclusion = next(item for item in items if item.kind == "conclusion")
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
    window._start_from_items([stale, conclusion, grok])
    qapp.processEvents()
    assert window.orchestrator.session_id == fresh
    seeded = db.list_turns(fresh)
    blob = "\n".join(turn.content for turn in seeded)
    assert "STALE COPY" not in blob
    assert "They discussed storing a session summary." in blob
    assert "Keep the carried summary." in blob
    assert "hidden remark" in blob
    assert "Grok:" not in blob
    assert all(turn.kind == "message" and turn.visible_to_local for turn in seeded)
    assert all(turn.actor != "grok" for turn in seeded)
    seen = build_local_messages(seeded)
    seen_blob = "\n".join(message["content"] for message in seen)
    assert "hidden remark" in seen_blob
    assert "Grok:" not in seen_blob
    bubbles = window.transcript.findChildren(Bubble)
    assert any("Carried summary" in bubble.meta.text() for bubble in bubbles)
    assert any("Carried conclusion" in bubble.meta.text() for bubble in bubbles)
    assert any("Carried note" in bubble.meta.text() for bubble in bubbles)
    assert db.get_session(sid).status == "open"

    _close(window, db, qapp)
    assert window._quit_done
    db.close()
