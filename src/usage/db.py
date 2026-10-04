"""Usage & Efficiency's own SQLite file: what every Claude session on this machine used
(docs/pillars/usage.md, "Store"). WAL mode, one file, opened only by this pillar.

The sources overlap on purpose (run results, OpenTelemetry, hooks, transcripts), so every table is
keyed by the id the sources share: a request by its API request id, a session by its session id, a
tool call by its tool-use id. Storing the same thing twice updates the row and counts nothing twice.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    project TEXT NOT NULL DEFAULT 'unattributed',
    origin TEXT NOT NULL DEFAULT 'interactive',
    entrypoint TEXT,
    cwd TEXT,
    transcript_path TEXT,
    repository TEXT,
    tag_project TEXT,
    job_id TEXT,
    job_type TEXT,
    run_id TEXT,
    model TEXT,
    cc_version TEXT,
    end_reason TEXT,
    first_seen TEXT,
    last_seen TEXT,
    startup_context INTEGER
);
CREATE TABLE IF NOT EXISTS requests (
    request_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    day TEXT NOT NULL,
    model TEXT NOT NULL DEFAULT '',
    input_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
    cache_write_1h_tokens INTEGER,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL,
    query_source TEXT,
    agent_name TEXT,
    skill_name TEXT,
    duration_ms INTEGER,
    prompt_id TEXT,
    source TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS requests_session ON requests (session_id, ts);
CREATE INDEX IF NOT EXISTS requests_day ON requests (day);
CREATE TABLE IF NOT EXISTS tool_calls (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    prompt_id TEXT,
    request_id TEXT,
    ts TEXT NOT NULL,
    tool TEXT NOT NULL,
    signature TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT '',
    input_bytes INTEGER,
    result_bytes INTEGER,
    duration_ms INTEGER,
    success INTEGER,
    source TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS tool_calls_session ON tool_calls (session_id, ts);
CREATE TABLE IF NOT EXISTS session_events (
    session_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    name TEXT NOT NULL,
    data_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (session_id, ts, name, data_json)
);
CREATE TABLE IF NOT EXISTS plan_pressure (
    ts TEXT PRIMARY KEY,
    day TEXT NOT NULL,
    session_id TEXT,
    five_hour_pct REAL,
    seven_day_pct REAL
);
CREATE TABLE IF NOT EXISTS day_rollups (
    day TEXT NOT NULL,
    project TEXT NOT NULL,
    origin TEXT NOT NULL,
    model TEXT NOT NULL,
    requests INTEGER NOT NULL DEFAULT 0,
    sessions INTEGER NOT NULL DEFAULT 0,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    weighted_input REAL NOT NULL DEFAULT 0,
    weighted_cache_read REAL NOT NULL DEFAULT 0,
    weighted_cache_write REAL NOT NULL DEFAULT 0,
    weighted_output REAL NOT NULL DEFAULT 0,
    context_tokens INTEGER NOT NULL DEFAULT 0,
    long_context_tokens INTEGER NOT NULL DEFAULT 0,
    poll_requests INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, project, origin, model)
);
CREATE TABLE IF NOT EXISTS run_usage (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    project TEXT NOT NULL DEFAULT '',
    job_id TEXT,
    session_id TEXT,
    created_at TEXT NOT NULL,
    data_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS doctor_reports (
    ts TEXT PRIMARY KEY,
    ok INTEGER NOT NULL,
    data_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS findings (
    id TEXT PRIMARY KEY,
    detector TEXT NOT NULL,
    project TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'open',
    title TEXT NOT NULL DEFAULT '',
    metric TEXT NOT NULL DEFAULT '',
    value REAL NOT NULL DEFAULT 0,
    saving REAL NOT NULL DEFAULT 0,
    action TEXT NOT NULL DEFAULT '',
    evidence_json TEXT NOT NULL DEFAULT '{}',
    period_start TEXT,
    period_end TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (detector, project)
);
CREATE TABLE IF NOT EXISTS experiments (
    id TEXT PRIMARY KEY,
    finding_id TEXT,
    candidate_id TEXT,
    detector TEXT,
    project TEXT NOT NULL,
    change TEXT NOT NULL DEFAULT '',
    metric TEXT NOT NULL DEFAULT '',
    before_value REAL,
    after_value REAL,
    state TEXT NOT NULL DEFAULT 'running',
    created_at TEXT NOT NULL,
    measured_at TEXT
);
CREATE TABLE IF NOT EXISTS candidates (
    id TEXT PRIMARY KEY,
    signature TEXT NOT NULL UNIQUE,
    state TEXT NOT NULL DEFAULT 'open',
    class TEXT NOT NULL DEFAULT 'heuristic',
    score INTEGER NOT NULL DEFAULT 0,
    occurrences INTEGER NOT NULL DEFAULT 0,
    sessions INTEGER NOT NULL DEFAULT 0,
    weekly_value REAL NOT NULL DEFAULT 0,
    median_cost REAL NOT NULL DEFAULT 0,
    proposed_form TEXT NOT NULL DEFAULT '',
    replacement TEXT,
    tokens_avoided REAL NOT NULL DEFAULT 0,
    data_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

SESSION_FIELDS = ("entrypoint", "cwd", "transcript_path", "repository", "tag_project", "job_id", "job_type", "run_id",
                  "model", "cc_version", "end_reason")


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    from usage import outcomes

    conn.executescript(outcomes.SCHEMA)
    conn.commit()
    return conn


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def local_day(ts: str) -> str:
    """The local date an ISO time falls on: a day on the page is the owner's day, not UTC's."""
    try:
        moment = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        moment = datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone().date().isoformat()


# -- sessions ---------------------------------------------------------------


def upsert_session(conn: sqlite3.Connection, session_id: str, *, seen: str | None = None, **fields) -> None:
    """Add what one source knows about a session; a value already stored is kept unless the source has one."""
    if not session_id:
        return
    known = {key: value for key, value in fields.items() if key in SESSION_FIELDS and value not in (None, "")}
    conn.execute("INSERT OR IGNORE INTO sessions (id) VALUES (?)", (session_id,))
    for key, value in known.items():
        conn.execute(f"UPDATE sessions SET {key} = ? WHERE id = ?", (value, session_id))
    if seen:
        conn.execute(
            "UPDATE sessions SET first_seen = MIN(COALESCE(first_seen, ?), ?), last_seen = MAX(COALESCE(last_seen, ?), ?) WHERE id = ?",
            (seen, seen, seen, seen, session_id),
        )


def get_session(conn: sqlite3.Connection, session_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    return dict(row) if row else None


def set_attribution(conn: sqlite3.Connection, session_id: str, project: str, origin: str) -> None:
    conn.execute("UPDATE sessions SET project = ?, origin = ? WHERE id = ?", (project, origin, session_id))


# -- requests and tool calls --------------------------------------------------


def upsert_request(conn: sqlite3.Connection, request: dict) -> bool:
    """Store one API request; True when it was not stored before. A second source only fills what the
    first left empty (the transcript knows the 1-hour share of a cache write, telemetry the cost)."""
    row = {
        "request_id": request["request_id"], "session_id": request["session_id"], "ts": request["ts"],
        "day": local_day(request["ts"]), "model": request.get("model") or "",
        "input_tokens": int(request.get("input_tokens") or 0), "cache_read_tokens": int(request.get("cache_read_tokens") or 0),
        "cache_write_tokens": int(request.get("cache_write_tokens") or 0), "cache_write_1h_tokens": request.get("cache_write_1h_tokens"),
        "output_tokens": int(request.get("output_tokens") or 0), "cost_usd": request.get("cost_usd"),
        "query_source": request.get("query_source"), "agent_name": request.get("agent_name"), "skill_name": request.get("skill_name"),
        "duration_ms": request.get("duration_ms"), "prompt_id": request.get("prompt_id"), "source": request.get("source") or "",
    }
    columns = ", ".join(row)
    cursor = conn.execute(f"INSERT OR IGNORE INTO requests ({columns}) VALUES ({', '.join(':' + c for c in row)})", row)
    if cursor.rowcount:
        return True
    conn.execute(
        """UPDATE requests SET cache_write_1h_tokens = COALESCE(cache_write_1h_tokens, :cache_write_1h_tokens),
               cost_usd = COALESCE(cost_usd, :cost_usd), query_source = COALESCE(query_source, :query_source),
               agent_name = COALESCE(agent_name, :agent_name), skill_name = COALESCE(skill_name, :skill_name),
               duration_ms = COALESCE(duration_ms, :duration_ms), prompt_id = COALESCE(prompt_id, :prompt_id)
           WHERE request_id = :request_id""", row)
    return False


def upsert_tool_call(conn: sqlite3.Connection, call: dict) -> bool:
    row = {
        "id": call["id"], "session_id": call["session_id"], "prompt_id": call.get("prompt_id"), "request_id": call.get("request_id"),
        "ts": call["ts"], "tool": call["tool"], "signature": call.get("signature") or "", "detail": call.get("detail") or "",
        "input_bytes": call.get("input_bytes"), "result_bytes": call.get("result_bytes"), "duration_ms": call.get("duration_ms"),
        "success": None if call.get("success") is None else int(bool(call["success"])), "source": call.get("source") or "",
    }
    cursor = conn.execute(f"INSERT OR IGNORE INTO tool_calls ({', '.join(row)}) VALUES ({', '.join(':' + c for c in row)})", row)
    if cursor.rowcount:
        return True
    conn.execute(
        """UPDATE tool_calls SET prompt_id = COALESCE(prompt_id, :prompt_id), request_id = COALESCE(request_id, :request_id),
               input_bytes = COALESCE(input_bytes, :input_bytes), result_bytes = COALESCE(result_bytes, :result_bytes),
               duration_ms = COALESCE(duration_ms, :duration_ms), success = COALESCE(success, :success)
           WHERE id = :id""", row)
    return False


def add_session_event(conn: sqlite3.Connection, session_id: str, ts: str, name: str, data: dict) -> None:
    conn.execute("INSERT OR IGNORE INTO session_events (session_id, ts, name, data_json) VALUES (?, ?, ?, ?)",
                 (session_id, ts, name, json.dumps(data)))


def add_plan_pressure(conn: sqlite3.Connection, ts: str, session_id: str | None, five_hour: float | None, seven_day: float | None) -> None:
    conn.execute("INSERT OR REPLACE INTO plan_pressure (ts, day, session_id, five_hour_pct, seven_day_pct) VALUES (?, ?, ?, ?, ?)",
                 (ts, local_day(ts), session_id, five_hour, seven_day))


def latest_plan_pressure(conn: sqlite3.Connection) -> dict | None:
    row = conn.execute("SELECT * FROM plan_pressure ORDER BY ts DESC LIMIT 1").fetchone()
    return dict(row) if row else None


# -- run results (S1), as the agent pillar stored them ------------------------


def add_run_usage(conn: sqlite3.Connection, row: dict) -> bool:
    cursor = conn.execute(
        "INSERT OR IGNORE INTO run_usage (id, kind, project, job_id, session_id, created_at, data_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (row["id"], row.get("kind") or "", row.get("project_id") or "", row.get("job_id"), row.get("session_id"),
         row.get("created_at") or now_iso(), json.dumps(row)),
    )
    return bool(cursor.rowcount)


# -- meta -------------------------------------------------------------------


def get_meta(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))


# -- removal and retention ----------------------------------------------------


def purge_project(conn: sqlite3.Connection, project: str) -> int:
    """Remove everything attributed to a project that was removed from little-workshop."""
    sessions = [row["id"] for row in conn.execute("SELECT id FROM sessions WHERE project = ?", (project,))]
    for session_id in sessions:
        for table in ("requests", "tool_calls", "session_events"):
            conn.execute(f"DELETE FROM {table} WHERE session_id = ?", (session_id,))
    conn.execute("DELETE FROM sessions WHERE project = ?", (project,))
    conn.execute("DELETE FROM day_rollups WHERE project = ?", (project,))
    conn.execute("DELETE FROM run_usage WHERE project = ?", (project,))
    conn.execute("DELETE FROM findings WHERE project = ?", (project,))
    conn.execute("DELETE FROM branch_finishes WHERE project = ?", (project,))
    conn.execute("DELETE FROM requirement_snapshots WHERE project = ?", (project,))
    conn.execute("DELETE FROM experiments WHERE project = ?", (project,))
    conn.commit()
    return len(sessions)


def prune_detail(conn: sqlite3.Connection, before_day: str) -> dict:
    """Drop per-request and per-tool-call rows older than `before_day`; the day rollups stay."""
    removed = {}
    removed["requests"] = conn.execute("DELETE FROM requests WHERE day < ?", (before_day,)).rowcount
    removed["tool_calls"] = conn.execute("DELETE FROM tool_calls WHERE substr(ts, 1, 10) < ?", (before_day,)).rowcount
    conn.commit()
    return removed


# -- findings and experiments ---------------------------------------------------

FINDING_OPEN, FINDING_ACCEPTED, FINDING_DISMISSED, FINDING_RESOLVED = "open", "accepted", "dismissed", "resolved"
FINDING_STATES = (FINDING_OPEN, FINDING_ACCEPTED, FINDING_DISMISSED, FINDING_RESOLVED)
EXPERIMENT_RUNNING, EXPERIMENT_IMPROVED, EXPERIMENT_NOT_IMPROVED = "running", "improved", "not_improved"


def _finding(row) -> dict:
    out = dict(row)
    out["evidence"] = json.loads(out.pop("evidence_json") or "{}")
    return out


def record_finding(conn: sqlite3.Connection, measurement: dict, period_start: str, period_end: str) -> str:
    """Open a finding for a flagged measurement, or refresh the one this detector already has for the
    project. A dismissed finding stays dismissed, an accepted one stays accepted; a resolved one that
    is detected again is opened again."""
    now = now_iso()
    row = conn.execute("SELECT id, state FROM findings WHERE detector = ? AND project = ?",
                       (measurement["detector"], measurement["project"])).fetchone()
    fields = (measurement["title"], measurement["metric"], measurement["value"], measurement["saving"], measurement["action"],
              json.dumps(measurement["evidence"]), period_start, period_end, now)
    if row is None:
        finding_id = uuid.uuid4().hex
        conn.execute(
            """INSERT INTO findings (id, detector, project, state, title, metric, value, saving, action, evidence_json,
                   period_start, period_end, created_at, updated_at) VALUES (?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (finding_id, measurement["detector"], measurement["project"], *fields[:-1], now, now))
        return finding_id
    state = FINDING_OPEN if row["state"] == FINDING_RESOLVED else row["state"]
    conn.execute(
        """UPDATE findings SET title = ?, metric = ?, value = ?, saving = ?, action = ?, evidence_json = ?, period_start = ?,
               period_end = ?, updated_at = ?, state = ? WHERE id = ?""", (*fields, state, row["id"]))
    return row["id"]


