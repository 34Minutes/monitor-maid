from pathlib import Path

import monitor_maid.executor as executor
from monitor_maid.executor import execute, parse_env_block
from monitor_maid.models import Instruction
from monitor_maid.policy import DEFAULT_DENYLIST, compile_denylist


def _run(instruction: Instruction, tmp_path: Path, decision: str = "approved", timeout: int = 5):
    patterns, errors = compile_denylist(DEFAULT_DENYLIST)
    assert errors == []
    return execute(
        instruction,
        decision=decision,
        timeout=timeout,
        cwd_default=str(tmp_path),
        denylist=patterns,
        allowed_pids={},
        log_dir=tmp_path / "logs",
    )


def test_shell_printf_and_nonzero(tmp_path):
    ok = _run(Instruction(op="shell", raw="printf hi", command="printf hi"), tmp_path)
    assert ok.ok
    assert ok.stdout == "hi"
    failed = _run(Instruction(op="shell", raw="ls missing-file-xyz", command="ls missing-file-xyz"), tmp_path)
    assert failed.ok is False
    assert failed.exit_code not in (None, 0)


def test_timeout_and_catastrophic_refusal(tmp_path):
    timed = _run(
        Instruction(op="shell", raw="sleep 5", command="sleep 5"),
        tmp_path,
        timeout=1,
    )
    assert timed.timed_out
    refused = _run(Instruction(op="shell", raw="rm -rf /", command="rm -rf /"), tmp_path, decision="approved")
    assert refused.refused
    auto = _run(
        Instruction(op="shell", raw="rm -rf ./build", command="rm -rf ./build"),
        tmp_path,
        decision="auto",
    )
    assert auto.refused


def test_interactive_rejected_without_running(tmp_path):
    result = _run(Instruction(op="shell", raw="vim", command="vim"), tmp_path)
    assert result.refused
    assert "interactive" in (result.error or "").lower()


def test_write_and_read_roundtrip(tmp_path):
    written = _run(
        Instruction(op="write_file", raw="write", path="notes/a.txt", content="hello\n"),
        tmp_path,
    )
    assert written.ok
    assert (tmp_path / "notes" / "a.txt").read_text(encoding="utf-8") == "hello\n"
    read = _run(Instruction(op="read_file", raw="read", path="notes/a.txt"), tmp_path)
    assert read.ok
    assert "hello" in read.stdout


def test_protected_write_and_unknown_stop(tmp_path):
    protected = _run(
        Instruction(op="write_file", raw="write", path="/etc/passwd", content="nope"),
        tmp_path,
    )
    assert protected.refused
    stopped = _run(Instruction(op="stop_process", raw="stop", pid=1), tmp_path)
    assert stopped.refused


def test_start_and_stop_process(tmp_path):
    patterns, _ = compile_denylist(DEFAULT_DENYLIST)
    started = execute(
        Instruction(op="start_process", raw="sleep 30", command="sleep 30"),
        decision="approved",
        timeout=5,
        cwd_default=str(tmp_path),
        denylist=patterns,
        allowed_pids={},
        log_dir=tmp_path / "logs",
    )
    assert started.ok
    assert started.pid
    try:
        stopped = execute(
            Instruction(op="stop_process", raw="stop", pid=started.pid),
            decision="approved",
            timeout=5,
            cwd_default=str(tmp_path),
            denylist=patterns,
            allowed_pids={started.pid: "sleep 30"},
            log_dir=tmp_path / "logs",
        )
        assert stopped.ok
        assert not stopped.refused
    finally:
        execute(
            Instruction(op="stop_process", raw="stop", pid=started.pid),
            decision="approved",
            timeout=5,
            cwd_default=str(tmp_path),
            denylist=patterns,
            allowed_pids={started.pid: "sleep 30"},
            log_dir=tmp_path / "logs",
        )


def test_env_block_ignores_shell_startup_text():
    raw = (
        b"hello from the shell\n"
        b"___MONITOR_MAID_ENV___\0"
        b"PATH=/opt/homebrew/bin:/usr/bin\0"
        b"HOME=/Users/dougie\0"
    )
    parsed = parse_env_block(raw)
    assert parsed["PATH"] == "/opt/homebrew/bin:/usr/bin"
    assert parsed["HOME"] == "/Users/dougie"
    assert parse_env_block(b"no marker") == {}


def test_user_bins_are_added_once(tmp_path):
    present = tmp_path / "bin"
    present.mkdir()
    missing = tmp_path / "missing"
    first = executor._with_user_bins("/usr/bin:/bin", extras=[present, missing])
    assert first.startswith(str(present) + ":")
    assert first.endswith("/usr/bin:/bin")
    second = executor._with_user_bins(first, extras=[present])
    assert second.count(str(present)) == 1


def test_shell_finds_a_program_hidden_from_the_app_path(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    tool = bin_dir / "maid-tool"
    tool.write_text("#!/bin/sh\nprintf found\n", encoding="utf-8")
    tool.chmod(0o755)
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setattr(
        executor,
        "_login_shell_env",
        lambda: {"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path)},
    )
    executor._ENV_CACHE = None
    result = _run(Instruction(op="shell", raw="maid-tool", command="maid-tool"), tmp_path)
    executor._ENV_CACHE = None
    assert result.ok
    assert result.stdout == "found"


def test_login_shell_path_includes_homebrew():
    if not Path("/opt/homebrew/bin/ollama").is_file():
        return
    env = executor._login_shell_env()
    assert env is not None
    assert "/opt/homebrew/bin" in env["PATH"].split(":")
