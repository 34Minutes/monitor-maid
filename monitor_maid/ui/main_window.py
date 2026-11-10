"""Main window: transcript, dual composer, history, and session controls."""

from __future__ import annotations

import os
import threading

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QTextCursor, QTextDocument
from PyQt6.QtPrintSupport import QPrintDialog, QPrinter
from PyQt6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
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
from monitor_maid.models import MODE_LABELS, HistoryItem
from monitor_maid.ollama_client import OllamaClient, OllamaError, apply_model_switch, same_model
from monitor_maid.orchestrator import Orchestrator
from monitor_maid.paths import process_log_dir
from monitor_maid.ui.command_log import CommandLog
from monitor_maid.ui.composer import Composer
from monitor_maid.ui.dialogs import (
    AskDialog,
    EndSessionDialog,
    NoteDialog,
    NotesBrowser,
    RecoveryDialog,
    SeedModelDialog,
    SettingsDialog,
    model_choices,
    seed_button_label,
)
from monitor_maid.ui.history_panel import HistoryPanel
from monitor_maid.ui.hooks import WindowHooks
from monitor_maid.ui.transcript import TranscriptView


class ModelCombo(QComboBox):
    """Model list. Opening it asks Ollama for `ollama list` again."""

    def showPopup(self) -> None:  # noqa: N802
        window = self.window()
        refresh = getattr(window, "_refresh_model_list", None)
        if callable(refresh):
            refresh()
        super().showPopup()

    def hidePopup(self) -> None:  # noqa: N802
        super().hidePopup()
        window = self.window()
        if getattr(window, "_model_names_pending", False):
            window._model_names_pending = False
            window._fill_model_combo()


