"""A message is shown in full, and Follow latest returns to the newest turn."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6")

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QApplication

from monitor_maid.models import Turn
from monitor_maid.ui.transcript import FullText, TranscriptView


@pytest.fixture
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


def _turn(text: str) -> Turn:
    return Turn(
        id="t1",
        session_id="s",
        seq=1,
        actor="local_llm",
        kind="message",
        content=text,
        created_at="2026-11-08T12:00:00Z",
        visible_to_local=True,
    )


def test_full_message_and_follow_latest(qapp):
    view = TranscriptView()
    font = QFont()
    font.setPixelSize(16)
    view.setFont(font)
    view.resize(420, 180)
    view.show()
    qapp.processEvents()
    text = "\n".join(f"line {index} of the reply" for index in range(40))
    view.set_turns([_turn(text)])
    qapp.processEvents()
    body = view.findChild(FullText)
    assert body is not None
    assert body.toPlainText() == text
    assert body.document().blockCount() == 40
    assert body.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    # document.size().height() is the block count for this widget, not pixels.
    assert body.height() >= 40 * body.fontMetrics().lineSpacing()
    assert view.widget().minimumHeight() > view.viewport().height()
    bar = view.verticalScrollBar()
    assert bar.maximum() > 0
    assert bar.value() == bar.maximum()
    assert not view.follow_button.isVisible()
    bar.setValue(0)
    qapp.processEvents()
    assert view.follow_button.isVisible()
    view.follow_latest()
    qapp.processEvents()
    assert bar.value() == bar.maximum()
    assert not view.follow_button.isVisible()
