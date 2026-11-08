"""Scrollable transcript. Each message is shown in full, in plain text."""

from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QWheelEvent
from PyQt6.QtWidgets import (
    QFrame,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from monitor_maid.models import Turn, display_actor

_SPECIAL = {
    "summary",
    "conclusion",
    "command_result",
    "error",
    "review",
    "verification",
    "refusal",
    "relay",
}


def _role(turn: Turn) -> str:
    if turn.kind in _SPECIAL:
        return turn.kind
    return turn.actor


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
        self.setObjectName("bubble")
        self.setProperty("role", _role(turn))
        self.style().unpolish(self)
        self.style().polish(self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(4)
        label = display_actor(turn.actor, turn.kind, turn.visible_to_local)
        note = ""
        if not turn.visible_to_local:
            note = "  ·  not visible to the local model"
        self.meta = QLabel(f"{label}   {turn.created_at}{note}")
        self.meta.setObjectName("meta")
        self.meta.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.body = FullText(turn.content)
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


class TranscriptView(QScrollArea):
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

    def _on_range(self, _minimum: int, _maximum: int) -> None:
        if self._stick and not self._adjusting:
            self._to_bottom()

    def set_turns(self, turns: list[Turn]) -> None:
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
        self._on_scroll(self.verticalScrollBar().value())

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
