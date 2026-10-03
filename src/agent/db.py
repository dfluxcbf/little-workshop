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
    description TEXT NOT NULL DEFAULT '',
    source_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    dismissed_at TEXT
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

CREATE TABLE IF NOT EXISTS incidents (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    pillar TEXT NOT NULL,
    context TEXT NOT NULL DEFAULT '',
    exception_type TEXT NOT NULL,
    message TEXT NOT NULL DEFAULT '',
    traceback TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'open',
    run_id TEXT,
    created_at TEXT NOT NULL,
    decided_at TEXT
);

CREATE TABLE IF NOT EXISTS doc_updates (
    job_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    branch TEXT NOT NULL,
    status TEXT NOT NULL,
    remote_control INTEGER NOT NULL DEFAULT 0,
    session_name TEXT,
    unit TEXT,
    worktree_id TEXT,
    worktree_path TEXT,
    base_sha TEXT,
    comment_ids_json TEXT NOT NULL DEFAULT '[]',
    result_json TEXT,
    error TEXT,
    started_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ci_checks (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    pr_number INTEGER NOT NULL,
    branch TEXT NOT NULL,
    sha TEXT NOT NULL,
    state TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    ran_at TEXT NOT NULL
);
"""

CI_PENDING = "pending"
CI_SUCCESS = "success"
CI_FAILURE = "failure"
CI_ERROR = "error"

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
    if "description" not in {row["name"] for row in conn.execute("PRAGMA table_info(agent_runs)")}:
        conn.execute("ALTER TABLE agent_runs ADD COLUMN description TEXT NOT NULL DEFAULT ''")
    if "dismissed_at" not in {row["name"] for row in conn.execute("PRAGMA table_info(agent_runs)")}:
        conn.execute("ALTER TABLE agent_runs ADD COLUMN dismissed_at TEXT")
    conn.commit()
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_run_id() -> str:
    return uuid.uuid4().hex


# -- agent_runs -----------------------------------------------------------


def create_run(
    conn: sqlite3.Connection, run_id: str, *, job_id: str | None, project_id: str, intent: str,
    review_mode: str, title: str, source: dict, description: str = "",
) -> None:
    now = _now()
    conn.execute(
        """INSERT INTO agent_runs (id, job_id, project_id, status, intent, review_mode, title, description, source_json, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (run_id, job_id, project_id, RUNNING, intent, review_mode, title, description, json.dumps(source), now, now),
    )
    conn.commit()


def update_run(conn: sqlite3.Connection, run_id: str, **fields) -> None:
    if not fields:
        return
    fields = {**fields, "updated_at": _now()}
    assignments = ", ".join(f"{key}=?" for key in fields)
    conn.execute(f"UPDATE agent_runs SET {assignments} WHERE id=?", (*fields.values(), run_id))
    conn.commit()


def fail_interrupted_runs(conn: sqlite3.Connection, reason: str) -> list[dict]:
    """Fail every run still `running` and return them. Only safe at startup:
    a run's worker is a thread inside this process, so a run left `running`
    by a previous process can never finish."""
    rows = [dict(r) for r in conn.execute("SELECT * FROM agent_runs WHERE status = ?", (RUNNING,)).fetchall()]
    for run in rows:
        update_run(conn, run["id"], status=FAILED, summary=reason)
    return rows


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


# -- incidents: an unhandled exception, awaiting the user's confirmation --


def new_incident_id() -> str:
    return uuid.uuid4().hex


def create_incident(
    conn: sqlite3.Connection, incident_id: str, *, project_id: str, pillar: str, context: str,
    exception_type: str, message: str, traceback: str,
) -> None:
    conn.execute(
        """INSERT INTO incidents (id, project_id, pillar, context, exception_type, message, traceback, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?)""",
        (incident_id, project_id, pillar, context, exception_type, message, traceback, _now()),
    )
    conn.commit()


def get_incident(conn: sqlite3.Connection, incident_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM incidents WHERE id = ?", (incident_id,)).fetchone()
    return dict(row) if row else None


def list_incidents(conn: sqlite3.Connection, *, status: str | None = None) -> list[dict]:
    if status is None:
        rows = conn.execute("SELECT * FROM incidents ORDER BY created_at DESC").fetchall()
    else:
        rows = conn.execute("SELECT * FROM incidents WHERE status = ? ORDER BY created_at DESC", (status,)).fetchall()
    return [dict(row) for row in rows]


def decide_incident(conn: sqlite3.Connection, incident_id: str, *, status: str, run_id: str | None = None) -> None:
    conn.execute(
        "UPDATE incidents SET status = ?, run_id = ?, decided_at = ? WHERE id = ?",
        (status, run_id, _now(), incident_id),
    )
    conn.commit()


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


# -- ci_checks --------------------------------------------------------------


def record_ci_check(
    conn: sqlite3.Connection, project_id: str, *, pr_number: int, branch: str, sha: str, state: str, summary: str = "",
) -> str:
    check_id = uuid.uuid4().hex
    conn.execute(
        """INSERT INTO ci_checks (id, project_id, pr_number, branch, sha, state, summary, ran_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (check_id, project_id, pr_number, branch, sha, state, summary, _now()),
    )
    conn.commit()
    return check_id


def latest_ci_check(conn: sqlite3.Connection, project_id: str, *, branch: str | None = None) -> dict | None:
    if branch is None:
        row = conn.execute(
            "SELECT * FROM ci_checks WHERE project_id = ? ORDER BY ran_at DESC LIMIT 1", (project_id,)
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM ci_checks WHERE project_id = ? AND branch = ? ORDER BY ran_at DESC LIMIT 1",
            (project_id, branch),
        ).fetchone()
    return dict(row) if row else None


def list_ci_checks(conn: sqlite3.Connection, project_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM ci_checks WHERE project_id = ? ORDER BY ran_at DESC", (project_id,)
    ).fetchall()
    return [dict(row) for row in rows]


# -- doc_updates: agent.update_docs jobs ------------------------------------

DOC_UPDATE_STARTING = "starting"
DOC_UPDATE_RUNNING = "running"
DOC_UPDATE_DONE = "done"
DOC_UPDATE_STOPPED = "stopped"
DOC_UPDATE_FAILED = "failed"
DOC_UPDATE_ACTIVE = (DOC_UPDATE_STARTING, DOC_UPDATE_RUNNING)


def _doc_update(row) -> dict | None:
    if row is None:
        return None
    out = dict(row)
    out["comment_ids"] = json.loads(out.pop("comment_ids_json") or "[]")
    out["result"] = json.loads(out.pop("result_json")) if out.get("result_json") else None
    out["remote_control"] = bool(out["remote_control"])
    return out


def create_doc_update(
    conn: sqlite3.Connection, job_id: str, *, project_id: str, branch: str, remote_control: bool, comment_ids: list[str],
) -> None:
    now = _now()
    conn.execute(
        """INSERT INTO doc_updates (job_id, project_id, branch, status, remote_control, comment_ids_json, started_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (job_id, project_id, branch, DOC_UPDATE_STARTING, int(remote_control), json.dumps(comment_ids), now, now),
    )
    conn.commit()


def update_doc_update(conn: sqlite3.Connection, job_id: str, **fields) -> None:
    if "result" in fields:
        fields["result_json"] = json.dumps(fields.pop("result"))
    fields = {**fields, "updated_at": _now()}
    assignments = ", ".join(f"{key}=?" for key in fields)
    conn.execute(f"UPDATE doc_updates SET {assignments} WHERE job_id=?", (*fields.values(), job_id))
    conn.commit()


def get_doc_update(conn: sqlite3.Connection, job_id: str) -> dict | None:
    return _doc_update(conn.execute("SELECT * FROM doc_updates WHERE job_id = ?", (job_id,)).fetchone())


def list_doc_updates(conn: sqlite3.Connection, *, project_id: str | None = None, active_only: bool = False) -> list[dict]:
    sql, args = "SELECT * FROM doc_updates WHERE 1=1", []
    if project_id is not None:
        sql += " AND project_id = ?"
        args.append(project_id)
    if active_only:
        sql += f" AND status IN ({','.join('?' for _ in DOC_UPDATE_ACTIVE)})"
        args.extend(DOC_UPDATE_ACTIVE)
    return [_doc_update(r) for r in conn.execute(sql + " ORDER BY started_at DESC", args).fetchall()]
