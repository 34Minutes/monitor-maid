from pathlib import Path

from monitor_maid.executor import execute
from monitor_maid.models import Instruction
from monitor_maid.policy import compile_denylist, DEFAULT_DENYLIST


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
