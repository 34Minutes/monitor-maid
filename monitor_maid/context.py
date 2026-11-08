"""Build model payloads and decide whether a session is worth summarising.

The local model receives only turns marked visible. Grok turns are forced
invisible at the database, and this module filters them again.
Grok sees the local transcript as quoted text, never as its own assistant turns.
"""

from __future__ import annotations

import json
import re

from monitor_maid.models import (
    NO_SUBSTANTIAL,
    Conclusion,
    HistoryItem,
    Summary,
    Turn,
)
from monitor_maid.instructions import scrub_relay
from monitor_maid.prompts import (
    CONCLUSION_INSTRUCTION,
    GROK_SYSTEM_PROMPT,
    SUMMARY_INSTRUCTION,
    local_system_prompt,
)


def is_local_context(turn: Turn) -> bool:
    return bool(turn.visible_to_local) and turn.actor != "grok"


def shutdown_plan(turns: list[Turn]) -> str:
    """Return 'skip', 'note', or 'ask'.

    skip: nothing was stored; the caller may delete the empty session.
    note: something was stored, but it is too thin to send to the model.
    ask: ask the local model for a summary and conclusions.
    """
    if not turns:
        return "skip"
    visible = [
        turn
        for turn in turns
        if is_local_context(turn)
        and turn.actor in {"user", "local_llm"}
        and turn.kind in {"message", "relay"}
    ]
    text = "".join(turn.content.strip() for turn in visible)
    if len(text) >= 80 and any(turn.actor == "user" for turn in visible):
        return "ask"
    return "note"


def build_local_messages(turns: list[Turn], *, cwd: str | None = None) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = [
        {"role": "system", "content": local_system_prompt(cwd)}
    ]
    for turn in turns:
        if not is_local_context(turn):
            continue
        if turn.kind in {"summary", "conclusion", "review", "verification", "refusal"}:
            # Shutdown notes are not part of the live chat the model continues.
            continue
        if turn.actor == "local_llm":
            messages.append({"role": "assistant", "content": turn.content})
        elif turn.actor == "user":
            messages.append({"role": "user", "content": turn.content})
        elif turn.actor == "system":
            messages.append({"role": "user", "content": "[host]\n" + turn.content})
    return messages


def summary_messages(turns: list[Turn], *, cwd: str | None = None) -> list[dict[str, str]]:
    messages = build_local_messages(turns, cwd=cwd)
    messages.append({"role": "user", "content": SUMMARY_INSTRUCTION})
    return messages


def conclusion_messages(turns: list[Turn], *, cwd: str | None = None) -> list[dict[str, str]]:
    messages = build_local_messages(turns, cwd=cwd)
    messages.append({"role": "user", "content": CONCLUSION_INSTRUCTION})
    return messages


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 20].rstrip() + "\n…(truncated)"


def take_tail(turns: list[Turn], budget: int) -> tuple[list[Turn], bool]:
    """Keep the newest turns that fit in `budget` characters of content."""
    if budget < 500:
        budget = 500
    chosen: list[Turn] = []
    size = 0
    omitted = False
    for turn in reversed(turns):
        piece = len(turn.content) + 48
        if chosen and size + piece > budget:
            omitted = True
            break
        if not chosen and piece > budget:
            clipped = Turn(
                id=turn.id,
                session_id=turn.session_id,
                seq=turn.seq,
                actor=turn.actor,
                kind=turn.kind,
                content=_clip(turn.content, budget - 48),
                created_at=turn.created_at,
                visible_to_local=turn.visible_to_local,
                metadata=turn.metadata,
            )
            return [clipped], True
        chosen.append(turn)
        size += piece
    chosen.reverse()
    return chosen, omitted


def format_transcript(turns: list[Turn], budget: int) -> str:
    visible = [turn for turn in turns if is_local_context(turn)]
    kept, omitted = take_tail(visible, budget)
    lines = []
    for turn in kept:
        label = {"user": "User", "local_llm": "Local model", "system": "Host"}.get(
            turn.actor, turn.actor
        )
        lines.append(f"[{label} {turn.created_at}]\n{turn.content}")
    body = "\n\n".join(lines) if lines else "(no local transcript yet)"
    if omitted:
        return "(Earlier local turns omitted for length.)\n\n" + body
    return body


def _grok_channel(turns: list[Turn]) -> list[Turn]:
    channel = [
        turn
        for turn in turns
        if turn.actor == "grok" or (turn.actor == "user" and not turn.visible_to_local)
    ]
    return [turn for turn in channel if turn.kind not in {"review"}]


