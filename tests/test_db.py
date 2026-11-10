from monitor_maid.context import build_local_messages
from monitor_maid.db import Database


def test_schema_session_and_grok_isolation(tmp_path):
    db = Database(tmp_path / "maid.db")
    db.migrate()
    session = db.create_session(
        title="New session",
        execution_mode="manual",
        auto_limit=4,
        grok_relay_mode="manual",
        grok_relay_limit=4,
        monitor_mode=False,
        ollama_model="llama3.2",
    )
    assert session.execution_mode == "manual"
    assert session.auto_limit == 4
    user = db.add_turn(session.id, actor="user", kind="message", content="Explain the three modes in detail please")
    assert user.seq == 1
    assert db.get_session(session.id).title.startswith("Explain the three")
    db.update_session(session.id, title="Kept name")
    db.add_turn(
        session.id,
        actor="user",
        kind="message",
        content="This later question is long enough to have replaced the title",
    )
    assert db.get_session(session.id).title == "Kept name"
    grok = db.add_turn(
        session.id,
        actor="grok",
        kind="message",
        content="GROK_SECRET_PHRASE",
        visible_to_local=True,
    )
    assert grok.visible_to_local is False
    db.add_turn(session.id, actor="local_llm", kind="message", content="Manual is the default.")
    db.add_summary(session.id, "A summary")
    db.add_prompt(session.id, "Manual approval stays the default.")
    db.add_turn(
        session.id,
        actor="local_llm",
        kind="prompt",
        content="PROMPT_SHOULD_STAY_OUT",
        visible_to_local=True,
    )
    messages = build_local_messages(db.list_turns(session.id))
    blob = "\n".join(item["content"] for item in messages)
    assert "GROK_SECRET_PHRASE" not in blob
    assert "PROMPT_SHOULD_STAY_OUT" in blob
    assert "Manual is the default." in blob
    assert db.list_prompts(session.id)[0].author == "local_llm"
    db.mark_closed(session.id)
    assert db.get_session(session.id).status == "closed"
    reopened = db.reopen_session(session.id)
    assert reopened.status == "open"
    assert db.list_summaries(session.id)[0].content == "A summary"
    db.close()


def test_delete_session_removes_transcript_and_notes(tmp_path):
    db = Database(tmp_path / "maid.db")
    db.migrate()
    session = db.create_session(
        title="Kept name",
        execution_mode="manual",
        auto_limit=4,
        grok_relay_mode="manual",
        grok_relay_limit=4,
        monitor_mode=False,
        ollama_model="llama3.2",
    )
    other = db.create_session(
        title="Other",
        execution_mode="manual",
        auto_limit=4,
        grok_relay_mode="manual",
        grok_relay_limit=4,
        monitor_mode=False,
        ollama_model="llama3.2",
    )
    db.add_turn(session.id, actor="user", kind="message", content="hello")
    db.add_summary(session.id, "A summary")
    db.add_prompt(session.id, "A prompt", author="grok")
    db.set_last_session(session.id)
    log_file = tmp_path / "proc-test.log"
    log_file.write_text("output", encoding="utf-8")
    db.add_process(1, session.id, "echo hi", str(log_file))
    assert db.delete_session(session.id) is True
    assert db.get_session(session.id) is None
    assert db.list_turns(session.id) == []
    assert db.list_summaries(session.id) == []
    assert db.list_prompts(session.id) == []
    assert db.list_processes() == []
    assert not log_file.exists()
    assert db.get_session(other.id) is not None
    assert db.get_setting("last_session_id", "") == ""
    assert db.delete_session(session.id) is False
    db.close()


def test_empty_session_delete_and_crash_marker(tmp_path):
    db = Database(tmp_path / "maid.db")
    db.migrate()
    session = db.create_session(
        title="New session",
        execution_mode="manual",
        auto_limit=4,
        grok_relay_mode="manual",
        grok_relay_limit=4,
        monitor_mode=True,
        ollama_model=None,
    )
    assert db.delete_session_if_empty(session.id) is True
    assert db.get_session(session.id) is None
    db.set_running(True, "missing")
    crashed, sid = db.crash_marker()
    assert crashed is True
    assert sid == "missing"
    db.set_running(False)
    crashed, _sid = db.crash_marker()
    assert crashed is False
    db.close()


def test_commands_are_logged(tmp_path):
    db = Database(tmp_path / "maid.db")
    db.migrate()
    session = db.create_session(
        title="New session",
        execution_mode="auto_n",
        auto_limit=4,
        grok_relay_mode="manual",
        grok_relay_limit=4,
        monitor_mode=False,
        ollama_model="llama3.2",
    )
    from monitor_maid.db import utc_now
    from monitor_maid.models import CommandRecord

    db.add_command(
        CommandRecord(
            id="c1",
            session_id=session.id,
            turn_id=None,
            proposed_at=utc_now(),
            command="ls",
            cwd="/tmp",
            kind="shell",
            status="done",
            decision="auto",
            exit_code=0,
            stdout="ok",
            stderr="",
        )
    )
    rows = db.list_commands(session.id)
    assert rows[0].decision == "auto"
    assert rows[0].stdout == "ok"
    db.close()


def test_old_conclusions_table_becomes_prompts(tmp_path):
    db = Database(tmp_path / "maid.db")
    db.migrate()
    session = db.create_session(
        title="Old notes",
        execution_mode="manual",
        auto_limit=4,
        grok_relay_mode="manual",
        grok_relay_limit=4,
        monitor_mode=False,
        ollama_model="llama3.2",
    )
    db._conn.execute("DROP TABLE prompts")
    db._conn.execute(
        """
        CREATE TABLE conclusions (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES sessions(id),
            content TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    db._conn.execute(
        "INSERT INTO conclusions (id, session_id, content, created_at) VALUES (?, ?, ?, ?)",
        ("old", session.id, "Keep the carried summary.", "2026-11-08T00:00:00+00:00"),
    )
    db.add_turn(
        session.id,
        actor="local_llm",
        kind="conclusion",
        content="- Keep the carried summary.",
        visible_to_local=True,
    )
    db._conn.commit()
    db.migrate()
    prompts = db.list_prompts(session.id)
    assert [item.content for item in prompts] == ["Keep the carried summary."]
    assert prompts[0].author == "local_llm"
    kinds = [turn.kind for turn in db.list_turns(session.id)]
    assert "prompt" in kinds
    assert "conclusion" not in kinds
    names = {
        row["name"]
        for row in db._conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert "prompts" in names
    assert "conclusions" not in names
    db.close()