def resolve_missing_findings(conn: sqlite3.Connection, detected: set[tuple[str, str]], measured: dict[tuple[str, str], float]) -> int:
    """An open finding whose detector no longer flags its project is resolved, with the value it has now."""
    resolved = 0
    for row in conn.execute("SELECT id, detector, project FROM findings WHERE state = 'open'").fetchall():
        key = (row["detector"], row["project"])
        if key not in detected:
            conn.execute("UPDATE findings SET state = 'resolved', value = ?, updated_at = ? WHERE id = ?",
                         (measured.get(key, 0.0), now_iso(), row["id"]))
            resolved += 1
    return resolved


def list_findings(conn: sqlite3.Connection, *, project: str | None = None, state: str | None = FINDING_OPEN) -> list[dict]:
    """Findings ranked by what a fix is estimated to save. `state=None` lists every state."""
    where, values = [], []
    if project:
        where.append("project IN (?, 'all')")
        values.append(project)
    if state:
        where.append("state = ?")
        values.append(state)
    sql = "SELECT * FROM findings" + (f" WHERE {' AND '.join(where)}" if where else "") + " ORDER BY saving DESC, updated_at DESC"
    return [_finding(row) for row in conn.execute(sql, values)]


def get_finding(conn: sqlite3.Connection, finding_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM findings WHERE id = ?", (finding_id,)).fetchone()
    return _finding(row) if row else None


def set_finding_state(conn: sqlite3.Connection, finding_id: str, state: str) -> bool:
    if state not in FINDING_STATES:
        raise ValueError(f"unknown finding state {state!r}")
    return bool(conn.execute("UPDATE findings SET state = ?, updated_at = ? WHERE id = ?", (state, now_iso(), finding_id)).rowcount)


def add_experiment(conn: sqlite3.Connection, *, project: str, metric: str, before_value: float | None, change: str = "",
                   finding_id: str | None = None, candidate_id: str | None = None, detector: str | None = None) -> str:
    experiment_id = uuid.uuid4().hex
    conn.execute(
        """INSERT INTO experiments (id, finding_id, candidate_id, detector, project, change, metric, before_value, state, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'running', ?)""",
        (experiment_id, finding_id, candidate_id, detector, project, change, metric, before_value, now_iso()))
    return experiment_id


def list_experiments(conn: sqlite3.Connection, *, state: str | None = None) -> list[dict]:
    sql = "SELECT * FROM experiments" + (" WHERE state = ?" if state else "") + " ORDER BY created_at DESC"
    return [dict(row) for row in conn.execute(sql, (state,) if state else ())]


def finish_experiment(conn: sqlite3.Connection, experiment_id: str, after_value: float, improved: bool) -> None:
    conn.execute("UPDATE experiments SET after_value = ?, state = ?, measured_at = ? WHERE id = ?",
                 (after_value, EXPERIMENT_IMPROVED if improved else EXPERIMENT_NOT_IMPROVED, now_iso(), experiment_id))


# -- automation candidates --------------------------------------------------------

CANDIDATE_OPEN, CANDIDATE_ACCEPTED, CANDIDATE_DISMISSED, CANDIDATE_DONE = "open", "accepted", "dismissed", "done"
CANDIDATE_STATES = (CANDIDATE_OPEN, CANDIDATE_ACCEPTED, CANDIDATE_DISMISSED, CANDIDATE_DONE)


def _candidate(row) -> dict:
    out = dict(row)
    out.update(json.loads(out.pop("data_json") or "{}"))
    return out


def record_candidate(conn: sqlite3.Connection, candidate: dict) -> str:
    """Store what mining found for a signature, keeping the state and the replacement it already has."""
    now = now_iso()
    data = json.dumps({key: candidate[key] for key in ("checks", "examples", "projects", "schedule", "estimate") if key in candidate})
    values = (candidate["class"], candidate["score"], candidate["occurrences"], candidate["sessions"], candidate["weekly_value"],
              candidate["median_cost"], candidate["proposed_form"], data, now)
    row = conn.execute("SELECT id FROM candidates WHERE signature = ?", (candidate["signature"],)).fetchone()
    if row is None:
        candidate_id = uuid.uuid4().hex
        conn.execute(
            """INSERT INTO candidates (id, signature, class, score, occurrences, sessions, weekly_value, median_cost, proposed_form,
                   data_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (candidate_id, candidate["signature"], *values[:-1], now, now))
        return candidate_id
    conn.execute(
        """UPDATE candidates SET class = ?, score = ?, occurrences = ?, sessions = ?, weekly_value = ?, median_cost = ?,
               proposed_form = ?, data_json = ?, updated_at = ? WHERE id = ?""", (*values, row["id"]))
    return row["id"]


def list_candidates(conn: sqlite3.Connection, *, state: str | None = CANDIDATE_OPEN) -> list[dict]:
    """Candidates ranked by the estimated net saving per week of automating them."""
    sql = "SELECT * FROM candidates" + (" WHERE state = ?" if state else "") + " ORDER BY weekly_value DESC, occurrences DESC"
    return [_candidate(row) for row in conn.execute(sql, (state,) if state else ())]


def get_candidate(conn: sqlite3.Connection, candidate_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM candidates WHERE id = ?", (candidate_id,)).fetchone()
    return _candidate(row) if row else None


def update_candidate(conn: sqlite3.Connection, candidate_id: str, **fields) -> bool:
    allowed = {key: value for key, value in fields.items() if key in ("state", "replacement", "tokens_avoided")}
    if "state" in allowed and allowed["state"] not in CANDIDATE_STATES:
        raise ValueError(f"unknown candidate state {allowed['state']!r}")
    if not allowed:
        return False
    allowed["updated_at"] = now_iso()
    assignments = ", ".join(f"{key} = ?" for key in allowed)
    return bool(conn.execute(f"UPDATE candidates SET {assignments} WHERE id = ?", (*allowed.values(), candidate_id)).rowcount)


def drop_stale_candidates(conn: sqlite3.Connection, found: set[str]) -> int:
    """Remove the open candidates mining no longer finds. One a person decided on (accepted, dismissed,
    done) is kept, with its decision."""
    stale = [row["id"] for row in conn.execute("SELECT id, signature FROM candidates WHERE state = 'open'") if row["signature"] not in found]
    for candidate_id in stale:
        conn.execute("DELETE FROM candidates WHERE id = ?", (candidate_id,))
    return len(stale)
