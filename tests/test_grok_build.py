"""Headless grok command building, session list parsing, and a fake binary."""

import json
import os
import shutil
import stat
from pathlib import Path

import pytest

from monitor_maid.grok_build import (
    GrokBuildConfig,
    GrokBuildError,
    RunSpec,
    StreamEvent,
    build_command,
    format_events,
    strip_grok_status,
    grok_available,
    list_models,
    list_sessions,
    load_config,
    parse_extra_flags,
    parse_models,
    parse_sessions_list,
    parse_stdout_line,
    resolve_binary,
    run_prompt,
    save_config,
    validate_directory,
)

SAMPLE_LIST = """
(no label)
SESSION ID                            CREATED     UPDATED     STATUS      SUMMARY
01a1ceed-ed80-78e2-8895-ddab61dc5e5b  2026-11-12  2026-11-12  local  Summarize the app
01a1ba2e-5a48-7463-8fd1-b247445d5484  2026-11-08  2026-11-10  local  Monitor Maid notes
"""

SAMPLE_MODELS = """
Default model: grok-4.7

Available models:
  * grok-4.7 (default)
  - grok-4.6
"""

FAKE_GROK = r"""#!/usr/bin/env python3
import json
import sys
from pathlib import Path

args = sys.argv[1:]


def prompt_of(argv):
    if "-p" in argv:
        return argv[argv.index("-p") + 1]
    if "--prompt-file" in argv:
        return Path(argv[argv.index("--prompt-file") + 1]).read_text(encoding="utf-8")
    return ""


if args == ["--version"]:
    print("grok test")
    raise SystemExit(0)

if args[:2] == ["sessions", "list"]:
    sys.stdout.write(
        "(no label)\n"
        "SESSION ID                            CREATED     UPDATED     STATUS      SUMMARY\n"
        "01a1ceed-ed80-78e2-8895-ddab61dc5e5b  2026-11-12  2026-11-12  local  Summarize the app\n"
    )
    raise SystemExit(0)

if args == ["models"]:
    print("  * grok-4.7 (default)")
    print("  - grok-4.6")
    raise SystemExit(0)

if args[:2] == ["sessions", "delete"]:
    if args[2] == "missing":
        print("not signed in", file=sys.stderr)
        raise SystemExit(1)
    print("deleted")
    raise SystemExit(0)

if args[:1] == ["export"]:
    Path(args[2]).write_text("# transcript\n", encoding="utf-8")
    raise SystemExit(0)

prompt = prompt_of(args)
if "FAILAUTH" in prompt:
    print("You are not signed in.", file=sys.stderr)
    raise SystemExit(1)
if "FAIL" in prompt:
    print("boom", file=sys.stderr)
    raise SystemExit(1)

print(json.dumps({"type": "text", "data": "Hello"}))
print(json.dumps({"type": "tool_call", "toolName": "read_file", "status": "in_progress"}))
print("noise on stderr", file=sys.stderr)
print(json.dumps({
    "type": "end",
    "sessionId": "22222222-2222-4222-8222-222222222222",
    "stopReason": "end_turn",
}))
"""


