"""Chat scroll, history clicks, and the chat search bar stay on the same turn."""

import os
import re

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6")

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from monitor_maid.context import build_history_items, link_history
from monitor_maid.db import Database
from monitor_maid.models import HistoryItem, Prompt, Summary, Turn
from monitor_maid.ui.history_panel import HistoryPanel
from monitor_maid.ui.main_window import MainWindow
from monitor_maid.ui.transcript import find_spans


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


def _turn(turn_id: str, actor: str, kind: str, content: str) -> Turn:
    return Turn(
        id=turn_id,
        session_id="s",
        seq=1,
        actor=actor,
        kind=kind,
        content=content,
        created_at="2026-11-12T12:00:00Z",
        visible_to_local=actor != "grok",
    )


def test_find_spans_is_case_insensitive_and_skips_empty_regex_hits():
    assert find_spans("Alpha beta", "alpha", regex=False) == [(0, 5)]
    assert find_spans("", "alpha", regex=False) == []
    assert find_spans("a1 a2", r"a\d", regex=True) == [(0, 2), (3, 5)]
    assert find_spans("a\nb", "a.b", regex=True) == []
    assert find_spans("a\nb", "^b", regex=True) == [(2, 3)]
    assert find_spans("bbb", "a*", regex=True) == []
    assert find_spans("ba", "a*", regex=True) == [(1, 2)]
    with pytest.raises(re.error):
        find_spans("abc", "(", regex=True)


def test_link_history_pairs_summary_and_prompt_records_with_turns():
    reply = "intro\n```prompt\nDo the thing.\n```\n```prompt\nThen stop.\n```"
    turns = [
        _turn("q", "user", "message", "What?"),
        _turn("a", "local_llm", "prompt", reply),
        _turn("s", "local_llm", "summary", "A short summary."),
        _turn("g", "grok", "message", "Hidden note"),
    ]
    items = build_history_items(
        turns,
        [Summary("sum1", "s", "A short summary.", "2026-11-12T12:00:03Z")],
        [
            Prompt("p1", "s", "Do the thing.", "2026-11-12T12:00:02Z"),
            Prompt("p2", "s", "Then stop.", "2026-11-12T12:00:02Z"),
        ],
    )
    turn_to_key, key_to_turn = link_history(turns, items)
    by_kind = {item.kind: item for item in items if item.kind != "prompt"}
    prompts = [item for item in items if item.kind == "prompt"]
    assert key_to_turn[by_kind["question"].key] == "q"
    assert key_to_turn[by_kind["grok"].key] == "g"
    assert key_to_turn[by_kind["summary"].key] == "s"
    assert [key_to_turn[item.key] for item in prompts] == ["a", "a"]
    assert turn_to_key["a"] == prompts[0].key
    assert turn_to_key["s"] == by_kind["summary"].key


def test_history_row_scrolls_to_the_middle(qapp):
    panel = HistoryPanel()
    items = [
        HistoryItem(
            key=f"turn:{index}",
            kind="question",
            actor="user",
            created_at=f"2026-11-12T12:{index:02d}:00Z",
            content=f"question {index}",
            source_id=str(index),
        )
        for index in range(30)
    ]
    panel.resize(480, 220)
    panel.show()
    panel.table.setMaximumHeight(120)
    panel.set_items(items)
    qapp.processEvents()
    assert panel.scroll_to_key(items[15].key)
    qapp.processEvents()
    row = panel.row_for_key(items[15].key)
    rect = panel.table.visualRect(panel.table.model().index(row, 1))
    view_height = panel.table.viewport().height()
    assert view_height > rect.height()
    assert abs(rect.center().y() - (view_height / 2)) <= rect.height()
    panel.close()


def test_chat_and_history_follow_each_other_and_search(tmp_path, qapp):
    db = Database(tmp_path / "maid.db")
    db.migrate()
    window = MainWindow(db)
    window.resize(1100, 720)
    window.show()
    qapp.processEvents()
    window.orchestrator.new_session()
    qapp.processEvents()
    session = window.orchestrator.current()
    assert session is not None
    turn_ids: list[str] = []
    for index in range(12):
        question = db.add_turn(
            session.id,
            actor="user",
            kind="message",
            content=f"question {index}\n" + ("q line\n" * 24),
        )
        answer = db.add_turn(
            session.id,
            actor="local_llm",
            kind="message",
            content=f"answer {index} token-{index}\n" + ("a line\n" * 24),
        )
        turn_ids.extend((question.id, answer.id))
    window.history.table.setMaximumHeight(140)
    window._reload_transcript()
    for _ in range(4):
        qapp.processEvents()

    search = window.chat_search
    assert search.mapTo(window, search.rect().topLeft()).y() < window.transcript.mapTo(
        window, window.transcript.rect().topLeft()
    ).y()
    chat = window.transcript
    bar = chat.verticalScrollBar()
    assert bar.maximum() > 0
    assert bar.value() == bar.maximum()
    assert window.history.table.currentRow() == window.history.table.rowCount() - 1

    bubble = chat._bubble_for(turn_ids[8])
    assert bubble is not None
    viewport_h = chat.viewport().height()
    parked = int(bubble.y() + 24 - (viewport_h / 2))
    parked = max(bar.minimum(), min(parked, bar.maximum()))
    bar.setValue(parked)
    qapp.processEvents()
    assert bar.value() == parked
    assert window.history.item_for_row(window.history.table.currentRow()).source_id == turn_ids[8]
    history_rect = window.history.table.visualRect(
        window.history.table.model().index(window.history.table.currentRow(), 1)
    )
    history_view = window.history.table.viewport().height()
    assert abs(history_rect.center().y() - (history_view / 2)) <= history_rect.height() + 2

    target = turn_ids[14]
    row = window.history.row_for_key(f"turn:{target}")
    window.history.scroll_to_key(f"turn:{target}")
    qapp.processEvents()
    assert bar.value() == parked
    cell = window.history.table.item(row, 3)
    rect = window.history.table.visualItemRect(cell)
    QTest.mouseClick(
        window.history.table.viewport(),
        Qt.MouseButton.LeftButton,
        pos=rect.center(),
    )
    qapp.processEvents()
    delta = chat.turn_viewport_delta(target)
    assert delta is not None
    assert abs(delta) <= 4

    window.history._boxes["answer"].setChecked(False)
    qapp.processEvents()
    chat.scroll_to_turn(turn_ids[15])
    qapp.processEvents()
    current = window.history.item_for_row(window.history.table.currentRow())
    assert current is not None
    assert current.kind == "question"
    assert current.source_id == turn_ids[14]
    window.history._boxes["answer"].setChecked(True)
    qapp.processEvents()

    search.edit.setText("token-")
    qapp.processEvents()
    assert search.count.text() == "1 of 12"
    assert search.next.isEnabled()
    search.next.click()
    qapp.processEvents()
    assert search.count.text() == "2 of 12"
    assert chat._matches[chat._match_index].turn_id == turn_ids[3]
    assert chat._bubble_for(turn_ids[3]).body.extraSelections()

    search.regex.setChecked(True)
    search.edit.setText(r"token-\d+")
    qapp.processEvents()
    assert search.count.text() == "1 of 12"
    search.edit.setText("TOKEN-4")
    qapp.processEvents()
    assert search.count.text() == "1 of 1"
    search.edit.setText("(")
    qapp.processEvents()
    assert search.count.text() == "Invalid pattern"
    assert not search.next.isEnabled()
    search.edit.clear()
    qapp.processEvents()
    assert search.count.text() == ""

    _close(window, db, qapp)
    db.close()
