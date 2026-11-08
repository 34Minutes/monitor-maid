"""Host-command safety classification and the three execution modes.

Catastrophic patterns are refused even after a person clicks Approve.
Denylist and sensitive-path matches are never run in an automatic mode;
they fall back to the approval dialog. Ambiguous commands (a generic
shell fence, or an instruction that was not closed) also stay manual.
"""

from __future__ import annotations

import re
from pathlib import Path

from monitor_maid.models import Instruction, SafetyAssessment

DEFAULT_DENYLIST = r"""
# One regex per line. Matching commands are never auto-run.
# Lines starting with # are ignored. Invalid regexes are rejected in Settings.
\brm\s+(-[a-zA-Z]*[rfRF][a-zA-Z]*\s+)+
\bsudo\b
\bshutdown\b|\breboot\b|\bhalt\b|\bpoweroff\b
\bchmod\b[^\n]*\b777\b
\bchown\b[^\n]*\s+/\s*$
curl[^\n]*\|\s*(ba)?sh
wget[^\n]*\|\s*(ba)?sh
\bkillall\b
\bpkill\b
\bmkfs(\.[a-zA-Z0-9]+)?\b
\bdd\b[^\n]*\bof=/dev/
\bdiskutil\b[^\n]*erase
>\s*/dev/(sd|nvme|disk|rdisk|mmcblk)
"""

_INTERACTIVE = re.compile(
    r"(?:^|[;&|]\s*)(?:sudo\s+)?(vim|nvim|vi|nano|emacs|less|more|top|htop|watch|"
    r"ssh|ftp|sftp|passwd|mysql|psql|sqlite3|ipython|irb)\b",
    re.IGNORECASE,
)
_PYTHON = re.compile(r"(?:^|[;&|]\s*)(?:sudo\s+)?python3?\b", re.IGNORECASE)
_NODE = re.compile(r"(?:^|[;&|]\s*)(?:sudo\s+)?node\b", re.IGNORECASE)
_BASH = re.compile(r"(?:^|[;&|]\s*)(?:sudo\s+)?(?:bash|zsh|sh|fish)\b", re.IGNORECASE)

_SENSITIVE = [
    re.compile(r"\.ssh(/|$|\s)", re.IGNORECASE),
    re.compile(r"id_(rsa|ed25519|ecdsa)\b", re.IGNORECASE),
    re.compile(r"\.aws/(credentials|config)\b", re.IGNORECASE),
    re.compile(r"/etc/shadow\b"),
    re.compile(r"/etc/sudoers\b"),
    re.compile(r"\.gnupg(/|$)", re.IGNORECASE),
    re.compile(r"(^|[\\/\s])\.env\b"),
    re.compile(r"cookies\.sqlite", re.IGNORECASE),
    re.compile(r"Login\.keychain", re.IGNORECASE),
    re.compile(r"\.netrc\b", re.IGNORECASE),
]

_PROTECTED_PREFIXES = (
    "/etc/",
    "/System/",
    "/bin/",
    "/sbin/",
    "/dev/",
    "/private/etc/",
)


def compile_denylist(text: str) -> tuple[list[re.Pattern[str]], list[str]]:
    patterns: list[re.Pattern[str]] = []
    errors: list[str] = []
    for line_no, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            patterns.append(re.compile(line))
        except re.error as exc:
            errors.append(f"line {line_no}: {exc}")
    return patterns, errors


def _rm_targets_root(command: str) -> bool:
    if not re.search(r"\brm\b", command):
        return False
    recursive = bool(
        re.search(r"(?:^|\s)-[a-zA-Z]*r[a-zA-Z]*\b", command)
        or re.search(r"(?:^|\s)--recursive\b", command)
    )
    # A bare `rm /` is refused even without -r. Other targets need a recursive flag.
    root_token = re.compile(
        r"(?:^|[\s'\"=])(/|/\*|~/?|\$HOME/?|\$\{HOME\}/?|/usr/?|/bin/?|/sbin/?|"
        r"/System/?|/etc/?|/Library/?|/Applications/?|/private/?|/var/?|/opt/?)"
        r"(?=$|[\s;&|`'\"])"
    )
    found_root = False
    for match in root_token.finditer(command):
        target = match.group(1)
        if target in {"/", "/*"}:
            return True
        found_root = True
    if found_root and recursive:
        return True
    return False