def _fake_grok(tmp_path: Path) -> str:
    path = tmp_path / "grok"
    path.write_text(FAKE_GROK, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def test_parse_sessions_and_models():
    rows = parse_sessions_list(SAMPLE_LIST)
    assert [row.session_id for row in rows] == [
        "01a1ceed-ed80-78e2-8895-ddab61dc5e5b",
        "01a1ba2e-5a48-7463-8fd1-b247445d5484",
    ]
    assert rows[0].summary == "Summarize the app"
    assert rows[0].created == "2026-11-12"
    assert rows[0].status == "local"
    assert parse_models(SAMPLE_MODELS) == ["grok-4.7", "grok-4.6"]


def test_streaming_line_keeps_session_and_stderr():
    event = parse_stdout_line(
        '{"type":"end","sessionId":"abc","stopReason":"refusal"}\n',
        "streaming-json",
    )
    assert event is not None
    assert event.session_id == "abc"
    assert event.stop_reason == "refusal"
    display, answer = format_events(
        [
            StreamEvent(source="stdout", kind="text", text="Hello"),
            StreamEvent(source="stdout", kind="tool_call", text="tool: read_file started"),
            StreamEvent(source="stderr", kind="stderr", text="noise on stderr"),
        ]
    )
    assert answer == "Hello"
    assert "tool: read_file started" in display
    assert "stderr:\nnoise on stderr" in display


def test_session_location_notice_is_left_out_of_the_chat():
    notice = (
        "Session 01a1cf51-66d3-78c2-9994-61b96654d37c found locally "
        "(originally in /Users/dougie/.grok/worktrees/projects-runner/2026-11-12-5ea5deca)"
    )
    display, answer = format_events(
        [
            StreamEvent(source="stdout", kind="text", text="Hello"),
            StreamEvent(source="stderr", kind="stderr", text=notice),
        ]
    )
    assert answer == "Hello"
    assert display == "Hello"
    assert "stderr:" not in display

    mixed, _answer = format_events(
        [
            StreamEvent(source="stdout", kind="text", text="Hello"),
            StreamEvent(source="stderr", kind="stderr", text=notice),
            StreamEvent(source="stderr", kind="stderr", text="boom"),
        ]
    )
    assert mixed == "Hello\n\nstderr:\nboom"
    assert strip_grok_status(f"Hello\n\nstderr:\n{notice}") == "Hello"
    assert strip_grok_status(f"Hello\n\nstderr:\n{notice}\nboom") == "Hello\n\nstderr:\nboom"

    missing, _answer = format_events(
        [StreamEvent(source="stderr", kind="stderr", text="Session not found locally")]
    )
    assert missing == "stderr:\nSession not found locally"


def test_build_command_matches_the_headless_shape(tmp_path):
    directory = tmp_path / "work"
    directory.mkdir()
    command = build_command(
        RunSpec(
            cwd=str(directory),
            prompt="Look around",
            resume_id="01a1ceed-ed80-78e2-8895-ddab61dc5e5b",
            model="grok-4.7",
            always_approve=True,
            output_format="streaming-json",
            extra_args=["--max-turns", "4"],
        ),
        "grok",
    )
    assert command[:6] == [
        "grok",
        "--no-auto-update",
        "--cwd",
        str(directory),
        "-p",
        "Look around",
    ]
    assert "--resume" in command
    assert "--always-approve" in command
    assert "--output-format" in command
    assert command[-2:] == ["--max-turns", "4"]
    with pytest.raises(GrokBuildError):
        parse_extra_flags("--cwd /tmp")


def test_config_round_trip_and_missing_directory(tmp_path):
    path = tmp_path / "grok-build.json"
    save_config(
        GrokBuildConfig(cwd=str(tmp_path), session_id="abc", bound=True, model="grok-4.7"),
        path,
    )
    loaded = load_config(path)
    assert loaded.session_id == "abc"
    assert loaded.bound is True
    assert loaded.output_format == "streaming-json"
    assert oct(path.stat().st_mode & 0o777) == oct(0o600)
    assert load_config(tmp_path / "missing.json").cwd == ""
    with pytest.raises(GrokBuildError) as raised:
        validate_directory(str(tmp_path / "nope"))
    assert raised.value.kind == "bad_args"


def test_grok_is_resolved_from_the_login_shell_path(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    grok = bin_dir / "grok"
    grok.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    grok.chmod(0o755)
    monkeypatch.delenv("MONITOR_MAID_GROK", raising=False)
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setattr(
        "monitor_maid.grok_build.command_environment",
        lambda: {"PATH": f"{bin_dir}{os.pathsep}/usr/bin{os.pathsep}/bin"},
    )
    assert shutil.which("grok", path="/usr/bin:/bin") is None
    assert grok_available()
    assert resolve_binary() == str(grok)


def test_fake_grok_runs_lists_and_reports_errors(tmp_path):
    binary = _fake_grok(tmp_path)
    work = tmp_path / "work"
    work.mkdir()
    rows = list_sessions(str(work), binary=binary)
    assert rows[0].summary == "Summarize the app"
    assert list_models(binary=binary) == ["grok-4.7", "grok-4.6"]
    seen = []
    result = run_prompt(
        RunSpec(cwd=str(work), prompt="Hello there", output_format="streaming-json"),
        lambda snapshot: seen.append(snapshot),
        binary=binary,
    )
    assert result.session_id == "22222222-2222-4222-8222-222222222222"
    assert result.text == "Hello"
    assert "stderr:" in result.display
    assert seen
    with pytest.raises(GrokBuildError) as auth:
        run_prompt(RunSpec(cwd=str(work), prompt="FAILAUTH"), binary=binary)
    assert auth.value.kind == "auth"
    with pytest.raises(GrokBuildError) as failed:
        run_prompt(RunSpec(cwd=str(work), prompt="FAIL now"), binary=binary)
    assert failed.value.kind == "exit"
    assert "boom" in str(failed.value)
    with pytest.raises(GrokBuildError) as missing:
        run_prompt(
            RunSpec(cwd=str(work), prompt="Hi"),
            binary=str(tmp_path / "not-grok"),
        )
    assert missing.value.kind == "not_found"


def test_new_session_id_is_passed_through(tmp_path):
    binary = _fake_grok(tmp_path)
    work = tmp_path / "work"
    work.mkdir()
    result = run_prompt(
        RunSpec(
            cwd=str(work),
            prompt="Start",
            new_session_id="33333333-3333-4333-8333-333333333333",
        ),
        binary=binary,
    )
    assert result.exit_code == 0
    # The fake grok reports the id from the JSON end event.
    assert result.session_id == "22222222-2222-4222-8222-222222222222"
    command = build_command(
        RunSpec(
            cwd=str(work),
            prompt="Start",
            new_session_id="33333333-3333-4333-8333-333333333333",
        ),
        binary,
    )
    assert command[command.index("--session-id") + 1] == "33333333-3333-4333-8333-333333333333"
    assert "--resume" not in command
    # The command is a list, so the prompt is not passed through a shell.
    assert isinstance(command, list)
    assert json.dumps(command)
    assert os.path.basename(command[0]) == "grok"
