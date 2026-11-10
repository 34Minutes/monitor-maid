"""Enter submits. Control or Command plus Enter inserts a newline."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6")

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QTextCursor
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from monitor_maid.db import Database
from monitor_maid.ui.composer import Composer
from monitor_maid.ui.main_window import MainWindow


@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


def _type(edit, text: str) -> None:
    edit.setPlainText(text)
    cursor = edit.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    edit.setTextCursor(cursor)


def test_enter_sends_and_ctrl_enter_adds_a_line(qapp):
    composer = Composer()
    composer.show()
    qapp.processEvents()
    sent = []
    both = []
    composer.send_local.connect(sent.append)
    composer.send_both.connect(lambda local, grok: both.append((local, grok)))
    assert composer.enter_sends.objectName() == "enterSends"
    assert composer.enter_sends.isChecked()
    assert composer.hint.isVisible()
    assert "Enter sends" in composer.hint.text()

    composer.local_edit.setPlainText("hello")
    QTest.keyClick(composer.local_edit, Qt.Key.Key_Return)
    assert sent == ["hello"]
    assert composer.local_edit.toPlainText() == ""

    _type(composer.local_edit, "line")
    QTest.keyClick(
        composer.local_edit,
        Qt.Key.Key_Return,
        Qt.KeyboardModifier.ControlModifier,
    )
    assert composer.local_edit.toPlainText() == "line\n"
    assert sent == ["hello"]
    QTest.keyClick(
        composer.local_edit,
        Qt.Key.Key_Enter,
        Qt.KeyboardModifier.MetaModifier,
    )
    assert composer.local_edit.toPlainText() == "line\n\n"

    _type(composer.local_edit, "shift")
    QTest.keyClick(
        composer.local_edit,
        Qt.Key.Key_Return,
        Qt.KeyboardModifier.ShiftModifier,
    )
    assert sent == ["hello"]
    assert composer.local_edit.toPlainText() == "shift\n"

    _type(composer.local_edit, "both")
    _type(composer.grok_edit, "side")
    QTest.keyClick(
        composer.grok_edit,
        Qt.Key.Key_Return,
        Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier,
    )
    assert both == [("both", "side")]
    composer.close()


def test_toggle_restores_ctrl_enter_to_send(qapp):
    composer = Composer()
    composer.show()
    qapp.processEvents()
    changed = []
    sent = []
    composer.enter_mode_changed.connect(changed.append)
    composer.send_local.connect(sent.append)
    composer.enter_sends.setChecked(False)
    qapp.processEvents()
    assert changed == [False]
    assert composer.local_edit.enter_submits is False
    assert composer.grok_edit.enter_submits is False
    assert "⌘/Ctrl+Enter sends" in composer.hint.text()
    assert "⌘/Ctrl+Enter to send" in composer.local_edit.placeholderText()

    _type(composer.local_edit, "hello")
    QTest.keyClick(composer.local_edit, Qt.Key.Key_Return)
    assert sent == []
    assert composer.local_edit.toPlainText() == "hello\n"

    _type(composer.local_edit, "send me")
    QTest.keyClick(
        composer.local_edit,
        Qt.Key.Key_Return,
        Qt.KeyboardModifier.MetaModifier,
    )
    assert sent == ["send me"]
    composer.close()


def test_enter_mode_is_saved(tmp_path, qapp):
    db = Database(tmp_path / "maid.db")
    db.migrate()
    window = MainWindow(db)
    window.show()
    qapp.processEvents()
    assert window.composer.enter_sends.isChecked()
    window.composer.enter_sends.setChecked(False)
    qapp.processEvents()
    assert db.get_setting("enter_submits") == "0"
    for session in db.list_sessions():
        if session.status != "closed":
            db.mark_closed(session.id)
    window.close()
    for _ in range(40):
        qapp.processEvents()
        if window._quit_done and not window.isVisible():
            break
    assert window._quit_done
    again = MainWindow(db)
    again.show()
    qapp.processEvents()
    assert not again.composer.enter_sends.isChecked()
    assert again.composer.local_edit.enter_submits is False
    assert again.composer.grok_edit.enter_submits is False
    for session in db.list_sessions():
        if session.status != "closed":
            db.mark_closed(session.id)
    again.close()
    for _ in range(40):
        qapp.processEvents()
        if again._quit_done and not again.isVisible():
            break
    db.close()
