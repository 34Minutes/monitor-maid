"""Choose the directory, session, and flags for a Grok Build run.

The prompt box is optional. Ask Grok reads the Grok box, so a person can
leave this empty and type the prompt there.
"""

from __future__ import annotations

import os
import threading

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from monitor_maid.grok_build import (
    OUTPUT_FORMATS,
    GrokBuildConfig,
    GrokBuildError,
    ListedSession,
    list_models,
    list_sessions,
    normalise_session_uuid,
    parse_extra_flags,
    validate_directory,
)


class GrokSessionDialog(QDialog):
    """Startup questions for one Grok Build session. No Grok window is opened."""

    _listed = pyqtSignal(int, object, str)
    _models = pyqtSignal(int, object, str)

    def __init__(
        self,
        config: GrokBuildConfig,
        prompt: str = "",
        parent=None,
        *,
        intent: str = "",
    ):
        super().__init__(parent)
        if intent == "new":
            self.setWindowTitle("New Grok")
        elif intent == "resume":
            self.setWindowTitle("Reopen Grok")
        else:
            self.setWindowTitle("Grok Build session")
        self.resize(860, 680)
        self._list_generation = 0
        self._model_generation = 0
        self._sessions: list[ListedSession] = []
        self._listed.connect(self._show_sessions, Qt.ConnectionType.QueuedConnection)
        self._models.connect(self._show_models, Qt.ConnectionType.QueuedConnection)
        self._config = GrokBuildConfig()

        layout = QVBoxLayout(self)
        intro = QLabel(
            "This starts a headless grok session. The Grok window stays closed. "
            "The directory has to exist. A blank prompt can be typed in the Grok "
            "box and sent with Ask Grok."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        directory_row = QWidget()
        directory_layout = QHBoxLayout(directory_row)
        directory_layout.setContentsMargins(0, 0, 0, 0)
        self.directory = QLineEdit(config.cwd or os.getcwd())
        self.directory.setPlaceholderText("Directory grok should work in")
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        self.list_button = QPushButton("List sessions")
        self.list_button.clicked.connect(self._refresh_sessions)
        directory_layout.addWidget(self.directory, 1)
        directory_layout.addWidget(browse)
        directory_layout.addWidget(self.list_button)
        layout.addWidget(directory_row)

        self.status = QLabel("")
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["Session ID", "Created", "Updated", "Status", "Summary"]
        )
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemSelectionChanged.connect(self._row_picked)
        layout.addWidget(self.table, 1)

        choice = QHBoxLayout()
        self.resume_radio = QRadioButton("Resume selected")
        self.paste_radio = QRadioButton("Paste an id")
        self.new_radio = QRadioButton("New session")
        self._choices = QButtonGroup(self)
        self._choices.addButton(self.resume_radio)
        self._choices.addButton(self.paste_radio)
        self._choices.addButton(self.new_radio)
        choice.addWidget(self.resume_radio)
        choice.addWidget(self.paste_radio)
        choice.addWidget(self.new_radio)
        choice.addStretch(1)
        layout.addLayout(choice)

        form = QFormLayout()
        self.paste_id = QLineEdit(config.session_id if config.bound else "")
        self.paste_id.setPlaceholderText("Session id to resume")
        self.new_id = QLineEdit(config.pending_session_id)
        self.new_id.setPlaceholderText("Optional UUID for --session-id")
        form.addRow("Resume id", self.paste_id)
        form.addRow("New session id", self.new_id)
        layout.addLayout(form)

        options = QFormLayout()
        model_row = QWidget()
        model_layout = QHBoxLayout(model_row)
        model_layout.setContentsMargins(0, 0, 0, 0)
        self.model = QComboBox()
        self.model.setEditable(True)
        self.model.addItem("(default)", "")
        if config.model:
            self.model.addItem(config.model, config.model)
            self.model.setCurrentText(config.model)
        self.model_button = QPushButton("Load models")
        self.model_button.clicked.connect(self._refresh_models)
        model_layout.addWidget(self.model, 1)
        model_layout.addWidget(self.model_button)
        self.always_approve = QCheckBox("Always approve tool calls (--always-approve)")
        self.always_approve.setChecked(config.always_approve)
        self.output_format = QComboBox()
        for name in OUTPUT_FORMATS:
            self.output_format.addItem(name)
        index = self.output_format.findText(config.output_format)
        self.output_format.setCurrentIndex(max(0, index))
        self.extra_flags = QLineEdit(config.extra_flags)
        self.extra_flags.setPlaceholderText("--max-turns 8 --effort high")
        options.addRow("Model", model_row)
        options.addRow("", self.always_approve)
        options.addRow("Output", self.output_format)
        options.addRow("Extra flags", self.extra_flags)
        layout.addLayout(options)

        self.prompt = QPlainTextEdit(prompt)
        self.prompt.setPlaceholderText(
            "Prompt for this run. Leave this blank to type it in the Grok box."
        )
        self.prompt.setMinimumHeight(90)
        layout.addWidget(QLabel("Prompt"))
        layout.addWidget(self.prompt)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Use this session")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        if intent == "new":
            self.new_radio.setChecked(True)
        elif intent == "resume":
            self.resume_radio.setChecked(True)
        elif config.bound and config.session_id:
            self.resume_radio.setChecked(True)
        elif config.pending_session_id:
            self.new_radio.setChecked(True)
        else:
            self.new_radio.setChecked(True)
        self.resume_radio.toggled.connect(self._sync_choice)
        self.paste_radio.toggled.connect(self._sync_choice)
        self.new_radio.toggled.connect(self._sync_choice)
        self._sync_choice()

    def config(self) -> GrokBuildConfig:
        return self._config

    def prompt_text(self) -> str:
        return self.prompt.toPlainText()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if not self._sessions:
            self._refresh_sessions()

    def _browse(self) -> None:
        start = self.directory.text().strip() or os.getcwd()
        chosen = QFileDialog.getExistingDirectory(self, "Grok working directory", start)
        if chosen:
            self.directory.setText(chosen)
            self._refresh_sessions()

    def _refresh_sessions(self) -> None:
        try:
            directory = str(validate_directory(self.directory.text()))
        except GrokBuildError as exc:
            self.status.setText(str(exc))
            return
        self.directory.setText(directory)
        self._list_generation += 1
        generation = self._list_generation
        self.list_button.setEnabled(False)
        self.status.setText("Listing sessions…")

        def work() -> None:
            try:
                rows = list_sessions(directory)
                error = ""
            except GrokBuildError as exc:
                rows = []
                error = str(exc)
            self._listed.emit(generation, rows, error)

        threading.Thread(target=work, daemon=True).start()

    def _show_sessions(self, generation: int, rows: object, error: str) -> None:
        if generation != self._list_generation or not self.isVisible():
            return
        self.list_button.setEnabled(True)
        self._sessions = list(rows) if isinstance(rows, list) else []
        self.table.setRowCount(len(self._sessions))
        for index, session in enumerate(self._sessions):
            values = (
                session.session_id,
                session.created,
                session.updated,
                session.status,
                session.summary,
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, session.session_id)
                self.table.setItem(index, column, item)
        self.table.resizeColumnsToContents()
        if error:
            self.status.setText(error)
            return
        if not self._sessions:
            self.status.setText("No sessions in that directory yet. Start a new one.")
            return
        self.status.setText(f"{len(self._sessions)} recent session(s).")
        wanted = self.paste_id.text().strip()
        if wanted:
            self._select_id(wanted)

    def _select_id(self, session_id: str) -> None:
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item and item.text() == session_id:
                self.table.selectRow(row)
                return

    def _row_picked(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        item = self.table.item(rows[0].row(), 0)
        if item is None:
            return
        self.paste_id.setText(item.text())
        if not self.paste_radio.isChecked():
            self.resume_radio.setChecked(True)

    def _refresh_models(self) -> None:
        self._model_generation += 1
        generation = self._model_generation
        self.model_button.setEnabled(False)

        def work() -> None:
            try:
                names = list_models()
                error = ""
            except GrokBuildError as exc:
                names = []
                error = str(exc)
            self._models.emit(generation, names, error)

        threading.Thread(target=work, daemon=True).start()

    def _show_models(self, generation: int, names: object, error: str) -> None:
        if generation != self._model_generation or not self.isVisible():
            return
        self.model_button.setEnabled(True)
        if error:
            self.status.setText(error)
            return
        current = self.model.currentText().strip()
        if current == "(default)":
            current = ""
        self.model.blockSignals(True)
        self.model.clear()
        self.model.addItem("(default)", "")
        for name in names if isinstance(names, list) else []:
            self.model.addItem(str(name), str(name))
        if current:
            self.model.setCurrentText(current)
        else:
            self.model.setCurrentIndex(0)
        self.model.blockSignals(False)
        self.status.setText("Loaded models from grok models.")

    def _sync_choice(self) -> None:
        self.paste_id.setEnabled(self.resume_radio.isChecked() or self.paste_radio.isChecked())
        self.new_id.setEnabled(self.new_radio.isChecked())

    def _selected_id(self) -> str:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return ""
        item = self.table.item(rows[0].row(), 0)
        return item.text().strip() if item else ""

    def _accept(self) -> None:
        try:
            directory = str(validate_directory(self.directory.text()))
        except GrokBuildError as exc:
            self.status.setText(str(exc))
            return
        try:
            parse_extra_flags(self.extra_flags.text())
        except GrokBuildError as exc:
            self.status.setText(str(exc))
            return
        model = self.model.currentText().strip()
        if model == "(default)":
            model = ""
        config = GrokBuildConfig(
            cwd=directory,
            model=model,
            always_approve=self.always_approve.isChecked(),
            output_format=self.output_format.currentText() or "streaming-json",
            extra_flags=self.extra_flags.text().strip(),
        )
        if self.new_radio.isChecked():
            pending = self.new_id.text().strip()
            if pending:
                try:
                    pending = normalise_session_uuid(pending)
                except GrokBuildError as exc:
                    self.status.setText(str(exc))
                    return
            config.pending_session_id = pending
            config.bound = False
            config.session_id = ""
        else:
            session_id = self.paste_id.text().strip() or self._selected_id()
            if not session_id:
                self.status.setText("Choose a session in the list, or paste its id.")
                return
            config.session_id = session_id
            config.bound = True
            config.pending_session_id = ""
        self._config = config
        self.directory.setText(directory)
        self.accept()
