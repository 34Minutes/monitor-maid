"""Local and Grok composers. Both boxes stay visible."""

from __future__ import annotations

from PyQt6.QtCore import QEvent, Qt, pyqtSignal
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)


class _Edit(QPlainTextEdit):
    send = pyqtSignal()
    send_both = pyqtSignal()

    def __init__(self, *, sends_both: bool = False):
        super().__init__()
        self.enter_submits = True
        self.sends_both = sends_both

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        enter = event.key() in {Qt.Key.Key_Return, Qt.Key.Key_Enter}
        mods = event.modifiers()
        ctrl = bool(
            mods & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier)
        )
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        if enter and ctrl and shift:
            if self.sends_both:
                self.send_both.emit()
            return
        if enter and ctrl:
            if self.enter_submits:
                self.insertPlainText("\n")
            else:
                self.send.emit()
            return
        if enter and self.enter_submits and not shift:
            self.send.emit()
            return
        super().keyPressEvent(event)


class Composer(QFrame):
    send_local = pyqtSignal(str)
    send_grok = pyqtSignal(str)
    send_both = pyqtSignal(str, str)
    ask_grok = pyqtSignal()
    interact = pyqtSignal()
    enter_mode_changed = pyqtSignal(bool)

    def __init__(self):
        super().__init__()
        self.setObjectName("composer")
        self.setFrameShape(QFrame.Shape.NoFrame)
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        self.local_edit = _Edit()
        self.local_edit.setObjectName("localInput")
        self.local_edit.setMinimumHeight(90)
        self.grok_edit = _Edit(sends_both=True)
        self.grok_edit.setObjectName("grokInput")
        self.grok_edit.setMinimumHeight(90)
        self.enter_sends = QCheckBox("Enter sends")
        self.enter_sends.setObjectName("enterSends")
        self.enter_sends.setChecked(True)
        self.enter_sends.setToolTip(
            "Applies to both boxes. When on, Enter submits and ⌘/Ctrl+Enter starts a new line. "
            "When off, ⌘/Ctrl+Enter submits and Enter starts a new line."
        )
        local_wrap = QWidget()
        local_col = QVBoxLayout(local_wrap)
        local_col.setContentsMargins(0, 0, 0, 0)
        local_label = QLabel("Local model")
        local_label.setObjectName("dotLocal")
        local_col.addWidget(local_label)
        local_col.addWidget(self.local_edit, 1)
        self.local_button = QPushButton("Ask Local")
        self.local_button.setObjectName("askLocal")
        self.local_button.setToolTip(
            "Send this box to the local model. Grok is not part of this."
        )
        local_col.addWidget(self.local_button)
        local_col.addWidget(self.enter_sends)
        self._grok_column = QWidget()
        self._grok_column.setObjectName("grokColumn")
        grok_col = QVBoxLayout(self._grok_column)
        grok_col.setContentsMargins(0, 0, 0, 0)
        self.grok_label = QLabel("Grok")
        self.grok_label.setObjectName("dotGrok")
        grok_col.addWidget(self.grok_label)
        grok_col.addWidget(self.grok_edit, 1)
        grok_buttons = QHBoxLayout()
        grok_buttons.setContentsMargins(0, 0, 0, 0)
        self.ask_grok_button = QPushButton("Ask Grok")
        self.ask_grok_button.setObjectName("askGrok")
        self.ask_grok_button.setToolTip(
            "Send this box to Grok Build. The local model is not part of this. "
            "Checked history rows are included."
        )
        self.interact_button = QPushButton("Interact with Local")
        self.interact_button.setObjectName("interactLocal")
        self._interact_tip = (
            "Ask Grok for a task the local model should do, run any host commands, "
            "and send those command results back to Grok. Repeats up to Forward N."
        )
        self.interact_button.setToolTip(self._interact_tip)
        grok_buttons.addWidget(self.ask_grok_button)
        grok_buttons.addWidget(self.interact_button)
        grok_buttons.addStretch(1)
        grok_col.addLayout(grok_buttons)
        # Same height as Enter sends, so both editors share the column equally.
        self._grok_pad = QWidget()
        self._grok_pad.setObjectName("grokFooterPad")
        grok_col.addWidget(self._grok_pad)
        self.input_split = QSplitter(Qt.Orientation.Horizontal)
        self.input_split.setObjectName("inputSplit")
        self.input_split.setChildrenCollapsible(False)
        self.input_split.setHandleWidth(8)
        self.input_split.addWidget(local_wrap)
        self.input_split.addWidget(self._grok_column)
        self.input_split.setStretchFactor(0, 1)
        self.input_split.setStretchFactor(1, 1)
        root.addWidget(self.input_split)

        self.local_button.clicked.connect(self._send_local)
        self.ask_grok_button.clicked.connect(self.ask_grok.emit)
        self.interact_button.clicked.connect(self.interact.emit)
        self.local_edit.send.connect(self._send_local)
        self.grok_edit.send.connect(self._send_grok)
        self.grok_edit.send_both.connect(self._send_both)
        self.enter_sends.toggled.connect(self._on_enter_toggled)
        self.enter_sends.installEventFilter(self)
        self._grok_available = True
        self._inputs_enabled = True
        self._interact_enabled = False
        self._syncing_pad = False
        self._apply_enter_mode(True)
        self._apply_input_enabled()
        self._sync_grok_pad()

    def set_enter_submits(self, enabled: bool) -> None:
        """Choose Enter-to-send without treating it as a user toggle."""
        self.enter_sends.blockSignals(True)
        self.enter_sends.setChecked(enabled)
        self.enter_sends.blockSignals(False)
        self._apply_enter_mode(enabled)

    def set_interact_enabled(self, enabled: bool) -> None:
        self._interact_enabled = enabled
        self.interact_button.setEnabled(enabled)
        self.interact_button.setToolTip(
            self._interact_tip
            if enabled
            else (
                "Open a local session, and have the grok command on PATH, "
                "to use Interact with Local."
            )
        )

    def set_grok_available(self, available: bool) -> None:
        self._grok_available = available
        self._apply_input_enabled()

    def set_enabled(self, enabled: bool) -> None:
        self._inputs_enabled = enabled
        self._apply_input_enabled()

    def _on_enter_toggled(self, checked: bool) -> None:
        self._apply_enter_mode(checked)
        self.enter_mode_changed.emit(checked)

    def _apply_enter_mode(self, enter_submits: bool) -> None:
        self.local_edit.enter_submits = enter_submits
        self.grok_edit.enter_submits = enter_submits
        if enter_submits:
            self.local_edit.setPlaceholderText(
                "Message the local model. It cannot see Grok.  "
                "Enter to send, ⌘/Ctrl+Enter for a new line."
            )
            self.grok_edit.setPlaceholderText(
                "Your own question to Grok Build. It is not added to the local model's task. "
                "Enter to send, ⌘/Ctrl+Enter for a new line."
            )
        else:
            self.local_edit.setPlaceholderText(
                "Message the local model. It cannot see Grok.  "
                "⌘/Ctrl+Enter to send, Enter for a new line."
            )
            self.grok_edit.setPlaceholderText(
                "Your own question to Grok Build. It is not added to the local model's task. "
                "⌘/Ctrl+Enter to send, Enter for a new line."
            )

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._sync_grok_pad()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._sync_grok_pad()

    def eventFilter(self, watched, event):  # noqa: N802
        if watched is self.enter_sends and event.type() == QEvent.Type.Resize:
            self._sync_grok_pad()
        return super().eventFilter(watched, event)

    def _sync_grok_pad(self) -> None:
        if self._syncing_pad:
            return
        height = self.enter_sends.height() or self.enter_sends.sizeHint().height()
        if height <= 0 or self._grok_pad.height() == height:
            return
        self._syncing_pad = True
        try:
            self._grok_pad.setFixedHeight(height)
        finally:
            self._syncing_pad = False

    def _apply_input_enabled(self) -> None:
        self.local_edit.setEnabled(self._inputs_enabled)
        self.local_button.setEnabled(self._inputs_enabled)
        self.enter_sends.setEnabled(self._inputs_enabled)
        grok_on = self._inputs_enabled and self._grok_available
        self.grok_edit.setEnabled(grok_on)
        self.ask_grok_button.setEnabled(grok_on)
        self.interact_button.setEnabled(self._interact_enabled)

    def _send_local(self) -> None:
        text = self.local_edit.toPlainText()
        if not text.strip():
            return
        self.local_edit.clear()
        self.send_local.emit(text)

    def _send_grok(self) -> None:
        text = self.grok_edit.toPlainText()
        if not text.strip():
            return
        self.grok_edit.clear()
        self.send_grok.emit(text)

    def _send_both(self) -> None:
        local = self.local_edit.toPlainText()
        grok = self.grok_edit.toPlainText()
        if not local.strip() and not grok.strip():
            return
        self.local_edit.clear()
        self.grok_edit.clear()
        self.send_both.emit(local, grok)
