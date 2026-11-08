"""Plain-text, Markdown, and print HTML for a session."""

from __future__ import annotations

import html

from monitor_maid.models import CommandRecord, Conclusion, Session, Summary, Turn, display_actor


def _actor_line(turn: Turn) -> str:
    label = display_actor(turn.actor, turn.kind, turn.visible_to_local)
    note = ""
    if turn.actor == "grok" or (turn.actor == "user" and not turn.visible_to_local):
        note = " (not visible to the local model)"
    return f"{label}{note} — {turn.created_at}"


def session_to_markdown(
    session: Session,
    turns: list[Turn],
    summaries: list[Summary],
    conclusions: list[Conclusion],
    commands: list[CommandRecord],
) -> str:
    lines = [
        f"# {session.title}",
        "",
        f"- Session: `{session.id}`",
        f"- Started: {session.created_at}",
        f"- Status: {session.status}",
    ]
    if session.closed_at:
        lines.append(f"- Ended: {session.closed_at}")
    lines.append(f"- Execution mode: {session.execution_mode}")
    lines.append("")
    lines.append("## Transcript")
    lines.append("")
    if not turns:
        lines.append("_No turns._")
        lines.append("")
    for turn in turns:
        lines.append(f"### {_actor_line(turn)}")
        lines.append("")
        lines.append(turn.content.rstrip() or "_(empty)_")
        lines.append("")
    lines.append("## Summaries")
    lines.append("")
    if not summaries:
        lines.append("_None._")
        lines.append("")
    for summary in summaries:
        lines.append(f"### {summary.created_at}")
        lines.append("")
        lines.append(summary.content.rstrip())
        lines.append("")
    lines.append("## Conclusions")
    lines.append("")
    if not conclusions:
        lines.append("_None._")
        lines.append("")
    for conclusion in conclusions:
        lines.append(f"- ({conclusion.created_at}) {conclusion.content}")
    lines.append("")
    lines.append("## Commands")
    lines.append("")
    if not commands:
        lines.append("_None._")
        lines.append("")
    for command in commands:
        lines.append(f"### {command.proposed_at} — {command.decision or command.status}")
        lines.append("")
        lines.append("```")
        lines.append(command.command)
        lines.append("```")
        lines.append("")
        if command.exit_code is not None:
            lines.append(f"Exit code: {command.exit_code}")
            lines.append("")
        if command.stdout:
            lines.append("stdout:")
            lines.append("")
            lines.append("```")
            lines.append(command.stdout.rstrip())
            lines.append("```")
            lines.append("")
        if command.stderr:
            lines.append("stderr:")
            lines.append("")
            lines.append("```")
            lines.append(command.stderr.rstrip())
            lines.append("```")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def session_to_plaintext(
    session: Session,
    turns: list[Turn],
    summaries: list[Summary],
    conclusions: list[Conclusion],
    commands: list[CommandRecord],
) -> str:
    lines = [
        session.title,
        f"Session: {session.id}",
        f"Started: {session.created_at}",
        f"Status: {session.status}",
    ]
    if session.closed_at:
        lines.append(f"Ended: {session.closed_at}")
    lines.append("")
    lines.append("TRANSCRIPT")
    lines.append("----------")
    for turn in turns:
        lines.append(_actor_line(turn))
        lines.append(turn.content.rstrip())
        lines.append("")
    lines.append("SUMMARIES")
    lines.append("---------")
    for summary in summaries:
        lines.append(summary.created_at)
        lines.append(summary.content.rstrip())
        lines.append("")
    if not summaries:
        lines.append("None.")
        lines.append("")
    lines.append("CONCLUSIONS")
    lines.append("-----------")
    for conclusion in conclusions:
        lines.append(f"- {conclusion.created_at}: {conclusion.content}")
    if not conclusions:
        lines.append("None.")
    lines.append("")
    lines.append("COMMANDS")
    lines.append("--------")
    for command in commands:
        lines.append(
            f"{command.proposed_at} [{command.decision or command.status}] {command.command}"
        )
        if command.exit_code is not None:
            lines.append(f"  exit {command.exit_code}")
        if command.stdout:
            lines.append("  stdout: " + command.stdout.strip().replace("\n", "\n  "))
        if command.stderr:
            lines.append("  stderr: " + command.stderr.strip().replace("\n", "\n  "))
    if not commands:
        lines.append("None.")
    lines.append("")
    return "\n".join(lines)


def session_to_html(
    session: Session,
    turns: list[Turn],
    summaries: list[Summary],
    conclusions: list[Conclusion],
    commands: list[CommandRecord],
) -> str:
    parts = [
        "<html><head><meta charset='utf-8'></head><body>",
        f"<h1>{html.escape(session.title)}</h1>",
        "<p>",
        f"Session: {html.escape(session.id)}<br>",
        f"Started: {html.escape(session.created_at)}<br>",
        f"Status: {html.escape(session.status)}",
        "</p>",
        "<h2>Transcript</h2>",
    ]
    for turn in turns:
        parts.append(f"<h3>{html.escape(_actor_line(turn))}</h3>")
        parts.append(f"<pre>{html.escape(turn.content)}</pre>")
    parts.append("<h2>Summaries</h2>")
    for summary in summaries:
        parts.append(f"<h3>{html.escape(summary.created_at)}</h3>")
        parts.append(f"<pre>{html.escape(summary.content)}</pre>")
    if not summaries:
        parts.append("<p>None.</p>")
    parts.append("<h2>Conclusions</h2><ul>")
    for conclusion in conclusions:
        parts.append(f"<li>{html.escape(conclusion.content)}</li>")
    if not conclusions:
        parts.append("<li>None.</li>")
    parts.append("</ul><h2>Commands</h2>")
    for command in commands:
        title = f"{command.proposed_at} — {command.decision or command.status}"
        parts.append(f"<h3>{html.escape(title)}</h3>")
        parts.append(f"<pre>{html.escape(command.command)}</pre>")
        if command.stdout:
            parts.append(f"<pre>{html.escape(command.stdout)}</pre>")
        if command.stderr:
            parts.append(f"<pre>{html.escape(command.stderr)}</pre>")
    if not commands:
        parts.append("<p>None.</p>")
    parts.append("</body></html>")
    return "".join(parts)


def selection_to_markdown(items) -> str:
    lines = ["# Selected history", ""]
    for item in items:
        lines.append(f"## {item.kind} — {item.created_at}")
        lines.append("")
        lines.append(item.content.rstrip() or "_(empty)_")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