def build_grok_input(
    turns: list[Turn],
    *,
    budget: int,
    extra_question: str | None = None,
) -> list[dict[str, str]]:
    """Responses API input. Local replies are quoted, not sent as role=assistant."""
    transcript = format_transcript(turns, budget)
    messages: list[dict[str, str]] = [
        {"role": "system", "content": GROK_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": "Local transcript (the local model cannot see you):\n\n" + transcript,
        },
    ]
    channel, channel_omitted = take_tail(_grok_channel(turns), max(2000, budget // 2))
    if channel_omitted:
        messages.append(
            {
                "role": "user",
                "content": "(Earlier Grok-side turns omitted for length.)",
            }
        )
    for turn in channel:
        if turn.actor == "grok":
            messages.append({"role": "assistant", "content": turn.content})
        else:
            messages.append({"role": "user", "content": turn.content})
    if extra_question:
        messages.append({"role": "user", "content": extra_question})
    return messages


def build_review_input(turns: list[Turn], command: str, *, budget: int) -> list[dict[str, str]]:
    transcript = format_transcript(turns, min(budget, 12000))
    question = (
        "Evaluate this proposed host command for safety and correctness.\n"
        f"Command:\n{command}\n\n"
        "Local transcript for context:\n"
        f"{transcript}\n\n"
        "Reply in exactly this shape:\n"
        "VERDICT: safe|unsafe|unsure\n"
        "REASONS: <short>\n"
        "Do not provide a more dangerous command."
    )
    return [
        {"role": "system", "content": GROK_SYSTEM_PROMPT},
        {"role": "user", "content": question},
    ]


def compose_selection_prompt(
    items: list[HistoryItem],
    question: str,
    *,
    for_local: bool = False,
) -> str:
    """When for_local is set, Grok items are labeled as notes and stripped of a leading attribution."""
    parts = [
        "The user selected these items from the session history and asked:",
        "",
        question.strip() or "Look at the selected items.",
        "",
        "--- selected items ---",
    ]
    for item in items:
        label = item.kind
        content = item.content
        if for_local and item.kind == "grok":
            label = "note"
            content = scrub_relay(content)
        parts.append(f"[{label} {item.created_at}]\n{content}")
        parts.append("")
    return "\n".join(parts).strip()


def verification_prompt(items: list[HistoryItem], question: str) -> str:
    kinds = sorted({item.kind for item in items})
    listed = ", ".join(kinds) if kinds else "items"
    custom = question.strip() or (
        f"Verify the selected {listed} against the local transcript. "
        "For each item say whether it is supported, only partly supported, or unsupported, "
        "and quote the part of the transcript you used. Do not add new conclusions."
    )
    return compose_selection_prompt(items, custom)


def parse_conclusion_list(text: str) -> list[str]:
    cleaned = (text or "").strip()
    if not cleaned:
        return [NO_SUBSTANTIAL]
    if cleaned.lower().rstrip(".") == NO_SUBSTANTIAL.lower().rstrip("."):
        return [NO_SUBSTANTIAL]
    fenced = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE)
    candidates = [fenced.strip()]
    match = re.search(r"(\[[\s\S]*\])", fenced)
    if match:
        candidates.append(match.group(1))
    object_match = re.search(r"(\{[\s\S]*\})", fenced)
    if object_match:
        candidates.append(object_match.group(1))
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, list):
            items = [str(item).strip() for item in value if str(item).strip()]
            return items or [NO_SUBSTANTIAL]
        if isinstance(value, dict) and isinstance(value.get("conclusions"), list):
            items = [str(item).strip() for item in value["conclusions"] if str(item).strip()]
            return items or [NO_SUBSTANTIAL]
    lines = []
    for line in cleaned.splitlines():
        stripped = re.sub(r"^\s*(?:[-*]|\d+[.)])\s*", "", line).strip()
        if stripped:
            lines.append(stripped)
    return lines or [NO_SUBSTANTIAL]


def build_history_items(
    turns: list[Turn],
    summaries: list[Summary],
    conclusions: list[Conclusion],
) -> list[HistoryItem]:
    items: list[HistoryItem] = []
    for turn in turns:
        if turn.actor == "user" and turn.visible_to_local and turn.kind in {"message", "relay"}:
            kind = "question"
        elif turn.actor == "local_llm" and turn.kind == "message":
            kind = "answer"
        elif turn.actor == "grok" and turn.kind in {"message", "verification", "refusal"}:
            kind = "grok"
        else:
            continue
        items.append(
            HistoryItem(
                key=f"turn:{turn.id}",
                kind=kind,
                actor=turn.actor,
                created_at=turn.created_at,
                content=turn.content,
                source_id=turn.id,
            )
        )
    for summary in summaries:
        items.append(
            HistoryItem(
                key=f"summary:{summary.id}",
                kind="summary",
                actor="local_llm",
                created_at=summary.created_at,
                content=summary.content,
                source_id=summary.id,
            )
        )
    for conclusion in conclusions:
        items.append(
            HistoryItem(
                key=f"conclusion:{conclusion.id}",
                kind="conclusion",
                actor="local_llm",
                created_at=conclusion.created_at,
                content=conclusion.content,
                source_id=conclusion.id,
            )
        )
    items.sort(key=lambda item: item.created_at)
    return items


def normalise_summary(text: str | None) -> str:
    cleaned = (text or "").strip()
    if not cleaned:
        return NO_SUBSTANTIAL
    return cleaned
