"""Local and Grok composers. The Grok pane is hidden until monitor mode is on."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)


class _Edit(QPlainTextEdit):
    send = pyqtSignal()
    send_both = pyqtSignal()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        enter = event.key() in {Qt.Key.Key_Return, Qt.Key.Key_Enter}
        mods = event.modifiers()
        if enter and mods & Qt.KeyboardModifier.ShiftModifier and mods & (
            Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier
        ):
            self.send_both.emit()
            return
        if enter and mods & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier):
            self.send.emit()
            return
        super().keyPressEvent(event)


class Composer(QFrame):
    send_local = pyqtSignal(str)
    send_grok = pyqtSignal(str)
    send_both = pyqtSignal(str, str)

    def __init__(self):
        super().__init__()
        self.setObjectName("composer")
        self.setFrameShape(QFrame.Shape.NoFrame)
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        panes = QHBoxLayout()
        self.local_edit = _Edit()
        self.local_edit.setObjectName("localInput")
        self.local_edit.setPlaceholderText(
            "Message the local model. It cannot see Grok.  ⌘/Ctrl+Enter to send."
        )
        self.local_edit.setMinimumHeight(90)
        self.grok_edit = _Edit()
        self.grok_edit.setObjectName("grokInput")
        self.grok_edit.setPlaceholderText(
            "Your own question to Grok. It is not added to the local model's task. "
            "Grok can still see the local transcript.  ⌘/Ctrl+Enter to send."
        )
        self.grok_edit.setMinimumHeight(90)
        local_col = QVBoxLayout()
        local_label = QLabel("Local model")
        local_label.setObjectName("dotLocal")
        local_col.addWidget(local_label)
        local_col.addWidget(self.local_edit)
        grok_col = QVBoxLayout()
        self.grok_label = QLabel("Grok")
        self.grok_label.setObjectName("dotGrok")
        grok_col.addWidget(self.grok_label)
        grok_col.addWidget(self.grok_edit)
        self._grok_col_widgets = (self.grok_label, self.grok_edit)
        panes.addLayout(local_col, 1)
        panes.addLayout(grok_col, 1)
        root.addLayout(panes)

        buttons = QHBoxLayout()
        self.local_button = QPushButton("Send to local")
        self.local_button.setObjectName("primary")
        self.grok_button = QPushButton("Send to Grok")
        self.both_button = QPushButton("Send to both")
        self.hint = QLabel("⌘/Ctrl+Enter sends the focused pane. ⌘/Ctrl+Shift+Enter sends both.")
        self.hint.setObjectName("muted")
        self.grok_hint = QLabel(
            "To ask Grok something of your own, turn on Monitor mode and type in the Grok box. "
            "That exchange stays off the local model's record. "
            "History → Ask Grok is only for items you have checked."
        )
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
        self.set_monitor(False)

    def set_monitor(self, enabled: bool) -> None:
        for widget in self._grok_col_widgets:
            widget.setVisible(enabled)
        self.grok_button.setVisible(enabled)
        self.both_button.setVisible(enabled)
        self.hint.setVisible(enabled)
        self.grok_hint.setVisible(not enabled)
        self.local_button.setText("Send to local" if enabled else "Send")

    def set_enabled(self, enabled: bool) -> None:
        self.local_edit.setEnabled(enabled)
        self.grok_edit.setEnabled(enabled)
        self.local_button.setEnabled(enabled)
        self.grok_button.setEnabled(enabled)
        self.both_button.setEnabled(enabled)

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
