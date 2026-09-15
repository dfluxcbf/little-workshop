"""Agentic Development's own SQLite file — `agent_runs`,
`remote_control_sessions` (docs/architecture.md's Data model), plus
`poll_cursors` — the GitHub adapter's own per-project cursor state
(docs/pillars/agentic_development.md's "cursor-file-based edge
detection"), kept here as rows instead of `little-runner`'s loose JSON
files now that this pillar has a real database.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_runs (
    id TEXT PRIMARY KEY,
    job_id TEXT,
    project_id TEXT NOT NULL,
    status TEXT NOT NULL,
    intent TEXT NOT NULL,
    branch TEXT,
    branch_type TEXT,
    review_mode TEXT NOT NULL,
    completion_status TEXT,
    title TEXT NOT NULL,
    summary TEXT,
    report TEXT,
    pr_url TEXT,
    source_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS remote_control_sessions (
    id TEXT PRIMARY KEY,
    agent_run_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    session_name TEXT NOT NULL,
    started_at TEXT NOT NULL,
    stopped_at TEXT
);

CREATE TABLE IF NOT EXISTS poll_cursors (
    project_id TEXT PRIMARY KEY,
    labeled_json TEXT NOT NULL DEFAULT '[]',
    assigned_json TEXT NOT NULL DEFAULT '[]',
    comment_cursor_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL
);
"""

RUNNING = "running"
AWAITING_FINISH = "awaiting_finish"
FINISHED = "finished"
FAILED = "failed"
SKIPPED = "skipped"


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_run_id() -> str:
    return uuid.uuid4().hex


# -- agent_runs -----------------------------------------------------------


def create_run(
    conn: sqlite3.Connection, run_id: str, *, job_id: str | None, project_id: str, intent: str,
    review_mode: str, title: str, source: dict,
) -> None:
    now = _now()
    conn.execute(
        """INSERT INTO agent_runs (id, job_id, project_id, status, intent, review_mode, title, source_json, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (run_id, job_id, project_id, RUNNING, intent, review_mode, title, json.dumps(source), now, now),
    )
    conn.commit()


def update_run(conn: sqlite3.Connection, run_id: str, **fields) -> None:
    if not fields:
        return
    fields = {**fields, "updated_at": _now()}
    assignments = ", ".join(f"{key}=?" for key in fields)
    conn.execute(f"UPDATE agent_runs SET {assignments} WHERE id=?", (*fields.values(), run_id))
    conn.commit()


def get_run(conn: sqlite3.Connection, run_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM agent_runs WHERE id = ?", (run_id,)).fetchone()
    return dict(row) if row else None


def list_runs(conn: sqlite3.Connection, project_id: str, *, status: str | None = None) -> list[dict]:
    if status is None:
        rows = conn.execute(
            "SELECT * FROM agent_runs WHERE project_id = ? ORDER BY created_at DESC", (project_id,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM agent_runs WHERE project_id = ? AND status = ? ORDER BY created_at DESC",
            (project_id, status),
        ).fetchall()
    return [dict(row) for row in rows]


# -- remote_control_sessions ----------------------------------------------


def record_session_start(conn: sqlite3.Connection, agent_run_id: str, project_id: str, session_name: str) -> str:
    session_id = uuid.uuid4().hex
    conn.execute(
        """INSERT INTO remote_control_sessions (id, agent_run_id, project_id, session_name, started_at)
           VALUES (?, ?, ?, ?, ?)""",
        (session_id, agent_run_id, project_id, session_name, _now()),
    )
    conn.commit()
    return session_id


def record_session_stop(conn: sqlite3.Connection, session_id: str) -> None:
    conn.execute("UPDATE remote_control_sessions SET stopped_at = ? WHERE id = ?", (_now(), session_id))
    conn.commit()


# -- poll_cursors -----------------------------------------------------------


def get_poll_cursor(conn: sqlite3.Connection, project_id: str) -> dict:
    row = conn.execute("SELECT * FROM poll_cursors WHERE project_id = ?", (project_id,)).fetchone()
    if row is None:
        return {"labeled": [], "assigned": [], "comment_cursor": {}}
    return {
        "labeled": json.loads(row["labeled_json"]),
        "assigned": json.loads(row["assigned_json"]),
        "comment_cursor": json.loads(row["comment_cursor_json"]),
    }


def save_poll_cursor(conn: sqlite3.Connection, project_id: str, *, labeled: list, assigned: list, comment_cursor: dict) -> None:
    conn.execute(
        """INSERT INTO poll_cursors (project_id, labeled_json, assigned_json, comment_cursor_json, updated_at)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(project_id) DO UPDATE SET labeled_json=excluded.labeled_json,
           assigned_json=excluded.assigned_json, comment_cursor_json=excluded.comment_cursor_json,
           updated_at=excluded.updated_at""",
        (project_id, json.dumps(labeled), json.dumps(assigned), json.dumps(comment_cursor), _now()),
    )
    conn.commit()
