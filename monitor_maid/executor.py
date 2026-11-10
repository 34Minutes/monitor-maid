"""Run an approved host instruction.

shell=True is intentional: the documented convention is a shell command.
Nothing reaches this module unless policy already allowed it, and catastrophic
patterns are checked again here so a dialog edit cannot bypass them.
stdin is DEVNULL. Interactive commands are rejected rather than hung.

A Mac app opened from Finder does not receive the Terminal PATH. Host
commands therefore take the environment of the user's login shell, which is
what `ollama` and other Homebrew programs need.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("monitor_maid.executor")
_ENV_MARKER = "___MONITOR_MAID_ENV___"
_ENV_CACHE: dict[str, str] | None = None

from monitor_maid.models import Instruction
from monitor_maid.policy import (
    assess_instruction,
    catastrophic_reasons,
    escapes_cwd,
    is_protected_write,
)

MAX_WRITE_CHARS = 1_000_000
MAX_READ_CHARS = 200_000
MAX_OUTPUT_CHARS = 100_000


@dataclass
class ExecResult:
    ok: bool
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool = False
    refused: bool = False
    error: str | None = None
    pid: int | None = None
    log_path: str | None = None


def _truncate(text: str) -> str:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    extra = len(text) - MAX_OUTPUT_CHARS
    return text[:MAX_OUTPUT_CHARS] + f"\n…({extra} more characters truncated)"


def _refuse(message: str) -> ExecResult:
    return ExecResult(ok=False, exit_code=None, stdout="", stderr="", refused=True, error=message)


def execute(
    instruction: Instruction,
    *,
    decision: str,
    timeout: int,
    cwd_default: str,
    denylist,
    allowed_pids: dict[int, str],
    log_dir: Path,
) -> ExecResult:
    outside = escapes_cwd(instruction.path, cwd_default)
    safety = assess_instruction(instruction, denylist, outside_cwd=outside)
    if safety.level == "block":
        return _refuse(" ".join(safety.reasons))
    if decision == "auto" and (safety.level != "ok" or safety.ambiguous):
        return _refuse("Automatic execution refused a command that needs approval.")
    if instruction.op == "shell":
        return _run_shell(instruction.command or "", _resolve_cwd(instruction.cwd, cwd_default), timeout, wait=True)
    if instruction.op == "start_process":
        return _start(instruction.command or "", _resolve_cwd(instruction.cwd, cwd_default), log_dir)
    if instruction.op == "stop_process":
        return _stop(instruction.pid, allowed_pids)
    if instruction.op == "read_file":
        return _read(instruction.path or "", cwd_default)
    if instruction.op == "write_file":
        return _write(instruction.path or "", instruction.content or "", cwd_default)
    return _refuse(f"Unsupported op {instruction.op!r}.")


def command_environment() -> dict[str, str]:
    """Environment for a host command, cached after the first lookup."""
    global _ENV_CACHE
    if _ENV_CACHE is None:
        _ENV_CACHE = _build_command_environment()
    return _ENV_CACHE


def parse_env_block(raw: bytes) -> dict[str, str]:
    """Parse `env -0` output that follows the marker, ignoring shell startup text."""
    token = _ENV_MARKER.encode() + b"\0"
    index = raw.find(token)
    if index < 0:
        return {}
    parsed: dict[str, str] = {}
    for item in raw[index + len(token) :].split(b"\0"):
        if not item or b"=" not in item:
            continue
        key, value = item.split(b"=", 1)
        try:
            name = key.decode()
            text = value.decode()
        except UnicodeDecodeError:
            continue
        if name:
            parsed[name] = text
    return parsed


def _login_shell_env() -> dict[str, str] | None:
    """Read the environment a Terminal login would export.

    `-l` reads the profile that adds Homebrew. `-i` reads the interactive
    file that adds the rest of the user's PATH. Startup text stays before
    the marker, so it is not treated as a variable.
    """
    shell = os.environ.get("SHELL", "").strip() or "/bin/zsh"
    if not Path(shell).is_file():
        shell = "/bin/zsh" if Path("/bin/zsh").is_file() else "/bin/sh"
    script = f"printf '%s\\0' {_ENV_MARKER}; /usr/bin/env -0"
    try:
        proc = subprocess.run(
            [shell, "-lic", script],
            capture_output=True,
            timeout=15,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    parsed = parse_env_block(proc.stdout or b"")
    if "PATH" not in parsed:
        return None
    return parsed


def _with_user_bins(path: str, extras: list[Path] | None = None) -> str:
    """Put the usual per-user bin directories on PATH when they exist."""
    home = Path.home()
    candidates = extras or [
        home / ".grok" / "bin",
        home / ".local" / "bin",
        Path("/opt/homebrew/bin"),
        Path("/opt/homebrew/sbin"),
        Path("/usr/local/bin"),
    ]
    parts = [item for item in path.split(":") if item]
    seen = set(parts)
    prefix: list[str] = []
    for extra in candidates:
        text = str(extra)
        if text in seen or not extra.is_dir():
            continue
        prefix.append(text)
        seen.add(text)
    return ":".join(prefix + parts)


def _build_command_environment() -> dict[str, str]:
    merged = os.environ.copy()
    shell_env = _login_shell_env()
    if shell_env:
        merged.update(shell_env)
        log.info("Host commands are using the login-shell environment.")
    else:
        log.info("The login shell did not report an environment. Host commands use the usual user bin directories.")
    merged["PATH"] = _with_user_bins(merged.get("PATH", ""))
    if not merged.get("HOME"):
        merged["HOME"] = str(Path.home())
    return merged


def _resolve_cwd(cwd: str | None, default: str) -> str:
    if not cwd or cwd == ".":
        return default
    path = Path(cwd)
    if not path.is_absolute():
        path = Path(default) / path
    return str(path)


def _run_shell(command: str, cwd: str, timeout: int, *, wait: bool) -> ExecResult:
    reasons = catastrophic_reasons(command)
    if reasons:
        return _refuse(" ".join(reasons))
    if not Path(cwd).is_dir():
        return _refuse(f"Working directory does not exist: {cwd}")
    if not wait:
        return _refuse("Internal error: _run_shell wait flag.")
    try:
        proc = subprocess.Popen(
            command,
            shell=True,
            cwd=cwd,
            env=command_environment(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
    except OSError as exc:
        return ExecResult(ok=False, exit_code=None, stdout="", stderr="", error=str(exc))
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_group(proc.pid)
        stdout, stderr = proc.communicate()
        return ExecResult(
            ok=False,
            exit_code=proc.returncode,
            stdout=_truncate(stdout or ""),
            stderr=_truncate(stderr or ""),
            timed_out=True,
            error=f"Timed out after {timeout}s and the process was killed.",
        )
    exit_code = proc.returncode
    return ExecResult(
        ok=exit_code == 0,
        exit_code=exit_code,
        stdout=_truncate(stdout or ""),
        stderr=_truncate(stderr or ""),
        error=None if exit_code == 0 else f"Exited with status {exit_code}.",
    )


def _start(command: str, cwd: str, log_dir: Path) -> ExecResult:
    reasons = catastrophic_reasons(command)
    if reasons:
        return _refuse(" ".join(reasons))
    if not Path(cwd).is_dir():
        return _refuse(f"Working directory does not exist: {cwd}")
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"proc-{os.getpid()}-{_safe_stamp()}.log"
    handle = open(log_path, "a", encoding="utf-8")  # noqa: SIM115 — closed after the child inherits it
    try:
        proc = subprocess.Popen(
            command,
            shell=True,
            cwd=cwd,
            env=command_environment(),
            stdin=subprocess.DEVNULL,
            stdout=handle,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
    except OSError as exc:
        handle.close()
        return ExecResult(ok=False, exit_code=None, stdout="", stderr="", error=str(exc))
    finally:
        handle.close()
    return ExecResult(
        ok=True,
        exit_code=None,
        stdout=f"Started pid {proc.pid}. Output is appended to {log_path}.",
        stderr="",
        pid=proc.pid,
        log_path=str(log_path),
    )


def _stop(pid: int | None, allowed_pids: dict[int, str]) -> ExecResult:
    if pid is None:
        return _refuse("stop_process requires a pid.")
    expected = allowed_pids.get(pid)
    if not expected:
        return _refuse("Refusing to signal a process this app did not start.")
    live = _process_command(pid)
    if live is None:
        return ExecResult(
            ok=True,
            exit_code=0,
            stdout=f"Process {pid} is not running.",
            stderr="",
        )
    if expected not in live and live not in expected:
        return _refuse(
            f"Refusing to signal pid {pid}: it no longer matches the command this app started."
        )
    _kill_group(pid)
    return ExecResult(ok=True, exit_code=0, stdout=f"Signalled pid {pid} to stop.", stderr="")


def _read(path_text: str, cwd_default: str) -> ExecResult:
    path = _resolve_file(path_text, cwd_default)
    if is_protected_write(str(path)) and str(path).startswith("/dev/"):
        return _refuse("Refusing to read a device path.")
    if not path.is_file():
        return _refuse(f"Not a file: {path}")
    raw = path.read_bytes()[: MAX_READ_CHARS + 1]
    if b"\x00" in raw[:1024]:
        return _refuse(f"Refusing to read binary file {path}.")
    truncated = len(raw) > MAX_READ_CHARS
    text = raw[:MAX_READ_CHARS].decode("utf-8", errors="replace")
    note = "\n…(file truncated)" if truncated else ""
    return ExecResult(ok=True, exit_code=0, stdout=text + note, stderr="")


def _write(path_text: str, content: str, cwd_default: str) -> ExecResult:
    if len(content) > MAX_WRITE_CHARS:
        return _refuse(f"Refusing to write more than {MAX_WRITE_CHARS} characters.")
    path = _resolve_file(path_text, cwd_default)
    if is_protected_write(str(path)):
        return _refuse(f"Refusing to write under a protected path: {path}")
    if path.exists() and path.is_dir():
        return _refuse(f"Refusing to overwrite a directory: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return ExecResult(
        ok=True,
        exit_code=0,
        stdout=f"Wrote {len(content)} characters to {path}.",
        stderr="",
    )


def _resolve_file(path_text: str, cwd_default: str) -> Path:
    path = Path(path_text)
    if not path.is_absolute():
        path = Path(cwd_default) / path
    return path.resolve()


def _process_command(pid: int) -> str | None:
    try:
        out = subprocess.check_output(
            ["ps", "-p", str(pid), "-o", "command="],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    text = out.strip()
    return text or None


def _kill_group(pid: int) -> None:
    try:
        os.killpg(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            return
    try:
        # Give the group a moment, then force it. communicate() already returned
        # on timeout; this second signal covers orphans in the group.
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass


def _safe_stamp() -> str:
    import time

    return str(int(time.time() * 1000))
