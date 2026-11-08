"""Audit list of proposed and executed host commands."""

from __future__ import annotations

from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from monitor_maid.models import CommandRecord


class CommandDetail(QDialog):
    def __init__(self, record: CommandRecord, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Command")
        self.resize(640, 460)
        layout = QVBoxLayout(self)
        header = (
            f"{record.decision or record.status}  ·  {record.kind}  ·  {record.proposed_at}"
        )
        layout.addWidget(QLabel(header))
        if record.decision_reason:
            reason = QLabel(record.decision_reason)
            reason.setWordWrap(True)
            reason.setObjectName("warning")
            layout.addWidget(reason)
        body = QPlainTextEdit()
        body.setReadOnly(True)
        parts = [record.command, ""]
        if record.exit_code is not None:
            parts.append(f"Exit code: {record.exit_code}")
        if record.stdout:
            parts.append("stdout:\n" + record.stdout)
        if record.stderr:
            parts.append("stderr:\n" + record.stderr)
        if record.grok_review:
            parts.append("Grok review (not shown to the local model):\n" + record.grok_review)
        body.setPlainText("\n".join(parts))
        layout.addWidget(body, 1)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        layout.addWidget(close)


class CommandLog(QWidget):
    def __init__(self):
        super().__init__()
        self._records: list[CommandRecord] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["When", "Decision", "Exit", "Command"])
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.cellDoubleClicked.connect(self._open)
        layout.addWidget(self.table, 1)
        note = QLabel("Double-click a row for stdout, stderr, and any Grok review.")
        note.setObjectName("muted")
        layout.addWidget(note)

    def set_records(self, records: list[CommandRecord]) -> None:
        self._records = list(records)
        self.table.setRowCount(len(records))
        for row, record in enumerate(records):
            when = QTableWidgetItem(record.proposed_at[11:19])
            decision = QTableWidgetItem(record.decision or record.status)
            if record.decision in {"blocked", "rejected"} or record.status in {"failed", "timeout"}:
                decision.setForeground(QColor("#f0a3a8"))
            elif record.decision == "auto":
                decision.setForeground(QColor("#e0b15a"))
            exit_code = "" if record.exit_code is None else str(record.exit_code)
            self.table.setItem(row, 0, when)
            self.table.setItem(row, 1, decision)
            self.table.setItem(row, 2, QTableWidgetItem(exit_code))
            preview = " ".join(record.command.split())
            self.table.setItem(row, 3, QTableWidgetItem(preview[:180]))

    def _open(self, row: int, _column: int) -> None:
        if 0 <= row < len(self._records):
            CommandDetail(self._records[row], self).exec()
