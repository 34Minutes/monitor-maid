"""Plain records shared by the database, the orchestrator, and the UI."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


NO_SUBSTANTIAL = "No substantial discussion."

ACTORS = ("user", "local_llm", "grok", "system")
EXECUTION_MODES = ("manual", "auto_n", "automatic")
MODE_LABELS = {
    "manual": "Manual approval",
    "auto_n": "Auto up to N, then manual",
    "automatic": "Fully automatic",
}


def display_actor(actor: str, kind: str, visible_to_local: bool) -> str:
    if kind == "summary":
        return "Summary"
    if kind == "conclusion":
        return "Conclusions"
    if kind == "command_result":
        return "Host"
    if kind in {"review", "verification", "refusal"} or actor == "grok":
        return "Grok"
    if actor == "user" and kind == "relay":
        return "You → local model"
    if actor == "user" and not visible_to_local:
        return "You → Grok"
    if actor == "user":
        return "You"
    if actor == "local_llm":
        return "Local model"
    if actor == "system":
        return "Host"
    return actor


@dataclass
class Session:
    id: str
    title: str
    created_at: str
    updated_at: str
    closed_at: str | None
    status: str
    execution_mode: str
    auto_limit: int
    auto_used: int
    grok_relay_mode: str
    grok_relay_limit: int
    grok_relay_used: int
    monitor_mode: bool
    ollama_model: str | None


@dataclass
class Turn:
    id: str
    session_id: str
    seq: int
    actor: str
    kind: str
    content: str
    created_at: str
    visible_to_local: bool
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Summary:
    id: str
    session_id: str
    content: str
    created_at: str


@dataclass
class Conclusion:
    id: str
    session_id: str
    content: str
    created_at: str


@dataclass
class CommandRecord:
    id: str
    session_id: str
    turn_id: str | None
    proposed_at: str
    command: str
    cwd: str | None
    kind: str
    status: str
    decision: str | None = None
    decision_reason: str | None = None
    grok_review: str | None = None
    exit_code: int | None = None
    stdout: str | None = None
    stderr: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    timeout_s: int | None = None


@dataclass
class ProcessRecord:
    pid: int
    session_id: str
    command: str
    started_at: str
    stopped_at: str | None
    log_path: str


@dataclass
class HistoryItem:
    key: str
    kind: str  # question, answer, summary, conclusion, grok
    actor: str
    created_at: str
    content: str
    source_id: str


@dataclass
class Instruction:
    op: str
    raw: str
    command: str | None = None
    path: str | None = None
    content: str | None = None
    cwd: str | None = None
    pid: int | None = None
    malformed: bool = False
    partial: bool = False
    ambiguous: bool = False
    error: str | None = None

    def shell_text(self) -> str:
        if self.op == "shell":
            return self.command or ""
        if self.op == "start_process":
            return self.command or ""
        if self.op == "stop_process":
            return f"stop pid {self.pid}" if self.pid is not None else "stop pid"
        if self.op == "read_file":
            return f"read {self.path or ''}"
        if self.op == "write_file":
            preview = (self.content or "").replace("\n", " ")
            if len(preview) > 80:
                preview = preview[:77] + "..."
            return f"write {self.path or ''} ({len(self.content or '')} chars) {preview}"
        return self.raw


@dataclass
class RelayBlock:
    text: str
    partial: bool = False


@dataclass
class SafetyAssessment:
    level: str  # ok, warn, block
    reasons: list[str]
    interactive: bool = False
    ambiguous: bool = False


@dataclass
class Job:
    kind: str
    local_text: str | None = None
    grok_text: str | None = None
    allow_relay: bool = True
    session_id: str | None = None
    on_done: Callable[[], None] | None = None
    reply_kind: str = "message"


@dataclass
class ApprovalRequest:
    op: str
    command: str
    cwd: str | None
    path: str | None
    file_content: str | None
    warnings: list[str]
    blocked: bool
    review_text: str | None
    review_error: str | None
    allow_review: bool
    reason: str


@dataclass
class ApprovalResult:
    decision: str  # approve, reject, review
    command: str | None = None
    path: str | None = None
    file_content: str | None = None
    cwd: str | None = None


@dataclass
class ForwardRequest:
    text: str
    partial: bool
    warnings: list[str]


@dataclass
class LLMReply:
    text: str
    refused: bool = False
