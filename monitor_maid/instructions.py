"""Parse host instructions and Grok-to-local relay blocks.

Explicit convention
-------------------
The local model requests a host action with a fenced block:

```maid-exec
{"op":"shell","command":"ls","cwd":"."}
```

Ops: shell, write_file, read_file, start_process, stop_process.
A single-line fallback is also accepted:

    MAID_EXEC: {"op":"shell","command":"pwd"}

A generic ```bash / ```sh fence is treated as ambiguous and always needs
confirmation. An opened maid-exec fence with no closing fence is partial
and is not executed.

Grok may suggest text for the local model only inside:

```maid-to-local
text that reads as a user message
```

Those blocks are never executed as host commands by themselves.
"""

from __future__ import annotations

import json
import re

from monitor_maid.models import Instruction, RelayBlock

_MAID_FENCE = re.compile(r"```maid-exec[^\n]*\n(.*?)```", re.IGNORECASE | re.DOTALL)
_MAID_PARTIAL = re.compile(r"```maid-exec[^\n]*\n(.*)\Z", re.IGNORECASE | re.DOTALL)
_PREFIX = re.compile(r"(?m)^[ \t]*MAID_EXEC:[ \t]*(\{.*\})[ \t]*$")
_BASH_FENCE = re.compile(
    r"```(?:bash|sh|shell|zsh|console)[^\n]*\n(.*?)```",
    re.IGNORECASE | re.DOTALL,
)
_RELAY = re.compile(r"```maid-to-local[^\n]*\n(.*?)```", re.IGNORECASE | re.DOTALL)
_RELAY_PARTIAL = re.compile(r"```maid-to-local[^\n]*\n(.*)\Z", re.IGNORECASE | re.DOTALL)

_OPS = {"shell", "write_file", "read_file", "start_process", "stop_process"}


def _loads(raw: str) -> tuple[dict | None, str | None]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, f"Invalid JSON ({exc.msg})."
    if not isinstance(value, dict):
        return None, "The instruction JSON must be an object."
    return value, None


def _from_object(raw: str, data: dict, *, ambiguous: bool) -> Instruction:
    op = str(data.get("op") or data.get("operation") or "").strip()
    if op not in _OPS:
        return Instruction(
            op=op or "unknown",
            raw=raw,
            malformed=True,
            ambiguous=ambiguous,
            error=f"Unknown op {op!r}. Use shell, write_file, read_file, start_process, or stop_process.",
        )
    cwd = data.get("cwd")
    cwd_text = str(cwd) if cwd else None
    if op in {"shell", "start_process"}:
        command = data.get("command")
        if not isinstance(command, str) or not command.strip():
            return Instruction(
                op=op,
                raw=raw,
                cwd=cwd_text,
                malformed=True,
                error="Missing string field 'command'.",
            )
        return Instruction(op=op, raw=raw, command=command, cwd=cwd_text, ambiguous=ambiguous)
    if op in {"read_file", "write_file"}:
        path = data.get("path")
        if not isinstance(path, str) or not path.strip():
            return Instruction(
                op=op,
                raw=raw,
                malformed=True,
                error="Missing string field 'path'.",
            )
        content = data.get("content")
        if op == "write_file" and not isinstance(content, str):
            return Instruction(
                op=op,
                raw=raw,
                path=path,
                malformed=True,
                error="write_file requires a string field 'content'.",
            )
        return Instruction(
            op=op,
            raw=raw,
            path=path,
            content=content if isinstance(content, str) else None,
            cwd=cwd_text,
            ambiguous=ambiguous,
        )
    pid = data.get("pid")
    if isinstance(pid, bool) or not isinstance(pid, int):
        return Instruction(
            op=op,
            raw=raw,
            malformed=True,
            error="stop_process requires an integer field 'pid'.",
        )
    if pid <= 0:
        return Instruction(op=op, raw=raw, malformed=True, error="pid must be a positive integer.")
    return Instruction(op=op, raw=raw, pid=pid, ambiguous=ambiguous)


def parse_instructions(text: str) -> list[Instruction]:
    """Return explicit instructions. Generic shell fences are included only when no maid-exec block exists."""
    if not text:
        return []
    found: list[Instruction] = []
    explicit = False
    for match in _MAID_FENCE.finditer(text):
        explicit = True
        body = match.group(1).strip()
        data, error = _loads(body)
        if error or data is None:
            found.append(
                Instruction(op="shell", raw=body, malformed=True, error=error or "Invalid JSON.")
            )
        else:
            found.append(_from_object(body, data, ambiguous=False))
    for match in _PREFIX.finditer(text):
        explicit = True
        body = match.group(1).strip()
        # Skip lines that sit inside a fence we already parsed.
        if any(body == item.raw for item in found):
            continue
        data, error = _loads(body)
        if error or data is None:
            found.append(
                Instruction(op="shell", raw=body, malformed=True, error=error or "Invalid JSON.")
            )
        else:
            found.append(_from_object(body, data, ambiguous=False))

    closed = _MAID_FENCE.sub("", text)
    partial = _MAID_PARTIAL.search(closed)
    if partial and partial.group(1).strip():
        found.append(
            Instruction(
                op="shell",
                raw=partial.group(1).strip(),
                partial=True,
                ambiguous=True,
                error="Instruction block was not closed.",
            )
        )
        explicit = True

    if explicit:
        return found

    for match in _BASH_FENCE.finditer(text):
        body = match.group(1).strip()
        if not body or body.startswith("{"):
            # A JSON example in a shell fence is still ambiguous, but skip empty fences.
            if not body:
                continue
        found.append(
            Instruction(
                op="shell",
                raw=body,
                command=body,
                ambiguous=True,
            )
        )
    return found


def scrub_relay(text: str) -> str:
    """Drop an obvious leading attribution so the local model is not told Grok wrote it."""
    cleaned = text.strip()
    cleaned = re.sub(
        r"(?is)^\s*(as\s+)?(grok|xai|another model|the other model)\s*[:,-]\s*",
        "",
        cleaned,
    )
    return cleaned.strip()


def parse_relays(text: str) -> list[RelayBlock]:
    if not text:
        return []
    blocks = [RelayBlock(text=match.group(1).strip(), partial=False) for match in _RELAY.finditer(text)]
    remainder = _RELAY.sub("", text)
    partial = _RELAY_PARTIAL.search(remainder)
    if partial and partial.group(1).strip():
        blocks.append(RelayBlock(text=partial.group(1).strip(), partial=True))
    return [block for block in blocks if block.text]
