"""Main window: transcript, dual composer, history, and session controls."""

from __future__ import annotations

import os

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QTextDocument
from PyQt6.QtPrintSupport import QPrintDialog, QPrinter
from PyQt6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QSpinBox,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from monitor_maid.config import Settings, effective_api_key
from monitor_maid.context import build_history_items
from monitor_maid.db import Database
from monitor_maid.export import selection_to_markdown, session_to_html, session_to_markdown, session_to_plaintext
from monitor_maid.models import MODE_LABELS
from monitor_maid.orchestrator import Orchestrator
from monitor_maid.paths import process_log_dir
from monitor_maid.ui.command_log import CommandLog
from monitor_maid.ui.composer import Composer
from monitor_maid.ui.dialogs import AskDialog, RecoveryDialog, SettingsDialog
from monitor_maid.ui.history_panel import HistoryPanel
from monitor_maid.ui.hooks import WindowHooks
from monitor_maid.ui.transcript import TranscriptView


class MainWindow(QMainWindow):
    def __init__(self, db: Database):
        super().__init__()
        self.db = db
        self.setWindowTitle("Monitor Maid")
        self.resize(1240, 800)
        self._quit_done = False
        self._quitting = False
        self._loading = False
        self.hooks = WindowHooks(lambda: self)
        self.orchestrator = Orchestrator(
            db,
            self.hooks,
            cwd=os.getcwd(),
            process_log_dir=process_log_dir(),
        )
        self._build()
        self._wire()
        QTimer.singleShot(0, self._startup)

    def _build(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(10, 10, 10, 6)
        root.setSpacing(8)

        strip = QFrame()
        strip.setObjectName("strip")
        strip_layout = QVBoxLayout(strip)
        strip_layout.setContentsMargins(8, 8, 8, 8)
        row1 = QHBoxLayout()
        self.session_combo = QComboBox()
        self.session_combo.setObjectName("sessionCombo")
        self.session_combo.setMinimumWidth(260)
        self.new_button = QPushButton("New")
        self.end_button = QPushButton("End session")
        self.reopen_button = QPushButton("Reopen")
        self.settings_button = QPushButton("Settings")
        row1.addWidget(QLabel("Session"))
        row1.addWidget(self.session_combo, 1)
        row1.addWidget(self.new_button)
        row1.addWidget(self.end_button)
        row1.addWidget(self.reopen_button)
        row1.addWidget(self.settings_button)
        row2 = QHBoxLayout()
        self.exec_mode = QComboBox()
        self.exec_limit = QSpinBox()
        self.exec_limit.setRange(0, 100)
        self.relay_mode = QComboBox()
        self.relay_limit = QSpinBox()
        self.relay_limit.setRange(0, 100)
        for mode, label in MODE_LABELS.items():
            self.exec_mode.addItem(label, mode)
            self.relay_mode.addItem(label, mode)
        self.monitor_button = QPushButton("Monitor mode")
        self.monitor_button.setCheckable(True)
        self._monitor_tip = (
            "Show a box where you can write to Grok yourself. "
            "Grok sees the local transcript. The local model does not see your note or Grok's reply."
        )
        self.monitor_button.setToolTip(self._monitor_tip)
        self.export_button = QPushButton("Export")
        self.print_button = QPushButton("Print")
        self.retry_button = QPushButton("Retry")
        self.used_label = QLabel("")
        self.used_label.setObjectName("muted")
        row2.addWidget(QLabel("Run"))
        row2.addWidget(self.exec_mode)
        row2.addWidget(QLabel("N"))
        row2.addWidget(self.exec_limit)
        row2.addWidget(QLabel("Forward"))
        row2.addWidget(self.relay_mode)
        row2.addWidget(QLabel("N"))
        row2.addWidget(self.relay_limit)
        row2.addWidget(self.used_label)
        row2.addStretch(1)
        row2.addWidget(self.monitor_button)
        row2.addWidget(self.export_button)
        row2.addWidget(self.print_button)
        row2.addWidget(self.retry_button)
        strip_layout.addLayout(row1)
        strip_layout.addLayout(row2)
        root.addWidget(strip)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        legend = QFrame()
        legend.setObjectName("legend")
        legend_row = QHBoxLayout(legend)
        legend_row.setContentsMargins(8, 4, 8, 4)
        for object_name, text in (
            ("dotUser", "● You"),
            ("dotLocal", "● Local model"),
            ("dotGrok", "● Grok (hidden from local)"),
            ("dotHost", "● Host"),
        ):
            dot = QLabel(text)
            dot.setObjectName(object_name)
            legend_row.addWidget(dot)
        legend_row.addStretch(1)
        self.transcript = TranscriptView()
        self.closed_banner = QLabel("This session is closed.")
        self.closed_banner.setObjectName("warning")
        self.pending_label = QLabel("")
        self.pending_label.setObjectName("muted")
        self.pending_label.setWordWrap(True)
        self.ollama_hint = QLabel("")
        self.ollama_hint.setObjectName("warning")
        self.ollama_hint.setWordWrap(True)
        self.ollama_hint.hide()
        self.composer = Composer()
        left_layout.addWidget(legend)
        left_layout.addWidget(self.transcript, 1)
        left_layout.addWidget(self.closed_banner)
        left_layout.addWidget(self.ollama_hint)
        left_layout.addWidget(self.pending_label)
        left_layout.addWidget(self.composer)
        self.history = HistoryPanel()
        self.commands = CommandLog()
        tabs = QTabWidget()
        tabs.addTab(self.history, "History")
        tabs.addTab(self.commands, "Commands")
        splitter.addWidget(left)
        splitter.addWidget(tabs)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        root.addWidget(splitter, 1)

        status = QStatusBar()
        self.setStatusBar(status)
        self.status_label = QLabel("Ready")
        self.ollama_label = QLabel("Ollama: …")
        self.queue_label = QLabel("Queue: 0")
        status.addWidget(self.status_label, 1)
        status.addPermanentWidget(self.queue_label)
        status.addPermanentWidget(self.ollama_label)

        self.progress = QProgressDialog("Ending the session…", "Skip summary", 0, 0, self)
        self.progress.setWindowTitle("Monitor Maid")
        self.progress.setMinimumDuration(0)
        self.progress.setAutoClose(False)
        self.progress.setAutoReset(False)
        self.progress.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.progress.setMinimumDuration(86_400_000)
        self.progress.reset()
        self.progress.hide()
        self.closed_banner.hide()

    def _wire(self) -> None:
        orch = self.orchestrator
        orch.transcript_changed.connect(self._reload_transcript)
        orch.sessions_changed.connect(self._reload_sessions)
        orch.session_changed.connect(self._reload_session_controls)
        orch.commands_changed.connect(self._reload_commands)
        orch.status_message.connect(self.status_label.setText)
        orch.queue_depth.connect(lambda n: self.queue_label.setText(f"Queue: {n}"))
        orch.pending_changed.connect(self._show_pending)
        orch.ollama_status.connect(self._show_ollama)
        orch.busy_changed.connect(self._show_busy)
        orch.accepting_changed.connect(self._apply_accepting)
        orch.shutdown_active.connect(self._show_shutdown)
        orch.shutdown_progress.connect(self.progress.setLabelText)
        self.session_combo.currentIndexChanged.connect(self._session_picked)
        self.new_button.clicked.connect(self._new_session)
        self.end_button.clicked.connect(self._end_session)
        self.reopen_button.clicked.connect(self.orchestrator.reopen_current)
        self.settings_button.clicked.connect(self._open_settings)
        self.exec_mode.currentIndexChanged.connect(self._mode_changed)
        self.exec_limit.valueChanged.connect(self._mode_changed)
        self.relay_mode.currentIndexChanged.connect(self._relay_changed)
        self.relay_limit.valueChanged.connect(self._relay_changed)
        self.monitor_button.toggled.connect(self._monitor_toggled)
        self.export_button.clicked.connect(self._export)
        self.print_button.clicked.connect(self._print)
        self.retry_button.clicked.connect(self._retry)
        self.composer.send_local.connect(self._send_local)
        self.composer.send_grok.connect(self._send_grok)
        self.composer.send_both.connect(self._send_both)
        self.history.ask_grok.clicked.connect(self._ask_grok)
        self.history.ask_local.clicked.connect(self._ask_local)
        self.history.verify.clicked.connect(self._verify)
        self.progress.canceled.connect(self.orchestrator.cancel_shutdown)

    def _startup(self) -> None:
        crashed, sid = self.db.crash_marker()
        self.db.set_running(True, sid)
        if crashed and sid:
            session = self.db.get_session(sid)
            if session and session.status in {"open", "closing"}:
                dialog = RecoveryDialog(session.title, session.status, self)
                dialog.exec()
                if dialog.choice == "resume":
                    if session.status == "closing":
                        self.db.reopen_session(sid)
                    self.orchestrator.select_session(sid)
                    self.orchestrator.check_ollama()
                    return
                if dialog.choice == "summarise":
                    self.orchestrator.select_session(sid)
                    self.orchestrator.request_end(self._after_recovery, session_id=sid)
                    self.orchestrator.check_ollama()
                    return
                self.orchestrator.abandon(sid)
        if self.orchestrator.current() is None:
            self.orchestrator.new_session()
        self._reload_sessions()
        self._reload_session_controls()
        self.orchestrator.check_ollama()

    def _after_recovery(self) -> None:
        self.orchestrator.accepting = True
        self.orchestrator.accepting_changed.emit(True)
        self.orchestrator.new_session()

    def _reload_sessions(self) -> None:
        current = self.orchestrator.session_id
        self._loading = True
        self.session_combo.blockSignals(True)
        self.session_combo.clear()
        for session in self.db.list_sessions():
            label = f"{session.title}  ({session.status})"
            self.session_combo.addItem(label, session.id)
        if current:
            index = self.session_combo.findData(current)
            if index >= 0:
                self.session_combo.setCurrentIndex(index)
        self.session_combo.blockSignals(False)
        self._loading = False

    def _reload_session_controls(self) -> None:
        session = self.orchestrator.current()
        self._loading = True
        if session is None:
            self.reopen_button.setEnabled(False)
            self.composer.set_enabled(False)
            self._loading = False
            return
        self.exec_mode.setCurrentIndex(max(0, self.exec_mode.findData(session.execution_mode)))
        self.exec_limit.setValue(session.auto_limit)
        self.relay_mode.setCurrentIndex(max(0, self.relay_mode.findData(session.grok_relay_mode)))
        self.relay_limit.setValue(session.grok_relay_limit)
        self.monitor_button.setChecked(session.monitor_mode)
        self.composer.set_monitor(session.monitor_mode)
        closed = session.status != "open"
        self.closed_banner.setVisible(closed)
        self.reopen_button.setEnabled(closed)
        self.end_button.setEnabled(session.status == "open")
        can_type = session.status == "open" and self.orchestrator.accepting
        self.composer.set_enabled(can_type)
        self.used_label.setText(
            f"auto {session.auto_used}/{session.auto_limit}   "
            f"forward {session.grok_relay_used}/{session.grok_relay_limit}"
        )
        model = session.ollama_model or Settings.load(self.db).ollama_model
        self.setWindowTitle(f"Monitor Maid — {session.title}")
        self.status_label.setText(f"{session.status}  ·  {model}")
        self._loading = False
        self._apply_grok_gate()
        self._reload_transcript()
        self._reload_commands()

    def _reload_transcript(self) -> None:
        session = self.orchestrator.current()
        if session is None:
            self.transcript.set_turns([])
            self.history.set_items([])
            return
        turns = self.db.list_turns(session.id)
        self.transcript.set_turns(turns)
        self.history.set_items(
            build_history_items(
                turns,
                self.db.list_summaries(session.id),
                self.db.list_conclusions(session.id),
            )
        )

    def _reload_commands(self) -> None:
        session = self.orchestrator.current()
        if session is None:
            self.commands.set_records([])
            return
        self.commands.set_records(self.db.list_commands(session.id))

    def _session_picked(self) -> None:
        if self._loading:
            return
        sid = self.session_combo.currentData()
        if sid and sid != self.orchestrator.session_id:
            self.orchestrator.select_session(str(sid))

    def _new_session(self) -> None:
        session = self.orchestrator.current()
        if session and session.status == "open":
            turns = self.db.list_turns(session.id)
            if not turns:
                self.composer.local_edit.setFocus()
                return
            box = QMessageBox(self)
            box.setWindowTitle("New session")
            box.setText("This session is still open.")
            leave = box.addButton("Leave it open", QMessageBox.ButtonRole.AcceptRole)
            end_first = box.addButton("End it first", QMessageBox.ButtonRole.DestructiveRole)
            box.addButton(QMessageBox.StandardButton.Cancel)
            box.exec()
            clicked = box.clickedButton()
            if clicked is end_first:
                self.orchestrator.request_end(self._end_then_new)
                return
            if clicked is not leave:
                return
        self.orchestrator.new_session()

    def _end_then_new(self) -> None:
        self.orchestrator.accepting = True
        self.orchestrator.accepting_changed.emit(True)
        self.orchestrator.new_session()

    def _end_session(self) -> None:
        session = self.orchestrator.current()
        if session is None or session.status != "open":
            return
        confirm = QMessageBox.question(
            self,
            "End session",
            "End this session? Monitor Maid will ask the local model for a summary "
            "and any conclusions. A very short session skips that call.",
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        self.orchestrator.request_end(self._after_end)

    def _after_end(self) -> None:
        self.orchestrator.accepting = True
        self.orchestrator.accepting_changed.emit(True)
        self._reload_session_controls()

    def _open_settings(self) -> None:
        dialog = SettingsDialog(self.db, self)
        if not dialog.exec():
            return
        settings = Settings.load(self.db)
        session = self.orchestrator.current()
        if session and session.status == "open":
            self.db.update_session(session.id, ollama_model=settings.ollama_model)
            self._reload_session_controls()
        self.orchestrator.check_ollama()

    def _mode_changed(self) -> None:
        if self._loading:
            return
        mode = self.exec_mode.currentData()
        if mode:
            self.orchestrator.set_execution_mode(str(mode), self.exec_limit.value())

    def _relay_changed(self) -> None:
        if self._loading:
            return
        mode = self.relay_mode.currentData()
        if mode:
            self.orchestrator.set_relay_mode(str(mode), self.relay_limit.value())

    def _monitor_toggled(self, checked: bool) -> None:
        if self._loading:
            return
        self.orchestrator.set_monitor_mode(checked)

    def _send_local(self, text: str) -> None:
        if not self.orchestrator.submit_local(text):
            self.composer.local_edit.setPlainText(text)

    def _send_grok(self, text: str) -> None:
        if not self.orchestrator.submit_grok(text):
            self.composer.grok_edit.setPlainText(text)

    def _send_both(self, local: str, grok: str) -> None:
        if not self.orchestrator.submit_both(local, grok):
            self.composer.local_edit.setPlainText(local)
            self.composer.grok_edit.setPlainText(grok)

    def _selected(self):
        items = self.history.selected_items()
        if not items:
            QMessageBox.information(self, "History", "Select at least one item.")
            return None
        return items

    def _ask_grok(self) -> None:
        items = self._selected()
        if not items:
            return
        dialog = AskDialog(
            "Ask Grok",
            f"{len(items)} selected item(s) will be sent to Grok with the local transcript. "
            "The local model will not see the question or the reply.",
            "What should I make of the selected items?",
            self,
        )
        if dialog.exec():
            self.orchestrator.submit_selection_grok(items, dialog.question())

    def _ask_local(self) -> None:
        items = self._selected()
        if not items:
            return
        dialog = AskDialog(
            "Send to the local model",
            f"{len(items)} selected item(s) will be sent as a user message.",
            "Using these items, continue.",
            self,
        )
        if dialog.exec():
            self.orchestrator.submit_selection_local(items, dialog.question())

    def _verify(self) -> None:
        items = self._selected()
        if not items:
            return
        dialog = AskDialog(
            "Verify with Grok",
            "Grok will check the selected items against the local transcript. "
            "Leave the question as written, or replace it.",
            "",
            self,
        )
        if dialog.exec():
            self.orchestrator.submit_verification(items, dialog.question())

    def _bundle(self):
        session = self.orchestrator.current()
        if session is None:
            return None
        return (
            session,
            self.db.list_turns(session.id),
            self.db.list_summaries(session.id),
            self.db.list_conclusions(session.id),
            self.db.list_commands(session.id),
        )

    def _export(self) -> None:
        bundle = self._bundle()
        if bundle is None:
            return
        selected = self.history.selected_items()
        if selected:
            box = QMessageBox(self)
            box.setWindowTitle("Export")
            box.setText("Export the whole session, or only the checked history items?")
            whole = box.addButton("Whole session", QMessageBox.ButtonRole.AcceptRole)
            checked_only = box.addButton("Checked items", QMessageBox.ButtonRole.ActionRole)
            box.addButton(QMessageBox.StandardButton.Cancel)
            box.exec()
            clicked = box.clickedButton()
            if clicked is not whole and clicked is not checked_only:
                return
            if clicked is checked_only:
                path, _filter = QFileDialog.getSaveFileName(
                    self, "Export selection", "selection.md", "Markdown (*.md)"
                )
                if path:
                    _write(path, selection_to_markdown(selected))
                return
        path, selected_filter = QFileDialog.getSaveFileName(
            self,
            "Export transcript",
            "transcript.md",
            "Markdown (*.md);;Plain text (*.txt)",
        )
        if not path:
            return
        session, turns, summaries, conclusions, commands = bundle
        if path.endswith(".txt") or "Plain" in selected_filter:
            text = session_to_plaintext(session, turns, summaries, conclusions, commands)
        else:
            text = session_to_markdown(session, turns, summaries, conclusions, commands)
        _write(path, text)
        self.status_label.setText(f"Exported {path}")

    def _print(self) -> None:
        bundle = self._bundle()
        if bundle is None:
            return
        session, turns, summaries, conclusions, commands = bundle
        document = QTextDocument()
        document.setHtml(session_to_html(session, turns, summaries, conclusions, commands))
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        dialog = QPrintDialog(printer, self)
        if dialog.exec():
            document.print(printer)

    def _retry(self) -> None:
        if not self.orchestrator.retry_last():
            self.status_label.setText("Nothing to retry.")

    def _show_pending(self, lines: list) -> None:
        if not lines:
            self.pending_label.setText("")
            self.pending_label.hide()
            return
        preview = " · ".join(lines[:3])
        extra = f"  (+{len(lines) - 3})" if len(lines) > 3 else ""
        self.pending_label.setText(f"Queued: {preview}{extra}")
        self.pending_label.show()

    def _apply_grok_gate(self) -> None:
        ready = bool(effective_api_key(Settings.load(self.db)))
        missing = "Add an xAI API key in Settings, or set XAI_API_KEY."
        self.composer.set_grok_available(ready)
        self.monitor_button.setEnabled(ready)
        self.monitor_button.setToolTip(self._monitor_tip if ready else missing)
        self.relay_mode.setEnabled(ready)
        self.relay_limit.setEnabled(ready)
        self.relay_mode.setToolTip("" if ready else missing)
        self.relay_limit.setToolTip("" if ready else missing)
        self.history.set_grok_enabled(ready)

    def _show_ollama(self, status: str) -> None:
        hints = {
            "offline": "Ollama is not running. Start it with `ollama serve`, then try again.",
            "timeout": "Ollama did not answer in time. Check that `ollama serve` is still running.",
        }
        if status == "online":
            self.ollama_label.setText("Ollama: online")
            self.ollama_hint.hide()
            return
        text = hints.get(status, f"Ollama: {status}")
        self.ollama_label.setText(text)
        self.ollama_hint.setText(text)
        self.ollama_hint.show()

    def _show_busy(self, busy: bool) -> None:
        self.session_combo.setEnabled(not busy and self.orchestrator.accepting)
        self.new_button.setEnabled(not busy and self.orchestrator.accepting)

    def _apply_accepting(self, accepting: bool) -> None:
        session = self.orchestrator.current()
        open_session = session is not None and session.status == "open"
        self.composer.set_enabled(accepting and open_session)
        self.end_button.setEnabled(accepting and open_session)
        self._apply_grok_gate()

    def _show_shutdown(self, active: bool) -> None:
        self.progress.blockSignals(True)
        if active:
            self.progress.reset()
            self.progress.show()
        else:
            self.progress.hide()
        self.progress.blockSignals(False)

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._quit_done:
            event.accept()
            return
        event.ignore()
        if self._quitting:
            return
        self._quitting = True
        self.orchestrator.request_end(self._finish_quit)

    def _finish_quit(self) -> None:
        self.progress.blockSignals(True)
        self.progress.hide()
        self.progress.blockSignals(False)
        self.orchestrator.mark_clean_exit()
        self._quit_done = True
        QTimer.singleShot(0, self.close)


def _write(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
