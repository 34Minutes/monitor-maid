"""Scrollable transcript. Each message is shown in full, in plain text."""

from __future__ import annotations

import re
from dataclasses import dataclass

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QIcon,
    QKeyEvent,
    QPainter,
    QPixmap,
    QTextCharFormat,
    QTextCursor,
    QWheelEvent,
)
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStyle,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from monitor_maid.grok_build import strip_grok_status
from monitor_maid.models import Turn, display_actor

_SPECIAL = {
    "summary",
    "prompt",
    "conclusion",
    "command_result",
    "error",
    "review",
    "verification",
    "refusal",
    "relay",
}


def _role(turn: Turn) -> str:
    if turn.metadata.get("seed"):
        source = str(turn.metadata.get("source_kind") or "")
        if source in {"summary", "grok", "preamble"}:
            return "summary"
        if source in {"prompt", "conclusion"}:
            return "prompt"
    if turn.kind in _SPECIAL:
        return turn.kind
    return turn.actor


@dataclass(frozen=True)
class TextMatch:
    """One search hit in a transcript bubble."""

    turn_id: str
    start: int
    end: int


def find_spans(text: str, query: str, *, regex: bool) -> list[tuple[int, int]]:
    """Return non-empty match ranges for `query`.

    An empty query matches nothing. Regex search is case-insensitive, and
    `^` and `$` match each line. `.` does not cross a newline. A match of
    length zero is skipped so a pattern such as `a*` cannot stall.
    `re.error` is left for the caller when `regex` is set and the pattern
    does not compile.
    """
    if query == "":
        return []
    flags = re.IGNORECASE
    if regex:
        flags |= re.MULTILINE
        pattern = query
    else:
        pattern = re.escape(query)
    compiled = re.compile(pattern, flags)
    spans: list[tuple[int, int]] = []
    for match in compiled.finditer(text):
        start, end = match.span()
        if end <= start:
            continue
        spans.append((start, end))
    return spans


