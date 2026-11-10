from monitor_maid.export import session_to_markdown, session_to_plaintext
from monitor_maid.models import CommandRecord, Prompt, Session, Summary, Turn
from monitor_maid.xai_client import extract_output_text


def _session() -> Session:
    return Session(
        id="abc",
        title="Notes",
        created_at="2026-11-08T01:02:03+00:00",
        updated_at="2026-11-08T01:02:03+00:00",
        closed_at="2026-11-08T01:10:00+00:00",
        status="closed",
        execution_mode="manual",
        auto_limit=4,
        auto_used=0,
        grok_relay_mode="manual",
        grok_relay_limit=4,
        grok_relay_used=0,
        monitor_mode=True,
        ollama_model="llama3.2",
    )


def test_export_includes_actor_timestamp_and_content():
    turns = [
        Turn(
            id="1",
            session_id="abc",
            seq=1,
            actor="user",
            kind="message",
            content="Hello local",
            created_at="2026-11-08T01:02:03+00:00",
            visible_to_local=True,
        ),
        Turn(
            id="2",
            session_id="abc",
            seq=2,
            actor="grok",
            kind="message",
            content="Seen by the user only",
            created_at="2026-11-08T01:03:03+00:00",
            visible_to_local=False,
        ),
    ]
    summaries = [Summary(id="s", session_id="abc", content="Short summary", created_at="2026-11-08T01:09:00+00:00")]
    prompts = [
        Prompt(
            id="c",
            session_id="abc",
            content="No change required.",
            created_at="2026-11-08T01:09:01+00:00",
            author="grok",
        )
    ]
    commands = [
        CommandRecord(
            id="cmd",
            session_id="abc",
            turn_id="1",
            proposed_at="2026-11-08T01:04:00+00:00",
            command="printf hi",
            cwd="/tmp",
            kind="shell",
            status="done",
            decision="approved",
            exit_code=0,
            stdout="hi",
            stderr="",
        )
    ]
    markdown = session_to_markdown(_session(), turns, summaries, prompts, commands)
    plain = session_to_plaintext(_session(), turns, summaries, prompts, commands)
    for text in (markdown, plain):
        assert "Hello local" in text
        assert "2026-11-08T01:02:03+00:00" in text
        assert "You" in text or "User" in text
        assert "Grok" in text
        assert "Seen by the user only" in text
        assert "Short summary" in text
        assert "No change required." in text
        assert "printf hi" in text
    assert "## Prompts" in markdown
    assert "Grok" in markdown
    assert "PROMPTS" in plain


def test_extract_output_text_and_refusal():
    text, refused = extract_output_text(
        {
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "42"}],
                }
            ]
        }
    )
    assert text == "42"
    assert refused is False
    text, refused = extract_output_text(
        {
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "refusal", "refusal": "I can't help with that."}],
                }
            ]
        }
    )
    assert refused is True
    assert "can't help" in text
    text, refused = extract_output_text({"output_text": "from convenience field", "output": []})
    assert text == "from convenience field"
    assert refused is False
