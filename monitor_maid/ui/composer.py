"""Local and Grok composers. The Grok pane is hidden until monitor mode is on."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
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

_HINT_ENTER_SENDS = (
    "Enter sends. ⌘/Ctrl+Enter adds a line. ⌘/Ctrl+Shift+Enter sends both."
)
_HINT_CHORD_SENDS = (
    "⌘/Ctrl+Enter sends. Enter adds a line. ⌘/Ctrl+Shift+Enter sends both."
)


class _Edit(QPlainTextEdit):
    send = pyqtSignal()
    send_both = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.enter_submits = True

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        enter = event.key() in {Qt.Key.Key_Return, Qt.Key.Key_Enter}
        mods = event.modifiers()
        ctrl = bool(
            mods & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier)
        )
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        if enter and ctrl and shift:
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
        self.grok_edit = _Edit()
        self.grok_edit.setObjectName("grokInput")
        self.grok_edit.setMinimumHeight(90)
        self.enter_sends = QCheckBox("Enter sends")
        self.enter_sends.setObjectName("enterSends")
        self.enter_sends.setChecked(True)
        self.enter_sends.setToolTip(
            "When on, Enter submits and ⌘/Ctrl+Enter starts a new line. "
            "When off, ⌘/Ctrl+Enter submits and Enter starts a new line."
        )
        local_wrap = QWidget()
        local_col = QVBoxLayout(local_wrap)
        local_col.setContentsMargins(0, 0, 0, 0)
        local_label = QLabel("Local model")
        local_label.setObjectName("dotLocal")
        local_col.addWidget(local_label)
        local_row = QHBoxLayout()
        local_row.setContentsMargins(0, 0, 0, 0)
        local_row.addWidget(self.local_edit, 1)
        local_row.addWidget(self.enter_sends, 0, Qt.AlignmentFlag.AlignVCenter)
        local_col.addLayout(local_row)
        self._grok_column = QWidget()
        self._grok_column.setObjectName("grokColumn")
        grok_col = QVBoxLayout(self._grok_column)
        grok_col.setContentsMargins(0, 0, 0, 0)
        self.grok_label = QLabel("Grok")
        self.grok_label.setObjectName("dotGrok")
        grok_col.addWidget(self.grok_label)
        grok_col.addWidget(self.grok_edit)
        self.input_split = QSplitter(Qt.Orientation.Horizontal)
        self.input_split.setObjectName("inputSplit")
        self.input_split.setChildrenCollapsible(False)
        self.input_split.setHandleWidth(8)
        self.input_split.addWidget(local_wrap)
        self.input_split.setStretchFactor(0, 1)
        self._grok_column.setParent(self)
        self._grok_column.hide()
        self.grok_edit.hide()
        root.addWidget(self.input_split)

        buttons = QHBoxLayout()
        self.local_button = QPushButton("Send to local")
        self.local_button.setObjectName("primary")
        self.grok_button = QPushButton("Send to Grok")
        self.both_button = QPushButton("Send to both")
        self.hint = QLabel(_HINT_ENTER_SENDS)
        self.hint.setObjectName("muted")
        self._grok_hint_ready = (
            "To ask Grok something of your own, turn on Monitor mode and type in the Grok box. "
            "That exchange stays off the local model's record. "
            "History → Ask Grok is only for items you have checked."
        )
        self._grok_hint_nokey = (
            "Grok is off because there is no API key. "
            "Add one in Settings, or set XAI_API_KEY, and save. "
            "Grok buttons stay disabled until a key is available."
        )
        self.grok_hint = QLabel(self._grok_hint_ready)
        self.grok_hint.setObjectName("muted")
        self.grok_hint.setWordWrap(True)
        buttons.addWidget(self.local_button)
        buttons.addWidget(self.grok_button)
        buttons.addWidget(self.both_button)
        buttons.addStretch(1)
        buttons.addWidget(self.hint)
        root.addLayout(buttons)
        root.addWidget(self.grok_hint)

        self.local_button.clicked.connect(self._send_local)
        self.grok_button.clicked.connect(self._send_grok)
        self.both_button.clicked.connect(self._send_both)
        self.local_edit.send.connect(self._send_local)
        self.grok_edit.send.connect(self._send_grok)
        self.local_edit.send_both.connect(self._send_both)
        self.grok_edit.send_both.connect(self._send_both)
        self.enter_sends.toggled.connect(self._on_enter_toggled)
        self._monitor = False
        self._grok_available = True
        self._inputs_enabled = True
        self._apply_enter_mode(True)
        self.set_monitor(False)

    def set_enter_submits(self, enabled: bool) -> None:
        """Choose Enter-to-send without treating it as a user toggle."""
        self.enter_sends.blockSignals(True)
        self.enter_sends.setChecked(enabled)
        self.enter_sends.blockSignals(False)
        self._apply_enter_mode(enabled)

    def set_monitor(self, enabled: bool) -> None:
        self._monitor = enabled
        self.grok_button.setVisible(enabled)
        self.both_button.setVisible(enabled)
        self.local_button.setText("Send to local" if enabled else "Send")
        self._place_grok_column(enabled)
        self._refresh_grok_hint()
        self._apply_input_enabled()

    def _place_grok_column(self, enabled: bool) -> None:
        """Keep the local box full width until Monitor mode shows Grok.

        A hidden column left in the row still takes half the width. Monitor
        mode puts Grok back, with a handle so either box can be dragged wider.
        """
        in_split = self.input_split.indexOf(self._grok_column) >= 0
        if enabled:
            if not in_split:
                self.input_split.addWidget(self._grok_column)
                self.input_split.setStretchFactor(1, 1)
            self._grok_column.setVisible(True)
            self.grok_edit.setVisible(True)
            self.grok_label.setVisible(True)
            width = max(self.input_split.width(), 2)
            self.input_split.setSizes([width // 2, width // 2])
        else:
            self.grok_edit.hide()
            self.grok_label.hide()
            self._grok_column.hide()
            if in_split:
                self._grok_column.setParent(self)

    def set_grok_available(self, available: bool) -> None:
        self._grok_available = available
        self._refresh_grok_hint()
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
                "Your own question to Grok. It is not added to the local model's task. "
                "Grok can still see the local transcript.  "
                "Enter to send, ⌘/Ctrl+Enter for a new line."
            )
            self.hint.setText(_HINT_ENTER_SENDS)
        else:
            self.local_edit.setPlaceholderText(
                "Message the local model. It cannot see Grok.  "
                "⌘/Ctrl+Enter to send, Enter for a new line."
            )
            self.grok_edit.setPlaceholderText(
                "Your own question to Grok. It is not added to the local model's task. "
                "Grok can still see the local transcript.  "
                "⌘/Ctrl+Enter to send, Enter for a new line."
            )
            self.hint.setText(_HINT_CHORD_SENDS)

    def _refresh_grok_hint(self) -> None:
        if not self._grok_available:
            self.grok_hint.setText(self._grok_hint_nokey)
            self.grok_hint.setVisible(True)
            return
        self.grok_hint.setText(self._grok_hint_ready)
        self.grok_hint.setVisible(not self._monitor)

    def _apply_input_enabled(self) -> None:
        self.local_edit.setEnabled(self._inputs_enabled)
        self.local_button.setEnabled(self._inputs_enabled)
        self.enter_sends.setEnabled(self._inputs_enabled)
        grok_on = self._inputs_enabled and self._grok_available
        self.grok_edit.setEnabled(grok_on)
        self.grok_button.setEnabled(grok_on)
        self.both_button.setEnabled(grok_on)

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