class FullText(QPlainTextEdit):
    """Read-only message body that grows to its text instead of scrolling inside."""

    def __init__(self, text: str):
        super().__init__()
        self.setReadOnly(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.setPlainText(text)
        self._fitting = False

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        event.ignore()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.fit(self.viewport().width())

    def fit(self, width: int) -> None:
        if self._fitting or width <= 0:
            return
        self._fitting = True
        document = self.document()
        document.setTextWidth(width)
        height = self._text_height()
        if self.height() != height:
            self.setFixedHeight(height)
        self._fitting = False

    def _text_height(self) -> int:
        """Pixel height of every line.

        QPlainTextEdit's document.size().height() is the block count, not pixels,
        so a long host result was given a few dozen pixels and then clipped.
        """
        document = self.document()
        layout = document.documentLayout()
        line = self.fontMetrics().lineSpacing()
        total = 0.0
        block = document.begin()
        while block.isValid():
            rect = layout.blockBoundingRect(block).height()
            total += rect if rect > 0 else line
            block = block.next()
        if total <= 0:
            total = line
        margin = int(document.documentMargin() * 2)
        return max(24, int(total) + margin + 6)


class Bubble(QFrame):
    def __init__(self, turn: Turn):
        super().__init__()
        self.turn_id = turn.id
        self.setObjectName("bubble")
        self.setProperty("role", _role(turn))
        self.style().unpolish(self)
        self.style().polish(self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(4)
        label = display_actor(turn.actor, turn.kind, turn.visible_to_local, turn.metadata)
        note = ""
        if not turn.visible_to_local:
            note = "  ·  not visible to the local model"
        self.meta = QLabel(f"{label}   {turn.created_at}{note}")
        self.meta.setObjectName("meta")
        self.meta.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.body = FullText(strip_grok_status(turn.content))
        layout.addWidget(self.meta)
        layout.addWidget(self.body)

    def fit(self, width: int) -> None:
        margins = self.layout().contentsMargins()
        self.body.fit(width - margins.left() - margins.right())
        needed = (
            margins.top()
            + margins.bottom()
            + self.layout().spacing()
            + self.meta.sizeHint().height()
            + self.body.height()
        )
        self.setMinimumHeight(needed)
        self.setMaximumHeight(needed)

    def apply_highlights(self, spans: list[tuple[int, int, bool]]) -> None:
        """Paint search hits. The current hit uses the stronger colour."""
        selections = []
        for start, end, current in spans:
            selection = QTextEdit.ExtraSelection()
            cursor = self.body.textCursor()
            cursor.setPosition(start)
            cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
            colour = QColor("#e0b15a" if current else "#5c4a28")
            ink = QColor("#1a1408" if current else "#fff8ef")
            fmt = QTextCharFormat()
            fmt.setBackground(colour)
            fmt.setForeground(ink)
            selection.cursor = cursor
            selection.format = fmt
            selections.append(selection)
        self.body.setExtraSelections(selections)


class TranscriptView(QScrollArea):
    # The turn under the middle of the viewport, when the user scrolls or a
    # search jump moves the chat. History listens and centers that row.
    centered_turn = pyqtSignal(str)
    # index is -1 when nothing is current. error is empty, or "Invalid pattern".
    matches_changed = pyqtSignal(int, int, str)
    def __init__(self):
        super().__init__()
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self._host = QWidget()
        self._host.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self._layout = QVBoxLayout(self._host)
        self._layout.setContentsMargins(8, 8, 8, 8)
        self._layout.setSpacing(8)
        self._layout.addStretch(1)
        self.setWidget(self._host)
        self._stick = True
        self._adjusting = False
        self._live: Bubble | None = None
        self._announced_turn = ""
        self._query = ""
        self._regex = False
        self._matches: list[TextMatch] = []
        self._match_index = -1
        self.follow_button = QPushButton("Follow latest", self)
        self.follow_button.setObjectName("primary")
        self.follow_button.setToolTip("Jump to the newest message and keep following new ones.")
        self.follow_button.hide()
        self.follow_button.clicked.connect(self.follow_latest)
        bar = self.verticalScrollBar()
        bar.valueChanged.connect(self._on_scroll)
        bar.rangeChanged.connect(self._on_range)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._place_follow()
        self._fit_bubbles()

    def follow_latest(self) -> None:
        self._stick = True
        self._to_bottom()
        self.follow_button.hide()

    def _on_scroll(self, value: int) -> None:
        if self._adjusting:
            return
        bar = self.verticalScrollBar()
        stick = bar.maximum() - value < 48
        self._stick = stick
        self.follow_button.setVisible(not stick and bar.maximum() > 0)
        if self.follow_button.isVisible():
            self._place_follow()
        self._announce_center()

    def _on_range(self, _minimum: int, _maximum: int) -> None:
        if self._stick and not self._adjusting:
            self._to_bottom()

    def set_live(self, text: str) -> None:
        """Show grok output while the process is still running.

        The bubble is replaced when the finished turn is loaded from the database.
        """
        text = strip_grok_status(text)
        if self._live is None:
            turn = Turn(
                id="live",
                session_id="",
                seq=0,
                actor="grok",
                kind="message",
                content=text,
                created_at="",
                visible_to_local=False,
            )
            bubble = Bubble(turn)
            bubble.meta.setText("Grok   running  ·  not visible to the local model")
            self._live = bubble
            slot = max(0, self._layout.count() - 1)
            self._layout.insertWidget(slot, bubble)
        else:
            self._live.body.setPlainText(text)
        self._stick = True
        self._fit_bubbles()
        self._to_bottom()
        self._reapply_search(reveal=False)
        self._on_scroll(self.verticalScrollBar().value())

    def set_turns(self, turns: list[Turn]) -> None:
        self._live = None
        self._announced_turn = ""
        bar = self.verticalScrollBar()
        previous = bar.value()
        stick = self._stick
        self._adjusting = True
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        if not turns:
            empty = QLabel(
                "No messages yet.\n"
                "The left-hand conversation is the only one the local model can see."
            )
            empty.setObjectName("muted")
            empty.setWordWrap(True)
            self._layout.addWidget(empty)
        else:
            for turn in turns:
                self._layout.addWidget(Bubble(turn))
        self._layout.addStretch(1)
        self._adjusting = False
        QTimer.singleShot(0, lambda stick=stick, previous=previous: self._settle(stick, previous))

    def _settle(self, stick: bool, previous: int) -> None:
        self._fit_bubbles()
        if stick:
            self._to_bottom()
        else:
            self._adjusting = True
            bar = self.verticalScrollBar()
            bar.setValue(min(previous, bar.maximum()))
            self._adjusting = False
        self._reapply_search(reveal=False)
        self._on_scroll(self.verticalScrollBar().value())

    def turn_ids(self) -> list[str]:
        return [bubble.turn_id for bubble in self._bubbles()]

    def center_turn_id(self) -> str | None:
        """The bubble that contains the middle of the viewport, or the nearest."""
        bubbles = self._bubbles()
        if not bubbles:
            return None
        center = self.verticalScrollBar().value() + (self.viewport().height() / 2)
        containing: Bubble | None = None
        nearest: Bubble | None = None
        nearest_distance: float | None = None
        for bubble in bubbles:
            top = bubble.y()
            bottom = top + bubble.height()
            if top <= center <= bottom:
                containing = bubble
                break
            distance = min(abs(center - top), abs(center - bottom))
            if nearest_distance is None or distance < nearest_distance:
                nearest_distance = distance
                nearest = bubble
        chosen = containing or nearest
        return chosen.turn_id if chosen is not None else None

    def turn_viewport_delta(self, turn_id: str) -> int | None:
        """Pixels from the viewport center to the bubble center. None if missing."""
        bubble = self._bubble_for(turn_id)
        if bubble is None:
            return None
        bubble_center = bubble.y() + (bubble.height() / 2)
        viewport_center = self.verticalScrollBar().value() + (self.viewport().height() / 2)
        return int(round(bubble_center - viewport_center))

    def scroll_to_turn(self, turn_id: str) -> None:
        """Put the bubble's middle on the viewport middle, clamped to the ends."""
        bubble = self._bubble_for(turn_id)
        if bubble is None:
            return
        self._scroll_host_y_to_center(bubble.y() + (bubble.height() / 2))

    def set_query(self, query: str, regex: bool) -> None:
        self._query = query
        self._regex = regex
        self._match_index = 0
        self._reapply_search(reveal=True)

    def step_match(self, delta: int) -> None:
        if not self._matches:
            return
        if self._match_index < 0:
            self._match_index = 0
        else:
            self._match_index = (self._match_index + delta) % len(self._matches)
        self._paint_matches()
        self._reveal_match(self._match_index)
        self.matches_changed.emit(self._match_index, len(self._matches), "")

    def _announce_center(self) -> None:
        turn_id = self.center_turn_id()
        if not turn_id or turn_id == self._announced_turn:
            return
        self._announced_turn = turn_id
        self.centered_turn.emit(turn_id)

    def _scroll_host_y_to_center(self, host_y: float) -> None:
        bar = self.verticalScrollBar()
        target = int(round(host_y - (self.viewport().height() / 2)))
        target = max(bar.minimum(), min(target, bar.maximum()))
        self._adjusting = True
        bar.setValue(target)
        self._adjusting = False
        self._on_scroll(bar.value())

    def _bubbles(self) -> list[Bubble]:
        found: list[Bubble] = []
        for index in range(self._layout.count()):
            widget = self._layout.itemAt(index).widget()
            if isinstance(widget, Bubble):
                found.append(widget)
        return found

    def _bubble_for(self, turn_id: str) -> Bubble | None:
        for bubble in self._bubbles():
            if bubble.turn_id == turn_id:
                return bubble
        return None

    def _reapply_search(self, *, reveal: bool) -> None:
        error = ""
        try:
            self._matches = self._collect_matches()
        except re.error:
            self._matches = []
            error = "Invalid pattern"
        if not self._matches:
            self._match_index = -1
        elif self._match_index < 0 or self._match_index >= len(self._matches):
            self._match_index = 0
        self._paint_matches()
        if reveal and self._match_index >= 0:
            self._reveal_match(self._match_index)
        self.matches_changed.emit(self._match_index, len(self._matches), error)

    def _collect_matches(self) -> list[TextMatch]:
        if self._query == "":
            return []
        found: list[TextMatch] = []
        for bubble in self._bubbles():
            for start, end in find_spans(bubble.body.toPlainText(), self._query, regex=self._regex):
                found.append(TextMatch(bubble.turn_id, start, end))
        return found

    def _paint_matches(self) -> None:
        grouped: dict[str, list[tuple[int, int, bool]]] = {}
        for index, match in enumerate(self._matches):
            grouped.setdefault(match.turn_id, []).append(
                (match.start, match.end, index == self._match_index)
            )
        for bubble in self._bubbles():
            bubble.apply_highlights(grouped.get(bubble.turn_id, []))

    def _reveal_match(self, index: int) -> None:
        match = self._matches[index]
        bubble = self._bubble_for(match.turn_id)
        if bubble is None:
            return
        cursor = bubble.body.textCursor()
        cursor.setPosition(min(match.start, len(bubble.body.toPlainText())))
        rect = bubble.body.cursorRect(cursor)
        point = bubble.body.viewport().mapTo(self._host, rect.center())
        self._scroll_host_y_to_center(point.y())

    def _fit_bubbles(self) -> None:
        margins = self._layout.contentsMargins()
        width = self.viewport().width() - margins.left() - margins.right()
        total = margins.top() + margins.bottom()
        count = self._layout.count()
        if count:
            total += self._layout.spacing() * (count - 1)
        for index in range(count):
            widget = self._layout.itemAt(index).widget()
            if isinstance(widget, Bubble):
                widget.fit(width)
                total += widget.minimumHeight()
            elif widget is not None:
                total += widget.sizeHint().height()
        self._host.setMinimumHeight(total)

    def _to_bottom(self) -> None:
        bar = self.verticalScrollBar()
        self._adjusting = True
        bar.setValue(bar.maximum())
        self._adjusting = False

    def _place_follow(self) -> None:
        button = self.follow_button
        button.adjustSize()
        origin = self.viewport().pos()
        x = origin.x() + max(12, (self.viewport().width() - button.width()) // 2)
        y = origin.y() + self.viewport().height() - button.height() - 12
        button.move(x, y)
        button.raise_()


class _SearchEdit(QLineEdit):
    """Enter moves to the next hit. Shift+Enter moves to the previous one."""

    step = pyqtSignal(int)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() in {Qt.Key.Key_Return, Qt.Key.Key_Enter}:
            shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            self.step.emit(-1 if shift else 1)
            return
        super().keyPressEvent(event)


def _regex_icon() -> QIcon:
    pixmap = QPixmap(22, 16)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setPen(QColor("#e7e9ee"))
    font = painter.font()
    font.setPixelSize(12)
    font.setBold(True)
    painter.setFont(font)
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, ".*")
    painter.end()
    return QIcon(pixmap)


class ChatSearchBar(QFrame):
    """Search the chat. The count and arrows walk the hits in order."""

    query_changed = pyqtSignal(str, bool)
    step_requested = pyqtSignal(int)

    def __init__(self):
        super().__init__()
        self.setObjectName("chatSearchBar")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(6)
        self.edit = _SearchEdit()
        self.edit.setObjectName("chatSearch")
        self.edit.setPlaceholderText("Search chat")
        self.edit.setClearButtonEnabled(True)
        self.edit.setToolTip("Search the chat. Enter jumps to the next match.")
        self.regex = QToolButton()
        self.regex.setObjectName("chatSearchRegex")
        self.regex.setCheckable(True)
        self.regex.setIcon(_regex_icon())
        self.regex.setToolTip(
            "Regular expression. Case is ignored. ^ and $ match a line. "
            "Dot does not match a new line."
        )
        self.regex.setAutoRaise(False)
        self.previous = QToolButton()
        self.previous.setObjectName("chatSearchPrevious")
        self.previous.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowUp))
        self.previous.setToolTip("Previous match")
        self.previous.setAutoRaise(False)
        self.next = QToolButton()
        self.next.setObjectName("chatSearchNext")
        self.next.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowDown))
        self.next.setToolTip("Next match")
        self.next.setAutoRaise(False)
        self.count = QLabel("")
        self.count.setObjectName("chatSearchCount")
        self.count.setMinimumWidth(110)
        for button in (self.regex, self.previous, self.next):
            button.setFixedSize(28, 28)
        layout.addWidget(self.edit, 1)
        layout.addWidget(self.regex)
        layout.addWidget(self.previous)
        layout.addWidget(self.next)
        layout.addWidget(self.count)
        self.edit.textChanged.connect(self._emit_query)
        self.edit.step.connect(self.step_requested.emit)
        self.regex.toggled.connect(lambda _checked: self._emit_query())
        self.previous.clicked.connect(lambda: self.step_requested.emit(-1))
        self.next.clicked.connect(lambda: self.step_requested.emit(1))
        self.set_match_status(-1, 0, "")

    def _emit_query(self) -> None:
        self.query_changed.emit(self.edit.text(), self.regex.isChecked())

    def set_match_status(self, index: int, count: int, error: str) -> None:
        if error:
            self.count.setText(error)
        elif self.edit.text() == "":
            self.count.setText("")
        elif count <= 0:
            self.count.setText("No matches")
        else:
            self.count.setText(f"{index + 1} of {count}")
        self.previous.setEnabled(count > 0 and not error)
        self.next.setEnabled(count > 0 and not error)