def catastrophic_reasons(command: str) -> list[str]:
    reasons: list[str] = []
    if _rm_targets_root(command):
        reasons.append("Refusing a delete aimed at the filesystem root or a top-level directory.")
    if re.search(r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:", command):
        reasons.append("Refusing a fork bomb.")
    if re.search(r"\bmkfs(\.[a-zA-Z0-9]+)?\b", command, re.IGNORECASE):
        reasons.append("Refusing a filesystem format.")
    if re.search(
        r"\bdd\b[\s\S]{0,240}\bof=/dev/(disk|rdisk|sd|nvme|mmcblk)",
        command,
        re.IGNORECASE,
    ):
        reasons.append("Refusing a raw write to a disk device.")
    if re.search(r">\s*/dev/(disk|rdisk|sd|nvme|mmcblk)", command):
        reasons.append("Refusing a redirect onto a raw disk.")
    if re.search(r"\bdiskutil\b[\s\S]{0,120}\berase", command, re.IGNORECASE):
        reasons.append("Refusing diskutil erase.")
    return reasons


def is_protected_write(path: str) -> bool:
    if not path:
        return False
    normalised = path if path.startswith("/") else ""
    if not normalised:
        return False
    if normalised in {"/", "/etc", "/System", "/bin", "/sbin", "/dev"}:
        return True
    return normalised.startswith(_PROTECTED_PREFIXES)


def is_interactive(command: str) -> bool:
    if _PYTHON.search(command):
        if re.search(r"\s-[c]\b", command) or re.search(r"[\w./-]+\.py\b", command):
            return False
        return True
    if _NODE.search(command):
        if re.search(r"\s-e\b", command) or re.search(r"[\w./-]+\.js\b", command):
            return False
        return True
    if _BASH.search(command):
        if re.search(r"\s-[c]\b", command) or re.search(r"[\w./-]+\.(sh|zsh)\b", command):
            return False
        return True
    if re.search(r"\bssh\b", command) and "BatchMode=yes" in command:
        return False
    return bool(_INTERACTIVE.search(command))


def _sensitive_reasons(text: str) -> list[str]:
    reasons = []
    for pattern in _SENSITIVE:
        if pattern.search(text):
            reasons.append(f"References a sensitive path ({pattern.pattern}).")
    return reasons


def assess_instruction(
    instruction: Instruction,
    denylist: list[re.Pattern[str]],
    *,
    outside_cwd: bool = False,
) -> SafetyAssessment:
    if instruction.partial:
        return SafetyAssessment(
            "block",
            ["The instruction block was not closed, so it was not run."],
            ambiguous=True,
        )
    if instruction.malformed:
        return SafetyAssessment(
            "block",
            [instruction.error or "The instruction could not be parsed."],
        )
    text = instruction.shell_text()
    if "\x00" in (instruction.raw or "") or "\x00" in text:
        return SafetyAssessment("block", ["The command contains a NUL byte."])
    if instruction.op in {"shell", "start_process"} and not (instruction.command or "").strip():
        return SafetyAssessment("block", ["The command is empty."])
    if len(text) > 8000:
        return SafetyAssessment("block", ["The command is longer than 8000 characters."])

    reasons = catastrophic_reasons(text)
    if instruction.op == "write_file" and is_protected_write(instruction.path or ""):
        reasons.append("Refusing a write under a protected system directory.")
    if reasons:
        return SafetyAssessment("block", reasons)

    if instruction.op in {"shell", "start_process"} and is_interactive(instruction.command or ""):
        return SafetyAssessment(
            "block",
            [
                "This looks interactive (editor, pager, login, or a bare REPL). "
                "It would hang because stdin is closed. Rewrite it as a non-interactive command."
            ],
            interactive=True,
        )

    warnings: list[str] = []
    if instruction.ambiguous:
        warnings.append(
            "This came from a generic shell code fence, not an explicit maid-exec block. "
            "Confirm it before it runs."
        )
    warnings.extend(_sensitive_reasons(text))
    if instruction.path:
        warnings.extend(_sensitive_reasons(instruction.path))
    for pattern in denylist:
        if pattern.search(text) or (instruction.path and pattern.search(instruction.path)):
            warnings.append(f"Matches the denylist ({pattern.pattern}).")
    if outside_cwd and instruction.op in {"read_file", "write_file"}:
        warnings.append("The path is outside the app working directory. Automatic run is disabled.")
    if warnings:
        return SafetyAssessment(
            "warn",
            warnings,
            ambiguous=instruction.ambiguous,
        )
    return SafetyAssessment("ok", [], ambiguous=instruction.ambiguous)


def plan_relay(mode: str, used: int, limit: int, *, partial: bool) -> str:
    """How to forward one Grok suggestion. Partial blocks are never automatic."""
    if partial or mode == "manual":
        return "manual"
    if mode == "auto_n" and used >= max(0, limit):
        return "manual"
    if mode in {"auto_n", "automatic"}:
        return "auto"
    return "manual"


def escapes_cwd(path: str | None, cwd: str) -> bool:
    if not path:
        return False
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = Path(cwd) / candidate
    try:
        candidate.resolve().relative_to(Path(cwd).resolve())
    except ValueError:
        return True
    return False


def plan_approval(
    mode: str,
    used: int,
    limit: int,
    safety: SafetyAssessment,
) -> str:
    """Return 'auto', 'manual', or 'block'."""
    if safety.level == "block":
        return "block"
    if safety.ambiguous or safety.level == "warn":
        return "manual"
    if mode == "manual":
        return "manual"
    if mode == "auto_n" and used >= max(0, limit):
        return "manual"
    if mode in {"auto_n", "automatic"}:
        return "auto"
    return "manual"


def apply_review_to_plan(plan: str, verdict: str | None, review_required: bool, review_failed: bool) -> str:
    """A Grok review never upgrades a decision. It can only demote auto to manual."""
    if plan != "auto":
        return plan
    if review_required and (review_failed or verdict != "safe"):
        return "manual"
    if verdict in {"unsafe", "unsure"}:
        return "manual"
    return plan


def parse_verdict(text: str | None) -> str:
    if not text:
        return "unsure"
    match = re.search(r"VERDICT:\s*(safe|unsafe|unsure)", text, re.IGNORECASE)
    if not match:
        return "unsure"
    return match.group(1).lower()
