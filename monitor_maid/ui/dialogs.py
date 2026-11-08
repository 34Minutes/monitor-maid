"""Settings, approval, recovery, and question dialogs."""

from __future__ import annotations

import threading

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from monitor_maid.config import Settings, effective_api_key, env_key_set
from monitor_maid.models import MODE_LABELS, ApprovalRequest, ApprovalResult, ForwardRequest
from monitor_maid.ollama_client import OllamaClient
from monitor_maid.policy import compile_denylist
from monitor_maid.xai_client import XAIClient


class RecoveryDialog(QDialog):
    def __init__(self, title: str, status: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Recover session")
        self.choice = "resume"
        layout = QVBoxLayout(self)
        state = "was still open" if status == "open" else "was interrupted while writing a summary"
        label = QLabel(
            f"Monitor Maid did not exit cleanly.\n\n"
            f"“{title}” {state}. You can resume it, ask the local model for a summary now, "
            "or close it without a summary."
        )
        label.setWordWrap(True)
        layout.addWidget(label)
        row = QHBoxLayout()
        resume = QPushButton("Resume")
        resume.setObjectName("primary")
        summarise = QPushButton("Summarise and close")
        abandon = QPushButton("Close without summary")
        row.addWidget(resume)
        row.addWidget(summarise)
        row.addWidget(abandon)
        layout.addLayout(row)
        resume.clicked.connect(lambda: self._pick("resume"))
        summarise.clicked.connect(lambda: self._pick("summarise"))
        abandon.clicked.connect(lambda: self._pick("abandon"))
        self.resize(520, 180)

    def _pick(self, choice: str) -> None:
        self.choice = choice
        self.accept()


class AskDialog(QDialog):
    def __init__(self, title: str, intro: str, default: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(560, 280)
        layout = QVBoxLayout(self)
        label = QLabel(intro)
        label.setWordWrap(True)
        layout.addWidget(label)
        self.edit = QPlainTextEdit(default)
        layout.addWidget(self.edit, 1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def question(self) -> str:
        return self.edit.toPlainText().strip()


class ApprovalDialog(QDialog):
    def __init__(self, request: ApprovalRequest, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Host command" if not request.blocked else "Command blocked")
        self.resize(680, 480)
        self.outcome = ApprovalResult("reject")
        layout = QVBoxLayout(self)
        intro = QLabel(f"{request.op}  ·  {request.reason}")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        if request.warnings:
            warning = QLabel("\n".join(request.warnings))
            warning.setObjectName("warning")
            warning.setWordWrap(True)
            layout.addWidget(warning)
        form = QFormLayout()
        self.command = QPlainTextEdit(request.command)
        self.command.setMinimumHeight(80)
        self.path = QLineEdit(request.path or "")
        self.file_content = QPlainTextEdit(request.file_content or "")
        self.cwd = QLineEdit(request.cwd or "")
        if request.op in {"shell", "start_process"}:
            form.addRow("Command", self.command)
            form.addRow("Working directory", self.cwd)
        elif request.op == "write_file":
            form.addRow("Path", self.path)
            form.addRow("Content", self.file_content)
        elif request.op == "read_file":
            form.addRow("Path", self.path)
        else:
            self.command.setReadOnly(True)
            form.addRow("Request", self.command)
        layout.addLayout(form)
        if request.blocked:
            self.command.setReadOnly(True)
            self.path.setReadOnly(True)
            self.file_content.setReadOnly(True)
            self.cwd.setReadOnly(True)
        if request.review_text or request.review_error:
            review = QPlainTextEdit(request.review_error or request.review_text or "")
            review.setReadOnly(True)
            review.setMinimumHeight(80)
            layout.addWidget(QLabel("Grok review (the local model does not see this)"))
            layout.addWidget(review)
        row = QHBoxLayout()
        self.reject_button = QPushButton("Close" if request.blocked else "Reject")
        self.review_button = QPushButton("Ask Grok to review")
        self.approve_button = QPushButton("Approve")
        self.approve_button.setObjectName("primary")
        self.review_button.setEnabled(request.allow_review)
        if request.blocked:
            self.approve_button.setEnabled(False)
            self.review_button.setEnabled(False)
        row.addWidget(self.reject_button)
        row.addWidget(self.review_button)
        row.addStretch(1)
        row.addWidget(self.approve_button)
        layout.addLayout(row)
        self.reject_button.clicked.connect(self.reject)
        self.approve_button.clicked.connect(self._approve)
        self.review_button.clicked.connect(self._review)

    def _capture(self, decision: str) -> ApprovalResult:
        return ApprovalResult(
            decision=decision,
            command=self.command.toPlainText(),
            path=self.path.text().strip(),
            file_content=self.file_content.toPlainText(),
            cwd=self.cwd.text().strip(),
        )

    def _approve(self) -> None:
        self.outcome = self._capture("approve")
        self.accept()

    def _review(self) -> None:
        self.outcome = self._capture("review")
        self.accept()


class ForwardDialog(QDialog):
    def __init__(self, request: ForwardRequest, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Send to the local model")
        self.resize(640, 360)
        self.accepted_forward = False
        layout = QVBoxLayout(self)
        intro = QLabel(
            "This text would be sent to the local model as a user message. "
            "The local model is not told where it came from."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        if request.warnings:
            warning = QLabel("\n".join(request.warnings))
            warning.setObjectName("warning")
            warning.setWordWrap(True)
            layout.addWidget(warning)
        body = QPlainTextEdit(request.text)
        body.setReadOnly(True)
        layout.addWidget(body, 1)
        row = QHBoxLayout()
        reject = QPushButton("Don't send")
        accept = QPushButton("Send to local model")
        accept.setObjectName("primary")
        row.addWidget(reject)
        row.addStretch(1)
        row.addWidget(accept)
        layout.addLayout(row)
        reject.clicked.connect(self.reject)
        accept.clicked.connect(self._accept)

    def _accept(self) -> None:
        self.accepted_forward = True
        self.accept()


def select_installed_model(current: str, names: list[str]) -> tuple[str, list[str], str]:
    """Pick the combo value after Ollama reports `names`.

    An editable combo keeps whatever text was already there. If that text is
    not an installed model, the closed field still looks unchanged after a
    refresh, and the completer hides every non-matching row. Snap to the
    installed name instead.
    """
    current = current.strip()
    cleaned: list[str] = []
    seen: set[str] = set()
    for name in names:
        name = name.strip()
        if name and name not in seen:
            seen.add(name)
            cleaned.append(name)
    if not cleaned:
        return current, [], "Ollama reported no installed models."
    if current in cleaned:
        return current, cleaned, "Installed: " + ", ".join(cleaned)
    canonical = f"{current}:latest" if current and ":" not in current else ""
    if canonical in cleaned:
        return canonical, cleaned, "Installed: " + ", ".join(cleaned)
    chosen = cleaned[0]
    if current:
        note = f"{current} is not installed. Selected {chosen}."
    else:
        note = f"Selected {chosen}."
    note += " Save to use it for this session."
    if len(cleaned) > 1:
        note += " Installed: " + ", ".join(cleaned)
    return chosen, cleaned, note


class SettingsDialog(QDialog):
    _probe_done = pyqtSignal(int, bool, str, str)

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.setWindowTitle("Settings")
        self.resize(720, 680)
        self._probe_gen = 0
        self._listed = False
        self._probe_done.connect(self._show_probe, Qt.ConnectionType.QueuedConnection)
        settings = Settings.load(db)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.ollama_url = QLineEdit(settings.ollama_url)
        self.ollama_model = QComboBox()
        self.ollama_model.setEditable(True)
        self.ollama_model.setCompleter(None)
        self.ollama_model.setCurrentText(settings.ollama_model)
        self.model_note = QLabel("Refresh models to list what Ollama has installed.")
        self.model_note.setObjectName("muted")
        self.model_note.setWordWrap(True)
        self.ollama_timeout = QSpinBox()
        self.ollama_timeout.setRange(10, 3600)
        self.ollama_timeout.setValue(settings.ollama_timeout)
        refresh = QPushButton("Refresh models")
        refresh.clicked.connect(self._refresh_models)
        model_row = QWidget()
        model_layout = QHBoxLayout(model_row)
        model_layout.setContentsMargins(0, 0, 0, 0)
        model_layout.addWidget(self.ollama_model, 1)
        model_layout.addWidget(refresh)
        self.xai_key = QLineEdit(settings.xai_api_key)
        self.xai_key.setEchoMode(QLineEdit.EchoMode.Password)
        if env_key_set():
            self.xai_key.setPlaceholderText("Ignored while XAI_API_KEY is set")
        self.xai_model = QLineEdit(settings.xai_model)
        self.xai_base = QLineEdit(settings.xai_base_url)
        self.xai_timeout = QSpinBox()
        self.xai_timeout.setRange(10, 3600)
        self.xai_timeout.setValue(settings.xai_timeout)
        self.exec_mode = QComboBox()
        self.relay_mode = QComboBox()
        for mode, label in MODE_LABELS.items():
            self.exec_mode.addItem(label, mode)
            self.relay_mode.addItem(label, mode)
        self.exec_mode.setCurrentIndex(max(0, self.exec_mode.findData(settings.default_execution_mode)))
        self.relay_mode.setCurrentIndex(max(0, self.relay_mode.findData(settings.default_grok_relay_mode)))
        self.auto_limit = QSpinBox()
        self.auto_limit.setRange(0, 100)
        self.auto_limit.setValue(settings.default_auto_limit)
        self.relay_limit = QSpinBox()
        self.relay_limit.setRange(0, 100)
        self.relay_limit.setValue(settings.default_grok_relay_limit)
        self.command_timeout = QSpinBox()
        self.command_timeout.setRange(1, 600)
        self.command_timeout.setValue(settings.command_timeout)
        self.summary_timeout = QSpinBox()
        self.summary_timeout.setRange(5, 600)
        self.summary_timeout.setValue(settings.summary_timeout)
        self.budget = QSpinBox()
        self.budget.setRange(2000, 500_000)
        self.budget.setSingleStep(1000)
        self.budget.setValue(settings.context_budget)
        self.review_first = QCheckBox("Ask Grok to review a command before it can run automatically")
        self.review_first.setChecked(settings.grok_review_before_exec)
        self.monitor_default = QCheckBox("Open new sessions in monitor mode")
        self.monitor_default.setChecked(settings.monitor_default)
        self.denylist = QPlainTextEdit(settings.denylist)
        self.denylist.setMinimumHeight(140)
        form.addRow("Ollama URL", self.ollama_url)
        form.addRow("Ollama model", model_row)
        form.addRow("", self.model_note)
        form.addRow("Ollama timeout (s)", self.ollama_timeout)
        form.addRow("xAI API key", self.xai_key)
        form.addRow("xAI model", self.xai_model)
        form.addRow("xAI base URL", self.xai_base)
        form.addRow("xAI timeout (s)", self.xai_timeout)
        form.addRow("Default execution", self.exec_mode)
        form.addRow("Auto limit N", self.auto_limit)
        form.addRow("Default Grok forward", self.relay_mode)
        form.addRow("Forward limit N", self.relay_limit)
        form.addRow("Command timeout (s)", self.command_timeout)
        form.addRow("Summary timeout (s)", self.summary_timeout)
        form.addRow("Context budget (chars)", self.budget)
        layout.addLayout(form)
        if env_key_set():
            note = QLabel("XAI_API_KEY is set in the environment and will be used instead of the stored key.")
            note.setWordWrap(True)
            note.setObjectName("muted")
            layout.addWidget(note)
        layout.addWidget(self.review_first)
        layout.addWidget(self.monitor_default)
        layout.addWidget(QLabel("Dangerous-command denylist (one regex per line)"))
        layout.addWidget(self.denylist, 1)
        probes = QHBoxLayout()
        test_ollama = QPushButton("Test Ollama")
        test_xai = QPushButton("Test xAI")
        test_ollama.clicked.connect(self._test_ollama)
        test_xai.clicked.connect(self._test_xai)
        probes.addWidget(test_ollama)
        probes.addWidget(test_xai)
        probes.addStretch(1)
        layout.addLayout(probes)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _save(self) -> None:
        _patterns, errors = compile_denylist(self.denylist.toPlainText())
        if errors:
            QMessageBox.warning(self, "Denylist", "Fix these regexes before saving:\n" + "\n".join(errors))
            return
        mode = self.exec_mode.currentData() or "manual"
        relay = self.relay_mode.currentData() or "manual"
        settings = Settings(
            ollama_url=self.ollama_url.text().strip(),
            ollama_model=self.ollama_model.currentText().strip(),
            ollama_timeout=self.ollama_timeout.value(),
            xai_api_key=self.xai_key.text(),
            xai_model=self.xai_model.text().strip(),
            xai_base_url=self.xai_base.text().strip(),
            xai_timeout=self.xai_timeout.value(),
            default_execution_mode=str(mode),
            default_auto_limit=self.auto_limit.value(),
            default_grok_relay_mode=str(relay),
            default_grok_relay_limit=self.relay_limit.value(),
            command_timeout=self.command_timeout.value(),
            summary_timeout=self.summary_timeout.value(),
            context_budget=self.budget.value(),
            denylist=self.denylist.toPlainText(),
            grok_review_before_exec=self.review_first.isChecked(),
            monitor_default=self.monitor_default.isChecked(),
        )
        settings.save(self.db)
        self.accept()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if not self._listed:
            self._listed = True
            self._probe("ollama-list", quiet=True)

    def _refresh_models(self) -> None:
        self._probe("ollama-list")

    def _test_ollama(self) -> None:
        self._probe("ollama")

    def _test_xai(self) -> None:
        self._probe("xai")

    def _probe(self, kind: str, *, quiet: bool = False) -> None:
        url = self.ollama_url.text().strip()
        model = self.ollama_model.currentText().strip()
        base = self.xai_base.text().strip()
        xai_model = self.xai_model.text().strip()
        key = self.xai_key.text().strip()
        draft = Settings(xai_api_key=key, xai_base_url=base, xai_model=xai_model, ollama_url=url)
        key = effective_api_key(draft)
        self._probe_gen += 1
        generation = self._probe_gen
        if kind != "xai":
            self.model_note.setText("Contacting Ollama…")

        def work() -> None:
            try:
                if kind == "xai":
                    reply = XAIClient(base, key).complete(
                        xai_model,
                        [
                            {"role": "system", "content": "Reply with the single word pong."},
                            {"role": "user", "content": "ping"},
                        ],
                        30,
                    )
                    message = "xAI replied: " + reply.text[:200]
                else:
                    names = OllamaClient(url).list_models()
                    if kind == "ollama-list":
                        message = "MODELS\n" + "\n".join(names)
                    else:
                        message = "Ollama models: " + (", ".join(names) if names else "(none)")
            except Exception as exc:  # noqa: BLE001 — shown in the dialog
                message = str(exc)
            self._probe_done.emit(generation, quiet, kind, message)

        threading.Thread(target=work, daemon=True).start()

    def _show_probe(self, generation: int, quiet: bool, kind: str, message: str) -> None:
        if generation != self._probe_gen or not self.isVisible():
            return
        if kind == "ollama-list" and message.startswith("MODELS\n"):
            names = [line for line in message.splitlines()[1:] if line]
            chosen, cleaned, note = select_installed_model(self.ollama_model.currentText(), names)
            self.ollama_model.blockSignals(True)
            self.ollama_model.clear()
            self.ollama_model.addItems(cleaned)
            self.ollama_model.setCurrentText(chosen)
            self.ollama_model.blockSignals(False)
            self.model_note.setText(note)
            return
        if kind != "xai":
            self.model_note.setText(message)
        if not quiet:
            QMessageBox.information(self, "Connection", message)
