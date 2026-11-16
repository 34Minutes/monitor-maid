"""Headless Grok Build sessions through the `grok` command.

Monitor Maid does not open the Grok TUI and does not call the xAI HTTP API.
Every Grok turn is one process:

    grok --no-auto-update --cwd <dir> -p "<prompt>" [session flags] [other flags]

The process is started with stdin closed, so a missing flag cannot fall
through into the interactive UI. streaming-json is the default because each
line is an event the chat can show as it arrives. `end.sessionId` is the id
to pass to `--resume` next time.

Extend this module in three places:

- `build_command` when a new flag should be part of every run.
- `parse_stdout_line` and `format_events` when a new streaming-json `type`
  should show up in the chat.
- `GrokBuildConfig` when a choice should be remembered across launches.
  The file is `grok-build.json` next to the SQLite database.

`MONITOR_MAID_GROK` overrides the binary used for tests. The app otherwise
looks up `grok` on the login-shell PATH, the same one host commands use,
so a Finder launch can see `~/.grok/bin`.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from monitor_maid.executor import command_environment

# A prompt longer than this is passed with --prompt-file. The command line
# on macOS is about 256 KiB, and a quoted transcript can pass that.
_PROMPT_FILE_LIMIT = 80_000

# Flags the app already sets. Extra flags must not repeat them, or clap
# would see two values and the session id would be ambiguous.
_RESERVED_FLAGS = frozenset(
    {
        "-p",
        "--single",
        "--prompt-file",
        "--cwd",
        "--resume",
        "-r",
        "--session-id",
        "-s",
        "--output-format",
        "--no-auto-update",
        "--continue",
        "-c",
        "--model",
        "-m",
        "--always-approve",
        "--yolo",
    }
)

_AUTH_MARKERS = (
    "not signed in",
    "not logged in",
    "login required",
    "authentication",
    "unauthorized",
    "invalid api key",
    "missing api key",
    "xai_api_key",
)

_SESSION_ROW = re.compile(
    r"^(?P<id>[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})"
    r"\s+(?P<created>\S+)"
    r"\s+(?P<updated>\S+)"
    r"\s+(?P<status>\S+)"
    r"(?:\s+(?P<summary>.*))?$"
)

_MODEL_MARK = re.compile(r"^\s*[*-]\s+(\S+)")

OUTPUT_FORMATS = ("streaming-json", "plain")


class GrokBuildError(Exception):
    """A grok command that did not produce a usable reply.

    `kind` is `not_found`, `auth`, `exit`, `timeout`, or `bad_args`.
    `text` and `stderr` are whatever the process printed before it failed,
    so the chat can still show a partial answer.
    """

    def __init__(
        self,
        message: str,
        kind: str = "exit",
        *,
        session_id: str | None = None,
        text: str = "",
        stderr: str = "",
        exit_code: int | None = None,
    ):
        super().__init__(message)
        self.kind = kind
        self.session_id = session_id
        self.text = text
        self.stderr = stderr
        self.exit_code = exit_code


@dataclass
class ListedSession:
    """One row of `grok sessions list`."""

    session_id: str
    created: str
    updated: str
    status: str
    summary: str


@dataclass
class GrokBuildConfig:
    """Last directory, session, and flags. Stored as JSON beside the database."""

    cwd: str = ""
    session_id: str = ""
    # True once a session id is the one to resume. A new session stays
    # unbound until the first run reports an id.
    bound: bool = False
    # Optional UUID for `--session-id` on the next new session only.
    pending_session_id: str = ""
    model: str = ""
    always_approve: bool = False
    output_format: str = "streaming-json"
    extra_flags: str = ""

    def to_dict(self) -> dict:
        return {
            "cwd": self.cwd,
            "session_id": self.session_id,
            "bound": self.bound,
            "pending_session_id": self.pending_session_id,
            "model": self.model,
            "always_approve": self.always_approve,
            "output_format": self.output_format
            if self.output_format in OUTPUT_FORMATS
            else "streaming-json",
            "extra_flags": self.extra_flags,
        }

    @classmethod
    def from_dict(cls, raw: object) -> GrokBuildConfig:
        data = raw if isinstance(raw, dict) else {}
        output = str(data.get("output_format") or "streaming-json")
        if output not in OUTPUT_FORMATS:
            output = "streaming-json"
        return cls(
            cwd=str(data.get("cwd") or ""),
            session_id=str(data.get("session_id") or ""),
            bound=bool(data.get("bound")),
            pending_session_id=str(data.get("pending_session_id") or ""),
            model=str(data.get("model") or ""),
            always_approve=bool(data.get("always_approve")),
            output_format=output,
            extra_flags=str(data.get("extra_flags") or ""),
        )


@dataclass
class RunSpec:
    """One headless prompt. `bind` is recorded by the caller, not by grok."""

    cwd: str
    prompt: str
    resume_id: str | None = None
    new_session_id: str | None = None
    model: str | None = None
    always_approve: bool = False
    output_format: str = "streaming-json"
    extra_args: list[str] = field(default_factory=list)


@dataclass
class StreamEvent:
    """One stdout event or one stderr line."""

    source: str  # stdout or stderr
    kind: str
    text: str = ""
    session_id: str | None = None
    stop_reason: str | None = None


@dataclass
class RunResult:
    exit_code: int
    session_id: str | None
    text: str
    display: str
    stderr: str
    refused: bool = False


def config_path(db_path: Path) -> Path:
    """JSON file next to the SQLite database, so a test database stays isolated."""
    return Path(db_path).parent / "grok-build.json"


def load_config(path: Path) -> GrokBuildConfig:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return GrokBuildConfig()
    return GrokBuildConfig.from_dict(raw)


def save_config(config: GrokBuildConfig, path: Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(config.to_dict(), indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(target, 0o600)
    except OSError:
        pass


def _search_path() -> str:
    """PATH from the login shell, including directories such as ~/.grok/bin."""
    return command_environment().get("PATH") or os.defpath


def grok_available() -> bool:
    """True when a grok binary can be resolved. Does not start it."""
    override = os.environ.get("MONITOR_MAID_GROK", "").strip()
    if override:
        return Path(override).is_file()
    return shutil.which("grok", path=_search_path()) is not None


def resolve_binary(binary: str | None = None) -> str:
    if binary:
        return binary
    override = os.environ.get("MONITOR_MAID_GROK", "").strip()
    if override:
        if not Path(override).is_file():
            raise GrokBuildError(
                f"MONITOR_MAID_GROK does not point at a file: {override}",
                "not_found",
            )
        return override
    found = shutil.which("grok", path=_search_path())
    if not found:
        raise GrokBuildError(
            "grok was not found on PATH. Install Grok Build, then start "
            "Monitor Maid again.",
            "not_found",
        )
    return found


def validate_directory(cwd: str) -> Path:
    text = (cwd or "").strip()
    if not text:
        raise GrokBuildError("Choose a directory for the Grok Build session.", "bad_args")
    path = Path(text).expanduser()
    if not path.is_dir():
        raise GrokBuildError(f"Directory does not exist: {path}", "bad_args")
    return path.resolve()


def normalise_session_uuid(value: str) -> str:
    """`--session-id` creates a new session and only accepts a UUID."""
    text = (value or "").strip()
    if not text:
        raise GrokBuildError("A new session id has to be a UUID.", "bad_args")
    try:
        parsed = uuid.UUID(text)
    except ValueError as exc:
        raise GrokBuildError(
            "A new session id has to be a UUID, for example from uuidgen.",
            "bad_args",
        ) from exc
    return str(parsed).lower()


def parse_extra_flags(text: str) -> list[str]:
    """Split the extra-flags box. Reject flags this app already sets."""
    raw = (text or "").strip()
    if not raw:
        return []
    try:
        parts = shlex.split(raw, posix=(os.name != "nt"))
    except ValueError as exc:
        raise GrokBuildError(f"Extra flags could not be parsed: {exc}", "bad_args") from exc
    for part in parts:
        if part in _RESERVED_FLAGS:
            raise GrokBuildError(
                f"{part} is already set by Monitor Maid. Remove it from extra flags.",
                "bad_args",
            )
    return parts


def build_command(spec: RunSpec, binary: str) -> list[str]:
    """Argv for one headless run. The prompt is an argument, never a shell string."""
    if spec.resume_id and spec.new_session_id:
        raise GrokBuildError(
            "Use --resume for an existing session, or --session-id for a new one.",
            "bad_args",
        )
    output = spec.output_format if spec.output_format in OUTPUT_FORMATS else "streaming-json"
    command = [
        binary,
        "--no-auto-update",
        "--cwd",
        spec.cwd,
        "-p",
        spec.prompt,
    ]
    if spec.resume_id:
        command.extend(["--resume", spec.resume_id])
    elif spec.new_session_id:
        command.extend(["--session-id", spec.new_session_id])
    if spec.model:
        command.extend(["--model", spec.model])
    if spec.always_approve:
        command.append("--always-approve")
    command.extend(["--output-format", output])
    command.extend(spec.extra_args)
    return command


def parse_sessions_list(text: str) -> list[ListedSession]:
    """Parse `grok sessions list`. Group labels and the header are ignored.

    A data row starts with a UUID, then the created date, the updated date,
    a status word, and the rest of the line as the summary.
    """
    found: list[ListedSession] = []
    for line in text.splitlines():
        match = _SESSION_ROW.match(line.strip())
        if not match:
            continue
        found.append(
            ListedSession(
                session_id=match.group("id"),
                created=match.group("created"),
                updated=match.group("updated"),
                status=match.group("status"),
                summary=(match.group("summary") or "").strip(),
            )
        )
    return found


def parse_models(text: str) -> list[str]:
    """Names from `grok models`. Lines look like `* grok-4.7 (default)`."""
    names: list[str] = []
    for line in text.splitlines():
        match = _MODEL_MARK.match(line)
        if not match:
            continue
        name = re.sub(r"\s*\(default\)\s*$", "", line.strip().lstrip("*-").strip()).strip()
        if name and name not in names:
            names.append(name)
    return names


def parse_stdout_line(line: str, output_format: str) -> StreamEvent | None:
    """Turn one stdout line into an event. Blank lines are dropped.

    Plain output is kept as text. streaming-json is parsed by `type`.
    A line that is not JSON is still shown, so a warning on stdout is not lost.
    """
    raw = line.rstrip("\n")
    if not raw.strip():
        return None
    if output_format != "streaming-json":
        return StreamEvent(source="stdout", kind="text", text=raw + "\n")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return StreamEvent(source="stdout", kind="text", text=raw + "\n")
    if not isinstance(payload, dict):
        return StreamEvent(source="stdout", kind="text", text=raw + "\n")
    kind = str(payload.get("type") or "")
    session_id = _session_field(payload)
    if kind == "text":
        return StreamEvent(
            source="stdout",
            kind="text",
            text=str(payload.get("data") or ""),
            session_id=session_id,
        )
    if kind == "thought":
        data = str(payload.get("data") or "").strip()
        if not data:
            return None
        return StreamEvent(source="stdout", kind="thought", text=data, session_id=session_id)
    if kind == "tool_call":
        name = payload.get("toolName") or payload.get("title") or "tool"
        return StreamEvent(
            source="stdout",
            kind="tool_call",
            text=f"tool: {name} started",
            session_id=session_id,
        )
    if kind == "tool_call_update":
        name = payload.get("toolName") or payload.get("title") or "tool"
        status = payload.get("status") or "update"
        return StreamEvent(
            source="stdout",
            kind="tool_update",
            text=f"tool: {name} {status}",
            session_id=session_id,
        )
    if kind == "error":
        return StreamEvent(
            source="stdout",
            kind="error",
            text=str(payload.get("message") or raw),
            session_id=session_id,
        )
    if kind == "end":
        return StreamEvent(
            source="stdout",
            kind="end",
            session_id=session_id,
            stop_reason=_text_field(payload.get("stopReason")),
        )
    # usage, plan, and later event types stay out of the chat until we
    # teach format_events about them. The session id is still captured.
    if session_id:
        return StreamEvent(source="stdout", kind=kind or "raw", session_id=session_id)
    return None


# Grok prints this on stderr when --resume finds the session in another
# directory. It is a status note, not a failure.
_SESSION_LOCATION = re.compile(
    r"^Session [0-9a-fA-F-]{8,} found locally \(originally in .+\)$"
)


def is_session_location_notice(line: str) -> bool:
    return _SESSION_LOCATION.match(line.strip()) is not None


def strip_grok_status(text: str) -> str:
    """Remove Grok's session-location note from chat text.

    A stderr section that is only that note disappears. Any other stderr
    line stays under the stderr label.
    """
    parts: list[str] = []
    for part in text.split("\n\n"):
        cleaned = _clean_stderr_part(part)
        if cleaned:
            parts.append(cleaned)
    return "\n\n".join(parts).strip()


def _clean_stderr_part(part: str) -> str:
    lines = part.split("\n")
    if not lines or lines[0].strip() != "stderr:":
        return part
    kept = [line for line in lines[1:] if line.strip() and not is_session_location_notice(line)]
    if not kept:
        return ""
    return "stderr:\n" + "\n".join(kept)


def format_events(events: list[StreamEvent]) -> tuple[str, str]:
    """Chat text and the assistant text used for relay fences.

    The assistant text is only the `text` chunks, joined in order. Tool
    lines are added underneath. Stderr is added only when it is more than
    Grok's session-location note, so that note does not sit under every reply.
    """
    chunks: list[str] = []
    notes: list[str] = []
    errors: list[str] = []
    stderr: list[str] = []
    for event in events:
        if event.kind == "text" and event.text:
            chunks.append(event.text)
        elif event.kind == "thought" and event.text:
            notes.append("thinking: " + event.text)
        elif event.kind in {"tool_call", "tool_update"} and event.text:
            notes.append(event.text)
        elif event.kind == "error" and event.text:
            errors.append(event.text)
        elif event.kind == "stderr" and event.text.strip():
            line = event.text.rstrip()
            if not is_session_location_notice(line):
                stderr.append(line)
    answer = "".join(chunks).strip()
    parts: list[str] = []
    if answer:
        parts.append(answer)
    if notes:
        parts.append("\n".join(notes))
    if errors:
        parts.append("error:\n" + "\n".join(errors))
    if stderr:
        parts.append("stderr:\n" + "\n".join(stderr))
    return "\n\n".join(parts).strip(), answer


def list_sessions(cwd: str, *, limit: int = 20, binary: str | None = None) -> list[ListedSession]:
    """Run `grok sessions list` with its working directory set to `cwd`.

    The list command has no `--cwd` flag. The process cwd is what selects
    the directory's sessions.
    """
    directory = validate_directory(cwd)
    exe = resolve_binary(binary)
    completed = _run(
        [exe, "sessions", "list", "--limit", str(max(1, limit))],
        cwd=str(directory),
        timeout=60,
    )
    _raise_if_failed(completed, "Could not list Grok Build sessions.")
    return parse_sessions_list(completed.stdout)


def list_models(*, binary: str | None = None) -> list[str]:
    exe = resolve_binary(binary)
    completed = _run([exe, "models"], cwd=None, timeout=60)
    _raise_if_failed(completed, "Could not list Grok models.")
    return parse_models(completed.stdout)


def probe_grok(*, binary: str | None = None) -> str:
    """Version line, or an error the settings dialog can show."""
    exe = resolve_binary(binary)
    completed = _run([exe, "--version"], cwd=None, timeout=30)
    _raise_if_failed(completed, "grok --version failed.")
    line = (completed.stdout or completed.stderr).strip()
    return line or "grok answered with no version text."


def export_session(
    session_id: str,
    output: Path,
    *,
    cwd: str,
    binary: str | None = None,
) -> Path:
    """Write `grok export <id> <file>`. The file must not already exist."""
    session = (session_id or "").strip()
    if not session:
        raise GrokBuildError("There is no Grok Build session to export.", "bad_args")
    target = Path(output)
    if target.exists():
        raise GrokBuildError(
            "That export file already exists. Choose a new name.",
            "bad_args",
        )
    directory = validate_directory(cwd)
    exe = resolve_binary(binary)
    completed = _run(
        [exe, "export", session, str(target)],
        cwd=str(directory),
        timeout=120,
    )
    _raise_if_failed(completed, "Could not export the Grok Build session.")
    return target


def delete_session(session_id: str, *, cwd: str, binary: str | None = None) -> None:
    session = (session_id or "").strip()
    if not session:
        raise GrokBuildError("There is no Grok Build session to delete.", "bad_args")
    directory = validate_directory(cwd)
    exe = resolve_binary(binary)
    completed = _run(
        [exe, "sessions", "delete", session],
        cwd=str(directory),
        timeout=60,
    )
    _raise_if_failed(completed, "Could not delete the Grok Build session.")


def run_prompt(
    spec: RunSpec,
    on_update: Callable[[str], None] | None = None,
    *,
    binary: str | None = None,
) -> RunResult:
    """Run one prompt. `on_update` receives the chat text so far.

    `on_update` may be called from a reader thread. It must only hand the
    string to the GUI thread. It must not touch SQLite or widgets.
    """
    directory = validate_directory(spec.cwd)
    exe = resolve_binary(binary)
    prompt_file: Path | None = None
    command_spec = spec
    if len(spec.prompt) > _PROMPT_FILE_LIMIT:
        handle = tempfile.NamedTemporaryFile(
            prefix="monitor-maid-prompt-",
            suffix=".txt",
            delete=False,
            mode="w",
            encoding="utf-8",
        )
        handle.write(spec.prompt)
        handle.close()
        prompt_file = Path(handle.name)
        command_spec = RunSpec(
            cwd=str(directory),
            prompt="",
            resume_id=spec.resume_id,
            new_session_id=spec.new_session_id,
            model=spec.model,
            always_approve=spec.always_approve,
            output_format=spec.output_format,
            extra_args=spec.extra_args,
        )
    try:
        command = build_command(command_spec, exe)
        if prompt_file is not None:
            # -p and the empty placeholder are replaced with --prompt-file.
            command = _swap_prompt_file(command, prompt_file)
        process = subprocess.Popen(
            command,
            cwd=str(directory),
            env=command_environment(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
    except FileNotFoundError as exc:
        _drop_prompt_file(prompt_file)
        raise GrokBuildError(
            "grok was not found on PATH. Install Grok Build, then start "
            "Monitor Maid again.",
            "not_found",
        ) from exc
    except OSError as exc:
        _drop_prompt_file(prompt_file)
        raise GrokBuildError(f"Could not start grok: {exc}", "exit") from exc

    events: list[StreamEvent] = []
    lock = threading.Lock()

    def publish(event: StreamEvent) -> None:
        with lock:
            events.append(event)
            display, _answer = format_events(events)
        if on_update and display:
            on_update(display)

    assert process.stdout is not None
    assert process.stderr is not None
    stdout_thread = threading.Thread(
        target=_read_stdout,
        args=(process.stdout, spec.output_format, publish),
        daemon=True,
    )
    stderr_thread = threading.Thread(
        target=_read_stderr,
        args=(process.stderr, publish),
        daemon=True,
    )
    stdout_thread.start()
    stderr_thread.start()
    stdout_thread.join()
    stderr_thread.join()
    code = process.wait()
    _drop_prompt_file(prompt_file)
    with lock:
        snapshot = list(events)
    display, answer = format_events(snapshot)
    stderr = "\n".join(event.text.rstrip() for event in snapshot if event.kind == "stderr")
    session_id = _session_id_from(snapshot, spec)
    refused = any(event.stop_reason == "refusal" for event in snapshot)
    if code != 0:
        kind = "auth" if _looks_like_auth(display, stderr) else "exit"
        message = _failure_message(code, display, stderr, kind)
        raise GrokBuildError(
            message,
            kind,
            session_id=session_id,
            text=display,
            stderr=stderr,
            exit_code=code,
        )
    return RunResult(
        exit_code=code,
        session_id=session_id,
        text=answer,
        display=display or answer,
        stderr=stderr,
        refused=refused,
    )


def messages_to_prompt(messages: list[dict[str, str]]) -> str:
    """One prompt for a call that used to send a message list to the HTTP API."""
    parts: list[str] = []
    for message in messages:
        content = str(message.get("content") or "").strip()
        if not content:
            continue
        role = str(message.get("role") or "user")
        parts.append(f"{role}:\n{content}")
    return "\n\n".join(parts)


def _swap_prompt_file(command: list[str], prompt_file: Path) -> list[str]:
    swapped: list[str] = []
    skip_next = False
    for part in command:
        if skip_next:
            skip_next = False
            continue
        if part == "-p":
            swapped.extend(["--prompt-file", str(prompt_file)])
            skip_next = True
            continue
        swapped.append(part)
    return swapped


def _drop_prompt_file(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.unlink()
    except OSError:
        pass


def _read_stdout(pipe, output_format: str, publish: Callable[[StreamEvent], None]) -> None:
    for line in pipe:
        event = parse_stdout_line(line, output_format)
        if event is not None:
            publish(event)
    pipe.close()


def _read_stderr(pipe, publish: Callable[[StreamEvent], None]) -> None:
    for line in pipe:
        text = line.rstrip("\n")
        if text.strip():
            publish(StreamEvent(source="stderr", kind="stderr", text=text))
    pipe.close()


def _run(command: list[str], *, cwd: str | None, timeout: int) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            cwd=cwd,
            env=command_environment(),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise GrokBuildError(
            "grok was not found on PATH. Install Grok Build, then start "
            "Monitor Maid again.",
            "not_found",
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise GrokBuildError(f"grok did not finish within {timeout}s.", "timeout") from exc


def _raise_if_failed(completed: subprocess.CompletedProcess[str], prefix: str) -> None:
    if completed.returncode == 0:
        return
    stderr = (completed.stderr or "").strip()
    stdout = (completed.stdout or "").strip()
    kind = "auth" if _looks_like_auth(stdout, stderr) else "exit"
    detail = stderr or stdout or f"exit {completed.returncode}"
    message = detail if kind == "auth" else f"{prefix} {detail}"
    if kind == "auth":
        message = (
            "Grok is not signed in. Run `grok login` in a terminal, "
            "or set XAI_API_KEY for the grok command.\n\n" + detail
        )
    raise GrokBuildError(message, kind, stderr=stderr, exit_code=completed.returncode)


def _failure_message(code: int, display: str, stderr: str, kind: str) -> str:
    if kind == "auth":
        return (
            "Grok is not signed in. Run `grok login` in a terminal, "
            "or set XAI_API_KEY for the grok command."
        )
    if code == 130:
        return "Grok was interrupted."
    if code == 143:
        return "Grok was stopped."
    detail = stderr.strip() or display.strip() or f"exit {code}"
    first = detail.splitlines()[0]
    return f"Grok Build exited {code}: {first}"


def _looks_like_auth(*chunks: str) -> bool:
    blob = "\n".join(chunks).lower()
    return any(marker in blob for marker in _AUTH_MARKERS)


def _session_id_from(events: list[StreamEvent], spec: RunSpec) -> str | None:
    found = None
    for event in events:
        if event.session_id:
            found = event.session_id
    if found:
        return found
    return spec.resume_id or spec.new_session_id


def _session_field(payload: dict) -> str | None:
    value = payload.get("sessionId") or payload.get("session_id")
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _text_field(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
