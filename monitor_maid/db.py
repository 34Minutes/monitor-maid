"""SQLite storage. All access stays on the GUI thread."""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from monitor_maid.models import (
    CommandRecord,
    Conclusion,
    ProcessRecord,
    Session,
    Summary,
    Turn,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    closed_at TEXT,
    status TEXT NOT NULL,
    execution_mode TEXT NOT NULL,
    auto_limit INTEGER NOT NULL DEFAULT 4,
    auto_used INTEGER NOT NULL DEFAULT 0,
    grok_relay_mode TEXT NOT NULL,
    grok_relay_limit INTEGER NOT NULL DEFAULT 4,
    grok_relay_used INTEGER NOT NULL DEFAULT 0,
    monitor_mode INTEGER NOT NULL DEFAULT 0,
    ollama_model TEXT
);

CREATE TABLE IF NOT EXISTS turns (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    seq INTEGER NOT NULL,
    actor TEXT NOT NULL,
    kind TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL,
    visible_to_local INTEGER NOT NULL DEFAULT 1,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(session_id, seq)
);

CREATE TABLE IF NOT EXISTS summaries (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conclusions (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS commands (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    turn_id TEXT,
    proposed_at TEXT NOT NULL,
    command TEXT NOT NULL,
    cwd TEXT,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    decision TEXT,
    decision_reason TEXT,
    grok_review TEXT,
    exit_code INTEGER,
    stdout TEXT,
    stderr TEXT,
    started_at TEXT,
    finished_at TEXT,
    timeout_s INTEGER
);

CREATE TABLE IF NOT EXISTS processes (
    pid INTEGER PRIMARY KEY,
    session_id TEXT NOT NULL,
    command TEXT NOT NULL,
    started_at TEXT NOT NULL,
    stopped_at TEXT,
    log_path TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_turns_session ON turns(session_id, seq);
CREATE INDEX IF NOT EXISTS idx_commands_session ON commands(session_id, proposed_at);
CREATE INDEX IF NOT EXISTS idx_summaries_session ON summaries(session_id);
CREATE INDEX IF NOT EXISTS idx_conclusions_session ON conclusions(session_id);
"""

_SESSION_FIELDS = {
    "title",
    "status",
    "closed_at",
    "execution_mode",
    "auto_limit",
    "auto_used",
    "grok_relay_mode",
    "grok_relay_limit",
    "grok_relay_used",
    "monitor_mode",
    "ollama_model",
    "updated_at",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _new_id() -> str:
    return str(uuid.uuid4())


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL")
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def close(self) -> None:
        self._conn.close()

    def migrate(self) -> None:
        self._conn.executescript(SCHEMA)
        if not self.get_setting("schema_version"):
            self.set_setting("schema_version", "1")
        self._conn.commit()

    def get_setting(self, key: str, default: str = "") -> str:
        row = self._conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        if row is None:
            return default
        return str(row["value"])

    def set_setting(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self._conn.commit()

    def crash_marker(self) -> tuple[bool, str | None]:
        running = self.get_setting("app_running", "0") == "1"
        last = self.get_setting("last_session_id", "") or None
        return running, last

    def set_running(self, running: bool, session_id: str | None = None) -> None:
        self.set_setting("app_running", "1" if running else "0")
        if session_id is not None:
            self.set_setting("last_session_id", session_id)

    def set_last_session(self, session_id: str) -> None:
        self.set_setting("last_session_id", session_id)

    def create_session(
        self,
        *,
        title: str,
        execution_mode: str,
        auto_limit: int,
        grok_relay_mode: str,
        grok_relay_limit: int,
        monitor_mode: bool,
        ollama_model: str | None,
    ) -> Session:
        now = utc_now()
        session_id = _new_id()
        self._conn.execute(
            """
            INSERT INTO sessions (
                id, title, created_at, updated_at, closed_at, status,
                execution_mode, auto_limit, auto_used,
                grok_relay_mode, grok_relay_limit, grok_relay_used,
                monitor_mode, ollama_model
            ) VALUES (?, ?, ?, ?, NULL, 'open', ?, ?, 0, ?, ?, 0, ?, ?)
            """,
            (
                session_id,
                title,
                now,
                now,
                execution_mode,
                auto_limit,
                grok_relay_mode,
                grok_relay_limit,
                1 if monitor_mode else 0,
                ollama_model,
            ),
        )
        self._conn.commit()
        session = self.get_session(session_id)
        assert session is not None
        return session

    def get_session(self, session_id: str) -> Session | None:
        row = self._conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        return _session(row) if row else None

    def list_sessions(self) -> list[Session]:
        rows = self._conn.execute(
            "SELECT * FROM sessions ORDER BY updated_at DESC"
        ).fetchall()
        return [_session(row) for row in rows]

    def update_session(self, session_id: str, **fields: Any) -> Session:
        unknown = set(fields) - _SESSION_FIELDS
        if unknown:
            raise ValueError(f"Unknown session fields: {sorted(unknown)}")
        if "updated_at" not in fields:
            fields["updated_at"] = utc_now()
        if "monitor_mode" in fields:
            fields["monitor_mode"] = 1 if fields["monitor_mode"] else 0
        assignments = ", ".join(f"{column} = ?" for column in fields)
        values = list(fields.values()) + [session_id]
        self._conn.execute(f"UPDATE sessions SET {assignments} WHERE id = ?", values)
        self._conn.commit()
        session = self.get_session(session_id)
        if session is None:
            raise KeyError(session_id)
        return session

    def delete_session_if_empty(self, session_id: str) -> bool:
        turns = self._conn.execute(
            "SELECT COUNT(*) AS n FROM turns WHERE session_id = ?", (session_id,)
        ).fetchone()["n"]
        if turns:
            return False
        self._conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        self._conn.commit()
        return True

    def mark_closed(self, session_id: str) -> Session:
        return self.update_session(session_id, status="closed", closed_at=utc_now())

    def reopen_session(self, session_id: str) -> Session:
        return self.update_session(session_id, status="open", closed_at=None)

    def add_turn(
        self,
        session_id: str,
        *,
        actor: str,
        kind: str,
        content: str,
        visible_to_local: bool = True,
        metadata: dict | None = None,
    ) -> Turn:
        # Grok never enters the local model's context, even if a caller asks.
        if actor == "grok":
            visible_to_local = False
        now = utc_now()
        seq_row = self._conn.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 AS seq FROM turns WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        turn_id = _new_id()
        self._conn.execute(
            """
            INSERT INTO turns (
                id, session_id, seq, actor, kind, content, created_at,
                visible_to_local, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                turn_id,
                session_id,
                int(seq_row["seq"]),
                actor,
                kind,
                content,
                now,
                1 if visible_to_local else 0,
                json.dumps(metadata or {}),
            ),
        )
        title = self._maybe_title(session_id, actor, kind, content, visible_to_local)
        if title:
            self._conn.execute(
                "UPDATE sessions SET updated_at = ?, title = ? WHERE id = ?",
                (now, title, session_id),
            )
        else:
            self._conn.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?",
                (now, session_id),
            )
        self._conn.commit()
        turn = self._get_turn(turn_id)
        assert turn is not None
        return turn

    def list_turns(self, session_id: str) -> list[Turn]:
        rows = self._conn.execute(
            "SELECT * FROM turns WHERE session_id = ? ORDER BY seq ASC",
            (session_id,),
        ).fetchall()
        return [_turn(row) for row in rows]

    def add_summary(self, session_id: str, content: str) -> Summary:
        now = utc_now()
        summary_id = _new_id()
        self._conn.execute(
            "INSERT INTO summaries (id, session_id, content, created_at) VALUES (?, ?, ?, ?)",
            (summary_id, session_id, content, now),
        )
        self._conn.commit()
        return Summary(id=summary_id, session_id=session_id, content=content, created_at=now)

    def add_conclusion(self, session_id: str, content: str) -> Conclusion:
        now = utc_now()
        conclusion_id = _new_id()
        self._conn.execute(
            "INSERT INTO conclusions (id, session_id, content, created_at) VALUES (?, ?, ?, ?)",
            (conclusion_id, session_id, content, now),
        )
        self._conn.commit()
        return Conclusion(
            id=conclusion_id, session_id=session_id, content=content, created_at=now
        )

    def list_summaries(self, session_id: str) -> list[Summary]:
        rows = self._conn.execute(
            "SELECT * FROM summaries WHERE session_id = ? ORDER BY created_at ASC",
            (session_id,),
        ).fetchall()
        return [
            Summary(
                id=row["id"],
                session_id=row["session_id"],
                content=row["content"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def list_conclusions(self, session_id: str) -> list[Conclusion]:
        rows = self._conn.execute(
            "SELECT * FROM conclusions WHERE session_id = ? ORDER BY created_at ASC",
            (session_id,),
        ).fetchall()
        return [
            Conclusion(
                id=row["id"],
                session_id=row["session_id"],
                content=row["content"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def add_command(self, record: CommandRecord) -> CommandRecord:
        self._conn.execute(
            """
            INSERT INTO commands (
                id, session_id, turn_id, proposed_at, command, cwd, kind, status,
                decision, decision_reason, grok_review, exit_code, stdout, stderr,
                started_at, finished_at, timeout_s
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.id,
                record.session_id,
                record.turn_id,
                record.proposed_at,
                record.command,
                record.cwd,
                record.kind,
                record.status,
                record.decision,
                record.decision_reason,
                record.grok_review,
                record.exit_code,
                record.stdout,
                record.stderr,
                record.started_at,
                record.finished_at,
                record.timeout_s,
            ),
        )
        self._conn.commit()
        return record

    def update_command(self, command_id: str, **fields: Any) -> None:
        allowed = {
            "status",
            "decision",
            "decision_reason",
            "grok_review",
            "exit_code",
            "stdout",
            "stderr",
            "started_at",
            "finished_at",
            "timeout_s",
        }
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"Unknown command fields: {sorted(unknown)}")
        if not fields:
            return
        assignments = ", ".join(f"{column} = ?" for column in fields)
        values = list(fields.values()) + [command_id]
        self._conn.execute(f"UPDATE commands SET {assignments} WHERE id = ?", values)
        self._conn.commit()

    def list_commands(self, session_id: str) -> list[CommandRecord]:
        rows = self._conn.execute(
            "SELECT * FROM commands WHERE session_id = ? ORDER BY proposed_at ASC",
            (session_id,),
        ).fetchall()
        return [_command(row) for row in rows]

    def add_process(self, pid: int, session_id: str, command: str, log_path: str) -> None:
        self._conn.execute(
            """
            INSERT INTO processes (pid, session_id, command, started_at, stopped_at, log_path)
            VALUES (?, ?, ?, ?, NULL, ?)
            ON CONFLICT(pid) DO UPDATE SET
                session_id = excluded.session_id,
                command = excluded.command,
                started_at = excluded.started_at,
                stopped_at = NULL,
                log_path = excluded.log_path
            """,
            (pid, session_id, command, utc_now(), log_path),
        )
        self._conn.commit()

    def mark_process_stopped(self, pid: int) -> None:
        self._conn.execute(
            "UPDATE processes SET stopped_at = ? WHERE pid = ?",
            (utc_now(), pid),
        )
        self._conn.commit()

    def list_processes(self, session_id: str | None = None, *, running_only: bool = False) -> list[ProcessRecord]:
        sql = "SELECT * FROM processes"
        clauses: list[str] = []
        values: list[Any] = []
        if session_id is not None:
            clauses.append("session_id = ?")
            values.append(session_id)
        if running_only:
            clauses.append("stopped_at IS NULL")
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY started_at ASC"
        rows = self._conn.execute(sql, values).fetchall()
        return [
            ProcessRecord(
                pid=int(row["pid"]),
                session_id=row["session_id"],
                command=row["command"],
                started_at=row["started_at"],
                stopped_at=row["stopped_at"],
                log_path=row["log_path"],
            )
            for row in rows
        ]

    def allowed_pids(self) -> dict[int, str]:
        return {
            record.pid: record.command
            for record in self.list_processes(running_only=True)
        }

    def _maybe_title(
        self,
        session_id: str,
        actor: str,
        kind: str,
        content: str,
        visible_to_local: bool,
    ) -> str | None:
        if actor != "user" or not visible_to_local or kind not in {"message", "relay"}:
            return None
        session = self.get_session(session_id)
        if session is None or session.title != "New session":
            return None
        compact = " ".join(content.split())
        if not compact:
            return None
        if len(compact) > 60:
            compact = compact[:57] + "..."
        return compact

    def _get_turn(self, turn_id: str) -> Turn | None:
        row = self._conn.execute("SELECT * FROM turns WHERE id = ?", (turn_id,)).fetchone()
        return _turn(row) if row else None


def _session(row: sqlite3.Row) -> Session:
    return Session(
        id=row["id"],
        title=row["title"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        closed_at=row["closed_at"],
        status=row["status"],
        execution_mode=row["execution_mode"],
        auto_limit=int(row["auto_limit"]),
        auto_used=int(row["auto_used"]),
        grok_relay_mode=row["grok_relay_mode"],
        grok_relay_limit=int(row["grok_relay_limit"]),
        grok_relay_used=int(row["grok_relay_used"]),
        monitor_mode=bool(row["monitor_mode"]),
        ollama_model=row["ollama_model"],
    )


def _turn(row: sqlite3.Row) -> Turn:
    try:
        metadata = json.loads(row["metadata_json"] or "{}")
    except json.JSONDecodeError:
        metadata = {}
    if not isinstance(metadata, dict):
        metadata = {}
    return Turn(
        id=row["id"],
        session_id=row["session_id"],
        seq=int(row["seq"]),
        actor=row["actor"],
        kind=row["kind"],
        content=row["content"],
        created_at=row["created_at"],
        visible_to_local=bool(row["visible_to_local"]),
        metadata=metadata,
    )


def _command(row: sqlite3.Row) -> CommandRecord:
    return CommandRecord(
        id=row["id"],
        session_id=row["session_id"],
        turn_id=row["turn_id"],
        proposed_at=row["proposed_at"],
        command=row["command"],
        cwd=row["cwd"],
        kind=row["kind"],
        status=row["status"],
        decision=row["decision"],
        decision_reason=row["decision_reason"],
        grok_review=row["grok_review"],
        exit_code=row["exit_code"],
        stdout=row["stdout"],
        stderr=row["stderr"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        timeout_s=row["timeout_s"],
    )
