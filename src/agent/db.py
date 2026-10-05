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

CREATE TABLE IF NOT EXISTS claude_usage (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    ref_id TEXT NOT NULL DEFAULT '',
    project_id TEXT NOT NULL DEFAULT '',
    job_id TEXT,
    session_id TEXT,
    model TEXT,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0,
    num_turns INTEGER NOT NULL DEFAULT 0,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    is_error INTEGER NOT NULL DEFAULT 0,
    model_usage_json TEXT NOT NULL DEFAULT '{}',
    budget_usd REAL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS query_sessions (
    session_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    ref TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS query_sessions_by_job ON query_sessions (job_id, started_at);

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
STOPPED = "stopped"  # stopped by the user while it ran (or after its worker was gone)


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
    if "budget_usd" not in {row["name"] for row in conn.execute("PRAGMA table_info(claude_usage)")}:
        conn.execute("ALTER TABLE claude_usage ADD COLUMN budget_usd REAL")
    # The Claude session a run works in: its transcript is what the Dashboard's peek shows.
    if "session_id" not in {row["name"] for row in conn.execute("PRAGMA table_info(agent_runs)")}:
        conn.execute("ALTER TABLE agent_runs ADD COLUMN session_id TEXT")
    # exception (a raised exception), crash (a pillar process died) or unexpected (behaviour no exception names).
    if "kind" not in {row["name"] for row in conn.execute("PRAGMA table_info(incidents)")}:
        conn.execute("ALTER TABLE incidents ADD COLUMN kind TEXT NOT NULL DEFAULT 'exception'")
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


def get_run_by_job(conn: sqlite3.Connection, job_id: str) -> dict | None:
    """The run an `agent.run_task` Job started, if it has one yet."""
    row = conn.execute("SELECT * FROM agent_runs WHERE job_id = ? ORDER BY created_at DESC LIMIT 1", (job_id,)).fetchone()
    return dict(row) if row else None


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


# -- claude_usage: what each Claude run this pillar started reported using --

USAGE_RUN, USAGE_QUERY, USAGE_DOC_UPDATE = "run", "query", "doc_update"
_USAGE_SUMS = ("input_tokens", "cache_read_tokens", "cache_write_tokens", "output_tokens", "cost_usd", "num_turns", "duration_ms")


def record_usage(
    conn: sqlite3.Connection, kind: str, usage: dict | None, *, ref_id: str = "", project_id: str | None = None,
    job_id: str | None = None,
) -> str | None:
    """Store one run's usage (`claude_invoke.usage_from_raw`); a run that reported none stores nothing."""
    if not usage:
        return None
    usage_id = uuid.uuid4().hex
    conn.execute(
        """INSERT INTO claude_usage (id, kind, ref_id, project_id, job_id, session_id, model, input_tokens,
               cache_read_tokens, cache_write_tokens, output_tokens, cost_usd, num_turns, duration_ms, is_error,
               model_usage_json, budget_usd, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (usage_id, kind, ref_id or "", project_id or "", job_id, usage.get("session_id"), usage.get("model"),
         usage.get("input_tokens", 0), usage.get("cache_read_tokens", 0), usage.get("cache_write_tokens", 0),
         usage.get("output_tokens", 0), usage.get("cost_usd", 0.0), usage.get("num_turns", 0),
         usage.get("duration_ms", 0), int(bool(usage.get("is_error"))), json.dumps(usage.get("model_usage") or {}),
         usage.get("budget_usd"), _now()),
    )
    conn.commit()
    return usage_id


def _usage(row) -> dict:
    out = dict(row)
    out["is_error"] = bool(out["is_error"])
    out["model_usage"] = json.loads(out.pop("model_usage_json") or "{}")
    return out


def list_usage(
    conn: sqlite3.Connection, *, project_id: str | None = None, kind: str | None = None, since: str | None = None,
) -> list[dict]:
    """Stored usage, oldest first. `since` is an ISO time: only rows created after it."""
    where, values = [], []
    for column, value, op in (("project_id", project_id, "="), ("kind", kind, "="), ("created_at", since, ">")):
        if value is not None:
            where.append(f"{column} {op} ?")
            values.append(value)
    sql = "SELECT * FROM claude_usage" + (f" WHERE {' AND '.join(where)}" if where else "") + " ORDER BY created_at, id"
    return [_usage(row) for row in conn.execute(sql, values).fetchall()]


def usage_total(conn: sqlite3.Connection, kind: str, ref_id: str) -> dict | None:
    """The usage of everything Claude did for one run, query or doc update, summed; None when nothing was stored."""
    rows = [_usage(r) for r in conn.execute(
        "SELECT * FROM claude_usage WHERE kind = ? AND ref_id = ? ORDER BY created_at, id", (kind, ref_id)).fetchall()]
    if not rows:
        return None
    total = {key: sum(row[key] for row in rows) for key in _USAGE_SUMS}
    budgets = [row["budget_usd"] for row in rows if row["budget_usd"] is not None]
    budget = sum(budgets) if budgets else None
    return {**total, "model": rows[-1]["model"], "session_id": rows[-1]["session_id"], "invocations": len(rows),
            "budget_usd": budget, "budget_used": (total["cost_usd"] / budget) if budget else None}


# -- incidents: an unhandled exception, awaiting the user's confirmation --


def new_incident_id() -> str:
    return uuid.uuid4().hex


INCIDENT_KINDS = ("exception", "crash", "unexpected")


def create_incident(
    conn: sqlite3.Connection, incident_id: str, *, project_id: str, pillar: str, context: str,
    exception_type: str, message: str, traceback: str, kind: str = "exception",
) -> None:
    conn.execute(
        """INSERT INTO incidents (id, project_id, pillar, context, exception_type, message, traceback, status, created_at, kind)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?, ?)""",
        (incident_id, project_id, pillar, context, exception_type, message, traceback, _now(),
         kind if kind in INCIDENT_KINDS else "exception"),
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


# -- query_sessions: the Claude session of each agent.query, against the Job that asked ----------


def start_query_session(conn: sqlite3.Connection, *, job_id: str, session_id: str, ref: str | None = None) -> None:
    conn.execute("INSERT INTO query_sessions (session_id, job_id, ref, started_at) VALUES (?, ?, ?, ?)",
                 (session_id, job_id, ref, _now()))
    conn.commit()


def finish_query_session(conn: sqlite3.Connection, session_id: str) -> None:
    conn.execute("UPDATE query_sessions SET finished_at = ? WHERE session_id = ?", (_now(), session_id))
    conn.commit()


def query_sessions(conn: sqlite3.Connection, *, job_id: str | None = None, ref: str | None = None) -> list[dict]:
    """Oldest first; every one, or those of a Job, or those that worked on `ref`."""
    where, args = [], []
    for column, value in (("job_id", job_id), ("ref", ref)):
        if value is not None:
            where.append(f"{column} = ?")
            args.append(value)
    clause = f" WHERE {' AND '.join(where)}" if where else ""
    return [dict(row) for row in conn.execute(f"SELECT * FROM query_sessions{clause} ORDER BY started_at", args).fetchall()]


def delete_query_sessions(conn: sqlite3.Connection, session_ids: list[str]) -> None:
    conn.executemany("DELETE FROM query_sessions WHERE session_id = ?", [(sid,) for sid in session_ids])
    conn.commit()

