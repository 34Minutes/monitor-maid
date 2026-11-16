"""Build model payloads and decide whether a session is worth summarising.

The local model receives only turns marked visible. Grok turns are forced
invisible at the database, and this module filters them again.
Grok sees the local transcript as quoted text, never as its own assistant turns.
"""

from __future__ import annotations

from monitor_maid.models import (
    NO_SUBSTANTIAL,
    HistoryItem,
    Prompt,
    Summary,
    Turn,
)
from monitor_maid.instructions import scrub_relay
from monitor_maid.prompts import (
    GROK_SYSTEM_PROMPT,
    PROMPT_INSTRUCTION,
    SUMMARY_INSTRUCTION,
    local_system_prompt,
)


def is_local_context(turn: Turn) -> bool:
    return bool(turn.visible_to_local) and turn.actor != "grok"


def shutdown_plan(turns: list[Turn]) -> str:
    """Return 'skip', 'note', or 'ask'.

    skip: nothing was stored; the caller may delete the empty session.
    note: something was stored, but it is too thin to send to the model.
    ask: ask the local model for a summary.
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
            # A reply marked as a prompt stays in the conversation.
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


def prompt_messages(turns: list[Turn], *, cwd: str | None = None) -> list[dict[str, str]]:
    messages = build_local_messages(turns, cwd=cwd)
    messages.append({"role": "user", "content": PROMPT_INSTRUCTION})
    return messages


def grok_prompt_messages(turns: list[Turn], *, budget: int) -> list[dict[str, str]]:
    """Ask Grok for one prompt. The local transcript is quoted, not a relay."""
    transcript = format_transcript(turns, budget)
    return [
        {"role": "system", "content": GROK_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                PROMPT_INSTRUCTION
                + "\n\nLocal transcript (the local model cannot see you):\n\n"
                + transcript
            ),
        },
    ]


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


_DRAFT_LABELS = {
    "question": "Question",
    "answer": "Answer",
    "summary": "Summary",
    "prompt": "Prompt",
    "conclusion": "Prompt",
    "grok": "Note",
}


def seed_draft(items: list[HistoryItem]) -> str:
    """Text for the input box when a new session starts from selected history.

    One item is the text itself, so it can be edited and then sent. Several
    items stay in order, each with a label. A Grok note has a leading
    attribution removed, because this text may be sent to the local model.
    """
    blocks: list[tuple[str, str]] = []
    for item in items:
        text = (item.content or "").strip()
        if item.kind == "grok":
            text = scrub_relay(text).strip()
        if not text:
            continue
        blocks.append((item.kind, text))
    if not blocks:
        return ""
    if len(blocks) == 1:
        return blocks[0][1]
    parts = []
    for kind, text in blocks:
        parts.append(f"{_DRAFT_LABELS.get(kind, 'Note')}\n{text}")
    return "\n\n".join(parts)


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


def build_history_items(
    turns: list[Turn],
    summaries: list[Summary],
    prompts: list[Prompt],
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
    for prompt in prompts:
        items.append(
            HistoryItem(
                key=f"prompt:{prompt.id}",
                kind="prompt",
                actor=prompt.author or "local_llm",
                created_at=prompt.created_at,
                content=prompt.content,
                source_id=prompt.id,
            )
        )
    items.sort(key=lambda item: item.created_at)
    return items


def link_history(
    turns: list[Turn],
    items: list[HistoryItem],
) -> tuple[dict[str, str], dict[str, str]]:
    """Map turn ids to history keys, and history keys back to turn ids.

    Questions, answers, and Grok notes use the turn id. A summary or prompt
    is a separate record: its id is not the turn id, so those rows are paired
    with the summary or prompt turn that contains the same text, in order.
    """
    turn_to_key: dict[str, str] = {}
    key_to_turn: dict[str, str] = {}
    for item in items:
        if item.kind in {"question", "answer", "grok"} and item.source_id:
            turn_to_key.setdefault(item.source_id, item.key)
            key_to_turn[item.key] = item.source_id
    _pair_exact(
        [turn for turn in turns if turn.kind == "summary"],
        [item for item in items if item.kind == "summary"],
        turn_to_key,
        key_to_turn,
    )
    _pair_contained(
        [turn for turn in turns if turn.kind == "prompt"],
        [item for item in items if item.kind == "prompt"],
        turn_to_key,
        key_to_turn,
    )
    return turn_to_key, key_to_turn


def _pair_exact(
    turns: list[Turn],
    items: list[HistoryItem],
    turn_to_key: dict[str, str],
    key_to_turn: dict[str, str],
) -> None:
    used: set[str] = set()
    for item in items:
        wanted = item.content.strip()
        for turn in turns:
            if turn.id in used:
                continue
            if turn.content.strip() == wanted:
                used.add(turn.id)
                turn_to_key.setdefault(turn.id, item.key)
                key_to_turn[item.key] = turn.id
                break


def _pair_contained(
    turns: list[Turn],
    items: list[HistoryItem],
    turn_to_key: dict[str, str],
    key_to_turn: dict[str, str],
) -> None:
    """Walk prompt turns once so two fences in one reply stay in order."""
    turn_index = 0
    cursor = 0
    for item in items:
        body = item.content.strip()
        if not body:
            continue
        while turn_index < len(turns):
            text = turns[turn_index].content
            found = text.find(body, cursor)
            if found < 0:
                turn_index += 1
                cursor = 0
                continue
            turn = turns[turn_index]
            turn_to_key.setdefault(turn.id, item.key)
            key_to_turn[item.key] = turn.id
            cursor = found + len(body)
            break


def normalise_summary(text: str | None) -> str:
    cleaned = (text or "").strip()
    if not cleaned:
        return NO_SUBSTANTIAL
    return cleaned


def normalise_prompt(text: str | None) -> str:
    return normalise_summary(text)
