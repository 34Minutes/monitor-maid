from monitor_maid.context import (
    build_grok_input,
    build_history_items,
    build_local_messages,
    compose_selection_prompt,
    format_transcript,
    parse_conclusion_list,
    seed_records,
    shutdown_plan,
    summary_messages,
)
from monitor_maid.models import NO_SUBSTANTIAL, Conclusion, HistoryItem, Summary, Turn, display_actor


def _turn(**kwargs) -> Turn:
    base = dict(
        id="t",
        session_id="s",
        seq=1,
        actor="user",
        kind="message",
        content="hello",
        created_at="2026-11-08T00:00:00+00:00",
        visible_to_local=True,
        metadata={},
    )
    base.update(kwargs)
    return Turn(**base)


def test_local_messages_drop_grok_even_if_marked_visible():
    turns = [
        _turn(id="1", seq=1, actor="user", content="What is 2+2?"),
        _turn(id="2", seq=2, actor="local_llm", content="4"),
        _turn(
            id="3",
            seq=3,
            actor="grok",
            content="GROK_SECRET_PHRASE says the answer is wrong",
            visible_to_local=True,
        ),
        _turn(
            id="4",
            seq=4,
            actor="user",
            content="hidden question",
            visible_to_local=False,
        ),
        _turn(id="5", seq=5, actor="system", kind="command_result", content="exit 0"),
    ]
    messages = build_local_messages(turns)
    blob = "\n".join(message["content"] for message in messages)
    assert "GROK_SECRET_PHRASE" not in blob
    assert "hidden question" not in blob
    assert "What is 2+2?" in blob
    assert "[host]\nexit 0" in blob
    assert messages[0]["role"] == "system"
    summary = summary_messages(turns)
    assert "GROK_SECRET_PHRASE" not in "\n".join(message["content"] for message in summary)


def test_grok_sees_local_transcript_as_quoted_text():
    turns = [
        _turn(id="1", seq=1, actor="user", content="local question"),
        _turn(id="2", seq=2, actor="local_llm", content="local answer"),
        _turn(id="3", seq=3, actor="user", content="ask grok", visible_to_local=False),
        _turn(id="4", seq=4, actor="grok", content="grok answer", visible_to_local=False),
    ]
    payload = build_grok_input(turns, budget=8000)
    roles = [item["role"] for item in payload]
    assert roles[0] == "system"
    transcript = payload[1]["content"]
    assert "local question" in transcript
    assert "local answer" in transcript
    assert "Local model" in transcript
    # The local answer must not be sent as Grok's own assistant turn.
    assistant = [item["content"] for item in payload if item["role"] == "assistant"]
    assert assistant == ["grok answer"]
    assert any(item["content"] == "ask grok" for item in payload)


def test_transcript_truncates_oldest_turns():
    turns = [
        _turn(id=str(i), seq=i, content="X" * 100, created_at=f"2026-11-08T00:00:{i:02d}+00:00")
        for i in range(20)
    ]
    text = format_transcript(turns, budget=400)
    assert "omitted" in text
    assert text.endswith("X" * 100) or "X" in text


def test_shutdown_plan_and_conclusions():
    assert shutdown_plan([]) == "skip"
    short = [_turn(content="hi")]
    assert shutdown_plan(short) == "note"
    long_user = _turn(content="Please explain how the session summary should be stored " * 3)
    reply = _turn(id="2", seq=2, actor="local_llm", content="It goes in the summaries table.")
    assert shutdown_plan([long_user, reply]) == "ask"
    assert parse_conclusion_list('["Use manual approval."]') == ["Use manual approval."]
    assert parse_conclusion_list("```json\n{\"conclusions\": [\"A\", \"B\"]}\n```") == ["A", "B"]
    assert parse_conclusion_list("") == [NO_SUBSTANTIAL]
    assert parse_conclusion_list("- first\n- second") == ["first", "second"]


def test_history_items_and_local_selection_hides_grok_label():
    turns = [
        _turn(id="u", actor="user", content="question"),
        _turn(id="a", seq=2, actor="local_llm", content="answer"),
        _turn(id="g", seq=3, actor="grok", kind="message", content="Grok: a note", visible_to_local=False),
    ]
    items = build_history_items(
        turns,
        [Summary(id="s", session_id="s", content="sum", created_at="2026-11-08T00:01:00+00:00")],
        [Conclusion(id="c", session_id="s", content="done", created_at="2026-11-08T00:02:00+00:00")],
    )
    kinds = [item.kind for item in items]
    assert kinds == ["question", "answer", "grok", "summary", "conclusion"]
    prompt = compose_selection_prompt(
        [HistoryItem("k", "grok", "grok", "t", "Grok: check the logs", "g")],
        "Look at this",
        for_local=True,
    )
    assert "Grok:" not in prompt
    assert "[note " in prompt
    assert "check the logs" in prompt


def test_seed_records_are_local_messages_and_hide_grok_attribution():
    items = [
        HistoryItem("q", "question", "user", "t1", "What is in the home directory?", "q"),
        HistoryItem("a", "answer", "local_llm", "t2", "A listing was produced.", "a"),
        HistoryItem("s", "summary", "local_llm", "t3", "The user listed the home directory.", "s"),
        HistoryItem("c", "conclusion", "local_llm", "t4", "The listing was approved.", "c"),
        HistoryItem("g", "grok", "grok", "t5", "Grok: look at the listing", "g"),
    ]
    records = seed_records(items)
    assert [actor for actor, _content, _meta in records] == ["user", "local_llm", "user", "user", "user"]
    blob = "\n".join(content for _actor, content, _meta in records)
    assert "What is in the home directory?" in blob
    assert "Summary of an earlier session:" in blob
    assert "Conclusion from an earlier session:" in blob
    assert "Note:" in blob
    assert "look at the listing" in blob
    assert "Grok:" not in blob
    assert all(meta.get("seed") for _actor, _content, meta in records)
    turns = [
        _turn(
            id=str(index),
            seq=index,
            actor=actor,
            kind="message",
            content=content,
            metadata=meta,
        )
        for index, (actor, content, meta) in enumerate(records, start=1)
    ]
    messages = build_local_messages(turns)
    sent = "\n".join(message["content"] for message in messages)
    assert "home directory" in sent
    assert "Grok:" not in sent
    assert display_actor("user", "message", True, {"seed": True, "source_kind": "summary"}) == (
        "Carried summary"
    )
    answer_only = seed_records(
        [HistoryItem("a", "answer", "local_llm", "t2", "Only the reply.", "a")]
    )
    assert answer_only[0][0] == "user"
    assert answer_only[0][1] == "Continue from the earlier replies below."
    assert answer_only[1][0] == "local_llm"