class MainWindow(QMainWindow):
    _models_listed = pyqtSignal(int, str)
    _model_switch_done = pyqtSignal(int, str)

    def __init__(self, db: Database):
        super().__init__()
        self.db = db
        self.setWindowTitle("Monitor Maid")
        self.resize(1240, 800)
        self._quit_done = False
        self._quitting = False
        self._loading = False
        self._seed_after_new: list[HistoryItem] | None = None
        self._seed_model: str | None = None
        self._model_names: list[str] = []
        self._model_names_pending = False
        self._model_list_gen = 0
        self._model_applied = False
        self._active_model = ""
        self._switch_gen = 0
        self._switch_reported = 0
        self._switch_lock = threading.Lock()
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
        self.model_combo = ModelCombo()
        self.model_combo.setObjectName("modelCombo")
        self.model_combo.setMinimumWidth(200)
        self.model_combo.setMaximumWidth(360)
        self.model_combo.setToolTip(
            "Models installed in Ollama. Choosing one unloads the others and loads this one."
        )
        self.session_combo = QComboBox()
        self.session_combo.setObjectName("sessionCombo")
        self.session_combo.setMinimumWidth(260)
        self.rename_button = QPushButton("Rename")
        self.rename_button.setObjectName("renameSession")
        self.rename_button.setToolTip("Give this session a name. A name you set is kept.")
        self.new_button = QPushButton("New")
        self.end_button = QPushButton("End session")
        self.reopen_button = QPushButton("Reopen")
        self.settings_button = QPushButton("Settings")
        model_label = QLabel("Model")
        model_label.setObjectName("modelLabel")
        row1.addWidget(model_label)
        row1.addWidget(self.model_combo)
        row1.addWidget(QLabel("Session"))
        row1.addWidget(self.session_combo, 1)
        row1.addWidget(self.rename_button)
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
        splitter.setObjectName("mainSplit")
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(8)
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
        self.composer.set_enter_submits(self.db.get_setting("enter_submits", "1") != "0")
        left_layout.addWidget(legend)
        left_layout.addWidget(self.transcript, 1)
        self.history = HistoryPanel()
        self.commands = CommandLog()
        tabs = QTabWidget()
        tabs.addTab(self.history, "History")
        tabs.addTab(self.commands, "Commands")
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(tabs, 1)
        right_layout.addWidget(self.closed_banner)
        right_layout.addWidget(self.ollama_hint)
        right_layout.addWidget(self.pending_label)
        right_layout.addWidget(self.composer)
        splitter.addWidget(left)
        splitter.addWidget(right)
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
        self._build_menus()

    def _build_menus(self) -> None:
        menu = self.menuBar().addMenu("Session")
        self.act_rename = QAction("Rename session…", self)
        self.act_rename.setObjectName("renameSessionAction")
        self.act_create_summary = QAction("Create summary", self)
        self.act_create_summary.setObjectName("createSummary")
        self.act_create_prompt = QAction("Create prompt", self)
        self.act_create_prompt.setObjectName("createPrompt")
        self.act_create_prompt_grok = QAction("Create prompt with Grok", self)
        self.act_create_prompt_grok.setObjectName("createPromptGrok")
        self.act_browse = QAction("Browse sessions…", self)
        self.act_browse.setObjectName("browseNotes")
        self.act_view_summary = QAction("View summary", self)
        self.act_view_summary.setObjectName("viewSummary")
        self.act_view_prompt = QAction("View prompt", self)
        self.act_view_prompt.setObjectName("viewPrompt")
        self.act_from_checked = QAction("New session from checked items", self)
        self.act_from_checked.setObjectName("newFromChecked")
        menu.addAction(self.act_rename)
        menu.addSeparator()
        menu.addAction(self.act_create_summary)
        menu.addAction(self.act_create_prompt)
        menu.addAction(self.act_create_prompt_grok)
        menu.addSeparator()
        menu.addAction(self.act_browse)
        menu.addAction(self.act_view_summary)
        menu.addAction(self.act_view_prompt)
        menu.addSeparator()
        menu.addAction(self.act_from_checked)

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
        self._models_listed.connect(self._on_models_listed, Qt.ConnectionType.QueuedConnection)
        self._model_switch_done.connect(
            self._on_model_switch,
            Qt.ConnectionType.QueuedConnection,
        )
        self.model_combo.activated.connect(self._model_activated)
        self.session_combo.currentIndexChanged.connect(self._session_picked)
        self.rename_button.clicked.connect(self._rename_session)
        self.act_rename.triggered.connect(self._rename_session)
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
        self.composer.enter_mode_changed.connect(self._save_enter_mode)
        self.history.ask_grok.clicked.connect(self._ask_grok)
        self.history.ask_local.clicked.connect(self._ask_local)
        self.history.verify.clicked.connect(self._verify)
        self.history.new_from_checked.connect(self._new_from_checked)
        self.history.view_item.connect(self._view_history_item)
        self.act_create_summary.triggered.connect(self._create_summary)
        self.act_create_prompt.triggered.connect(self._create_prompt)
        self.act_create_prompt_grok.triggered.connect(self._create_prompt_grok)
        self.act_browse.triggered.connect(self._browse_notes)
        self.act_view_summary.triggered.connect(lambda: self._view_latest("summary"))
        self.act_view_prompt.triggered.connect(lambda: self._view_latest("prompt"))
        self.act_from_checked.triggered.connect(self._new_from_checked)
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
                    self._refresh_model_list()
                    return
                if dialog.choice == "summarise":
                    self.orchestrator.select_session(sid)
                    self.orchestrator.request_end(self._after_recovery, session_id=sid)
                    self.orchestrator.check_ollama()
                    self._refresh_model_list()
                    return
                self.orchestrator.abandon(sid)
        if self.orchestrator.current() is None:
            self.orchestrator.new_session()
        self._reload_sessions()
        self._reload_session_controls()
        self.orchestrator.check_ollama()
        self._refresh_model_list()

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
        self._fill_model_combo(model)
        open_session = session.status == "open"
        self.model_combo.setEnabled(
            open_session and self.orchestrator.accepting and not self.orchestrator.busy
        )
        self.setWindowTitle(f"Monitor Maid — {session.title}")
        if self._switch_gen == self._switch_reported:
            self.status_label.setText(f"{session.status}  ·  {model}")
        self._loading = False
        self._apply_grok_gate()
        self._reload_transcript()
        self._reload_commands()
        self._consider_model_switch(model)

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
                self.db.list_prompts(session.id),
            )
        )

    def _reload_commands(self) -> None:
        session = self.orchestrator.current()
        if session is None:
            self.commands.set_records([])
            return
        self.commands.set_records(self.db.list_commands(session.id))

    def _fill_model_combo(self, current: str | None = None) -> None:
        if current is None:
            session = self.orchestrator.current()
            if session and session.ollama_model:
                current = session.ollama_model
            else:
                current = Settings.load(self.db).ollama_model
        chosen, names = model_choices(current or "", self._model_names)
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        if names:
            self.model_combo.addItems(names)
            index = self.model_combo.findText(chosen)
            if index >= 0:
                self.model_combo.setCurrentIndex(index)
        self.model_combo.blockSignals(False)

    def _refresh_model_list(self) -> None:
        """Ask Ollama for the installed models. This is `ollama list`."""
        self._model_list_gen += 1
        generation = self._model_list_gen
        url = Settings.load(self.db).ollama_url

        def work() -> None:
            try:
                names = OllamaClient(url).list_models()
                payload = "\n".join(names)
            except OllamaError as exc:
                payload = "ERROR\n" + str(exc)
            except Exception as exc:  # noqa: BLE001 — the list is optional
                payload = "ERROR\n" + str(exc)
            try:
                self._models_listed.emit(generation, payload)
            except RuntimeError:
                return

        threading.Thread(target=work, name="monitor-maid-models", daemon=True).start()

    def _on_models_listed(self, generation: int, payload: str) -> None:
        if generation != self._model_list_gen or self._quitting or self._quit_done:
            return
        error = ""
        if payload.startswith("ERROR\n"):
            error = payload.split("\n", 1)[1]
        else:
            names = [line.strip() for line in payload.splitlines() if line.strip()]
            if names:
                self._model_names = names
        if self.model_combo.view().isVisible():
            self._model_names_pending = True
            return
        self._fill_model_combo()
        if error and not self._model_names:
            self.model_combo.setToolTip(error)
        elif self._model_names:
            self.model_combo.setToolTip(
                "Models installed in Ollama. Choosing one unloads the others and loads this one."
            )

    def _model_activated(self, index: int) -> None:
        if self._loading:
            return
        name = self.model_combo.itemText(index).strip()
        if not name:
            return
        self.orchestrator.set_ollama_model(name)

    def _consider_model_switch(self, model: str) -> None:
        if not model:
            return
        if not self._model_applied:
            self._model_applied = True
            self._active_model = model
            return
        if same_model(model, self._active_model):
            self._active_model = model
            return
        previous = self._active_model
        self._active_model = model
        self._begin_model_switch(model, previous)

    def _begin_model_switch(self, target: str, previous: str | None) -> None:
        self._switch_gen += 1
        generation = self._switch_gen
        url = Settings.load(self.db).ollama_url
        self.status_label.setText(f"Stopping other models and starting {target}…")

        def work() -> None:
            try:
                with self._switch_lock:
                    if generation != self._switch_gen:
                        return
                    apply_model_switch(OllamaClient(url), target, previous)
                message = f"{target} is loaded."
            except Exception as exc:  # noqa: BLE001 — the status line is the report
                message = str(exc) or "The model could not be changed."
            try:
                self._model_switch_done.emit(generation, message)
            except RuntimeError:
                return

        threading.Thread(target=work, name="monitor-maid-model", daemon=True).start()

    def _on_model_switch(self, generation: int, message: str) -> None:
        if generation != self._switch_gen or self._quitting or self._quit_done:
            return
        self._switch_reported = generation
        self.status_label.setText(message)

    def _ask_seed_model(self) -> str | None:
        session = self.orchestrator.current()
        current = ""
        if session and session.ollama_model:
            current = session.ollama_model
        else:
            current = Settings.load(self.db).ollama_model
        dialog = SeedModelDialog(self._model_names, current, self)
        if not dialog.exec():
            return None
        return dialog.choice or None

    def _session_picked(self) -> None:
        if self._loading:
            return
        sid = self.session_combo.currentData()
        if sid and sid != self.orchestrator.session_id:
            self.orchestrator.select_session(str(sid))

    def _rename_session(self) -> None:
        session = self.orchestrator.current()
        if session is None:
            self.status_label.setText("Open a session first.")
            return
        text, ok = QInputDialog.getText(
            self,
            "Rename session",
            "Name for this session:",
            QLineEdit.EchoMode.Normal,
            session.title,
        )
        if ok:
            self.orchestrator.rename_session(text)

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

    def _create_summary(self) -> None:
        self.orchestrator.request_summary()

    def _save_enter_mode(self, enabled: bool) -> None:
        self.db.set_setting("enter_submits", "1" if enabled else "0")

    def _create_prompt(self) -> None:
        self.orchestrator.request_prompt("local_llm")

    def _create_prompt_grok(self) -> None:
        self.orchestrator.request_prompt("grok")

    def _browse_notes(self) -> None:
        protected = None
        if self.orchestrator.busy or not self.orchestrator.accepting:
            protected = self.orchestrator.session_id
        dialog = NotesBrowser(
            self.db,
            self,
            current_id=self.orchestrator.session_id,
            protected_id=protected,
        )
        accepted = bool(dialog.exec())
        self._apply_deleted_sessions(dialog.deleted_ids)
        if accepted and dialog.seed_item is not None:
            self._start_from_items([dialog.seed_item])

    def _apply_deleted_sessions(self, ids: list[str]) -> None:
        if not ids:
            return
        if self.orchestrator.session_id in ids:
            self.orchestrator.session_id = None
            sessions = self.db.list_sessions()
            if sessions:
                self.orchestrator.select_session(sessions[0].id)
            else:
                self.orchestrator.new_session()
            return
        self._reload_sessions()

    def _view_latest(self, kind: str) -> None:
        session = self.orchestrator.current()
        if session is None:
            QMessageBox.information(self, "History", "Open a session first.")
            return
        items = [
            item
            for item in build_history_items(
                [],
                self.db.list_summaries(session.id),
                self.db.list_prompts(session.id),
            )
            if item.kind == kind
        ]
        title = "Summary" if kind == "summary" else "Prompt"
        if not items:
            QMessageBox.information(self, title, f"This session has no {kind} yet.")
            return
        if kind == "prompt":
            body = "\n\n".join(item.content for item in items)
            label = (
                "Start a new session with this prompt"
                if len(items) == 1
                else "Start a new session with these prompts"
            )
            heading = (
                "Prompt stored in this session."
                if len(items) == 1
                else f"{len(items)} prompts stored in this session."
            )
            self._show_notes(
                items,
                title="Prompt" if len(items) == 1 else "Prompts",
                heading=heading,
                body=body,
                seed_label=label,
            )
            return
        heading = "Latest summary in this session."
        if len(items) > 1:
            heading = (
                f"Latest of {len(items)} summaries in this session. "
                "Browse sessions to see the earlier ones."
            )
        self._show_note(items[-1], heading=heading)

    def _view_history_item(self, item: object) -> None:
        if not isinstance(item, HistoryItem):
            return
        title = "Summary" if item.kind == "summary" else "Prompt"
        self._show_note(item, heading=f"{title} from this session.")

    def _show_note(self, item: HistoryItem, *, heading: str) -> None:
        title = "Summary" if item.kind == "summary" else "Prompt"
        self._show_notes(
            [item],
            title=title,
            heading=heading,
            body=item.content,
            seed_label=seed_button_label(item.kind),
        )

    def _show_notes(
        self,
        items: list[HistoryItem],
        *,
        title: str,
        heading: str,
        body: str,
        seed_label: str,
    ) -> None:
        dialog = NoteDialog(title, heading, body, seed_label, self)
        if dialog.exec() and dialog.seed:
            self._start_from_items(items)

    def _new_from_checked(self) -> None:
        items = self.history.checked_items()
        if not items:
            QMessageBox.information(
                self,
                "History",
                "Tick at least one history row. The chosen rows are placed in the input box of a new session.",
            )
            return
        self._start_from_items(items)

    def _start_from_items(self, items: list[HistoryItem]) -> None:
        if self.orchestrator.busy or not self.orchestrator.accepting:
            self.status_label.setText("Wait for the current reply before starting a new session.")
            return
        model = self._ask_seed_model()
        if not model:
            return
        held = list(items)
        session = self.orchestrator.current()
        if session and session.status == "open" and self.db.list_turns(session.id):
            box = QMessageBox(self)
            box.setWindowTitle("New session")
            box.setText(
                "This session is still open. A new session will start with what you chose in the input box."
            )
            leave = box.addButton("Leave it open", QMessageBox.ButtonRole.AcceptRole)
            end_first = box.addButton("End it first", QMessageBox.ButtonRole.DestructiveRole)
            box.addButton(QMessageBox.StandardButton.Cancel)
            box.exec()
            clicked = box.clickedButton()
            if clicked is end_first:
                self._seed_after_new = held
                self._seed_model = model
                self.orchestrator.request_end(self._end_then_seed)
                return
            if clicked is not leave:
                return
        self._plant_in_fresh_session(held, model)

    def _end_then_seed(self) -> None:
        self.orchestrator.accepting = True
        self.orchestrator.accepting_changed.emit(True)
        items = self._seed_after_new or []
        model = self._seed_model
        self._seed_after_new = None
        self._seed_model = None
        self.orchestrator.new_session(ollama_model=model)
        self._put_selection_in_input(items)

    def _plant_in_fresh_session(self, items: list[HistoryItem], model: str) -> None:
        session = self.orchestrator.current()
        reuse = (
            session is not None
            and session.status == "open"
            and not self.db.list_turns(session.id)
        )
        if reuse:
            if model and (session.ollama_model or "") != model:
                self.orchestrator.set_ollama_model(model)
        else:
            self.orchestrator.new_session(ollama_model=model)
        self._put_selection_in_input(items)

    def _put_selection_in_input(self, items: list[HistoryItem]) -> None:
        """Place the chosen history in the local input. Nothing is sent yet."""
        text = self.orchestrator.selection_draft(items)
        if not text.strip():
            self.status_label.setText("Nothing from that selection is still stored.")
            return
        edit = self.composer.local_edit
        edit.setPlainText(text)
        cursor = edit.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        edit.setTextCursor(cursor)
        edit.setFocus()
        self.status_label.setText("The selection is in the input box. Edit it, then send.")

    def _end_session(self) -> None:
        session = self.orchestrator.current()
        if session is None or session.status != "open":
            return
        dialog = EndSessionDialog(self)
        dialog.exec()
        if dialog.choice == "yes":
            self.orchestrator.request_end(self._after_end)
        elif dialog.choice == "no":
            self.orchestrator.request_end(self._after_end, store_notes=False)

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
            self.db.list_prompts(session.id),
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
        session, turns, summaries, prompts, commands = bundle
        if path.endswith(".txt") or "Plain" in selected_filter:
            text = session_to_plaintext(session, turns, summaries, prompts, commands)
        else:
            text = session_to_markdown(session, turns, summaries, prompts, commands)
        _write(path, text)
        self.status_label.setText(f"Exported {path}")

    def _print(self) -> None:
        bundle = self._bundle()
        if bundle is None:
            return
        session, turns, summaries, prompts, commands = bundle
        document = QTextDocument()
        document.setHtml(session_to_html(session, turns, summaries, prompts, commands))
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
        self._sync_prompt_grok_action()
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
        self._set_session_actions(not busy and self.orchestrator.accepting)

    def _apply_accepting(self, accepting: bool) -> None:
        session = self.orchestrator.current()
        open_session = session is not None and session.status == "open"
        self.composer.set_enabled(accepting and open_session)
        self.end_button.setEnabled(accepting and open_session)
        self._set_session_actions(accepting and not self.orchestrator.busy)
        self._apply_grok_gate()

    def _set_session_actions(self, enabled: bool) -> None:
        session = self.orchestrator.current()
        open_session = session is not None and session.status == "open"
        self.model_combo.setEnabled(enabled and open_session)
        self.session_combo.setEnabled(enabled)
        self.new_button.setEnabled(enabled)
        self.history.can_start = enabled
        self.act_create_summary.setEnabled(enabled)
        self.act_create_prompt.setEnabled(enabled)
        self.act_from_checked.setEnabled(enabled)
        self._sync_prompt_grok_action()

    def _sync_prompt_grok_action(self) -> None:
        ready = bool(effective_api_key(Settings.load(self.db)))
        session = self.orchestrator.current()
        open_session = session is not None and session.status == "open"
        self.act_create_prompt_grok.setEnabled(
            ready and open_session and self.orchestrator.accepting and not self.orchestrator.busy
        )
        self.act_create_prompt_grok.setToolTip(
            "" if ready else "Add an xAI API key in Settings, or set XAI_API_KEY."
        )

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
