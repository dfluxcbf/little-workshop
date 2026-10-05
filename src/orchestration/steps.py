"""The per-Job checklist, stored in Orchestration's own database.

A Job's steps come from its type's declared list (`common/step_specs`) and are
inserted with the Job. The pillar running the Job reports each step's state
(`report`), asks the user how to proceed (state `attention` with a prompt, then
`answer`), and Orchestration settles whatever is left when the Job ends
(`on_job_updated`, called from `db.update_job`, the one place a Job's status is
written). A step that finishes OK adds its runtime to `step_durations`, whose
last `EXPECTED_RUNTIME_SAMPLES` rows per (job type, step) give the expected runtime.

Runtime never counts the time a step spent waiting for the user.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

from common.job_steps import (
    ATTENTION,
    ATTENTION_TIMEOUT_SECONDS,
    EXPECTED_RUNTIME_SAMPLES,
    FAILED,
    PENDING,
    RUNNING,
    STEP_STATES,
    SUCCEEDED,
    TERMINAL_STEP_STATES,
)
from common.step_specs import steps_for
from orchestration.models import Job, JobStatus

_JOB_FINISHED = (JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.STOPPED)


class StepError(Exception):
    """A step report or answer that cannot be applied; `status_code` is the HTTP code to answer with."""

    def __init__(self, message: str, status_code: int = 409) -> None:
        super().__init__(message)
        self.status_code = status_code


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def init_steps(conn: sqlite3.Connection, job: Job) -> None:
    """Insert the Job's declared steps, all pending. Idempotent."""
    if conn.execute("SELECT 1 FROM job_steps WHERE job_id = ? LIMIT 1", (job.id,)).fetchone():
        return
    conn.executemany(
        "INSERT INTO job_steps (job_id, position, key, title, needs_attention) VALUES (?, ?, ?, ?, ?)",
        [(job.id, i, spec.key, spec.title, int(spec.needs_attention)) for i, spec in enumerate(steps_for(job.type))],
    )
    conn.commit()


def delete_steps(conn: sqlite3.Connection, job_ids: list[str]) -> None:
    conn.executemany("DELETE FROM job_steps WHERE job_id = ?", [(i,) for i in job_ids])


def _rows(conn: sqlite3.Connection, job_id: str) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM job_steps WHERE job_id = ? ORDER BY position", (job_id,)).fetchall()


def expected_seconds(conn: sqlite3.Connection, job_type: str, key: str) -> float | None:
    """The average runtime of this step's last `EXPECTED_RUNTIME_SAMPLES` successful runs."""
    row = conn.execute(
        """SELECT AVG(seconds) AS avg FROM (
               SELECT seconds FROM step_durations WHERE job_type = ? AND step_key = ?
               ORDER BY id DESC LIMIT ?)""",
        (job_type, key, EXPECTED_RUNTIME_SAMPLES),
    ).fetchone()
    return row["avg"] if row and row["avg"] is not None else None


def _runtime(row: sqlite3.Row, now: datetime) -> float | None:
    started = _parse(row["started_at"])
    if started is None:
        return None
    end = _parse(row["finished_at"]) or now
    waited = row["waited_seconds"] or 0.0
    since = _parse(row["attention_since"])
    if since is not None and row["finished_at"] is None:
        waited += (now - since).total_seconds()  # still waiting: that time is not running time
    return max(0.0, (end - started).total_seconds() - waited)


def list_steps(conn: sqlite3.Connection, job: Job, *, now: datetime | None = None) -> list[dict]:
    """The Job's checklist for display, created on the fly for a Job that predates steps."""
    now = now or _now()
    rows = _rows(conn, job.id)
    if not rows:
        init_steps(conn, job)
        if job.status in _JOB_FINISHED or job.status == JobStatus.RUNNING:
            on_job_updated(conn, job)
        rows = _rows(conn, job.id)
    steps = []
    for row in rows:
        prompt = json.loads(row["prompt"]) if row["prompt"] else None
        started = _parse(row["started_at"])
        since = _parse(row["attention_since"])
        steps.append({
            "key": row["key"],
            "title": row["title"],
            "position": row["position"],
            "needs_attention": bool(row["needs_attention"]),
            "state": row["state"],
            "detail": row["detail"],
            "prompt": prompt if row["state"] == ATTENTION else None,
            "answer": row["answer"],
            "started_at": started.isoformat() if started else None,
            "finished_at": row["finished_at"],
            "attention_since": since.isoformat() if since else None,
            "attention_expires_at": (since + timedelta(seconds=ATTENTION_TIMEOUT_SECONDS)).isoformat() if since else None,
            "runtime_seconds": _runtime(row, now),
            "expected_seconds": expected_seconds(conn, job.type, row["key"]),
        })
    return steps


def progress(conn: sqlite3.Connection, job_id: str) -> dict:
    rows = _rows(conn, job_id)
    current = next((r for r in rows if r["state"] in (RUNNING, ATTENTION)), None)
    return {
        "total": len(rows),
        "done": sum(1 for r in rows if r["state"] == SUCCEEDED),
        "current": current["title"] if current else None,
        "attention": any(r["state"] == ATTENTION for r in rows),
    }


def _finish(conn: sqlite3.Connection, job: Job, row: sqlite3.Row, state: str, detail: str | None, now: datetime) -> None:
    started = _parse(row["started_at"]) or now
    since = _parse(row["attention_since"])
    waited = (row["waited_seconds"] or 0.0) + ((now - since).total_seconds() if since else 0.0)
    conn.execute(
        """UPDATE job_steps SET state = ?, started_at = ?, finished_at = ?, waited_seconds = ?, attention_since = NULL,
           detail = COALESCE(?, detail), prompt = NULL WHERE job_id = ? AND key = ?""",
        (state, _iso(started), _iso(now), waited, detail, job.id, row["key"]),
    )
    if state == SUCCEEDED and row["started_at"]:
        seconds = max(0.0, (now - started).total_seconds() - waited)
        conn.execute(
            "INSERT INTO step_durations (job_type, step_key, seconds, finished_at) VALUES (?, ?, ?, ?)",
            (job.type, row["key"], seconds, _iso(now)),
        )
        conn.execute(
            """DELETE FROM step_durations WHERE job_type = ? AND step_key = ? AND id NOT IN (
                   SELECT id FROM step_durations WHERE job_type = ? AND step_key = ? ORDER BY id DESC LIMIT ?)""",
            (job.type, row["key"], job.type, row["key"], EXPECTED_RUNTIME_SAMPLES),
        )


def report(
    conn: sqlite3.Connection, job: Job, key: str, state: str, *, detail: str | None = None, prompt: dict | None = None,
    now: datetime | None = None,
) -> dict:
    """Apply a pillar's report on one step. Reporting the state a step is already in changes nothing."""
    now = now or _now()
    if state not in STEP_STATES or state == PENDING:
        raise StepError(f"cannot report a step as {state!r}", 422)
    if job.status in _JOB_FINISHED:
        raise StepError(f"the job is {job.status.value}", 409)
    init_steps(conn, job)
    row = conn.execute("SELECT * FROM job_steps WHERE job_id = ? AND key = ?", (job.id, key)).fetchone()
    if row is None:
        raise StepError(f"job type {job.type!r} has no step {key!r}", 404)
    current = row["state"]

    if state == RUNNING:
        if current == RUNNING:
            return {"key": key, "state": current}
        restart = current in TERMINAL_STEP_STATES
        started = _iso(now) if restart or row["started_at"] is None else row["started_at"]
        since = _parse(row["attention_since"])
        waited = 0.0 if restart else (row["waited_seconds"] or 0.0) + ((now - since).total_seconds() if since else 0.0)
        conn.execute(
            "UPDATE job_steps SET state = ?, started_at = ?, finished_at = NULL, waited_seconds = ?, attention_since = NULL, "
            "detail = CASE WHEN ? THEN NULL ELSE detail END, prompt = NULL WHERE job_id = ? AND key = ?",
            (RUNNING, started, waited, int(restart), job.id, key),
        )
    elif state == ATTENTION:
        if current == ATTENTION:
            return {"key": key, "state": current}
        conn.execute(
            "UPDATE job_steps SET state = ?, started_at = COALESCE(started_at, ?), attention_since = ?, prompt = ?, answer = NULL, "
            "finished_at = NULL WHERE job_id = ? AND key = ?",
            (ATTENTION, _iso(now), _iso(now), json.dumps(prompt or {}), job.id, key),
        )
    else:
        _finish(conn, job, row, state, detail, now)
    conn.commit()
    _touch(conn, job, now)
    return {"key": key, "state": state}


def _touch(conn: sqlite3.Connection, job: Job, now: datetime) -> None:
    """A step moving is a sign of life: the stuck-Job sweep measures from the Job's last change."""
    conn.execute("UPDATE jobs SET updated_at = ? WHERE id = ?", (_iso(now), job.id))
    conn.commit()
    job.updated_at = now
    from orchestration import changes

    changes.note_job(job, kind="step")


def answer(conn: sqlite3.Connection, job: Job, key: str, choice: str, *, now: datetime | None = None) -> dict:
    """The user's answer to a step awaiting attention: the step runs again."""
    now = now or _now()
    row = conn.execute("SELECT * FROM job_steps WHERE job_id = ? AND key = ?", (job.id, key)).fetchone()
    if row is None:
        raise StepError(f"no step {key!r} on this job", 404)
    if row["state"] != ATTENTION:
        raise StepError(f"step {key!r} is not waiting for an answer (it is {row['state']})", 409)
    options = (json.loads(row["prompt"]) if row["prompt"] else {}).get("options", [])
    if choice not in {option["id"] for option in options}:
        raise StepError(f"{choice!r} is not one of the options: {', '.join(o['id'] for o in options)}", 422)
    since = _parse(row["attention_since"]) or now
    conn.execute(
        "UPDATE job_steps SET state = ?, answer = ?, attention_since = NULL, prompt = NULL, waited_seconds = waited_seconds + ? "
        "WHERE job_id = ? AND key = ?",
        (RUNNING, choice, (now - since).total_seconds(), job.id, key),
    )
    conn.commit()
    _touch(conn, job, now)
    return {"key": key, "state": RUNNING, "answer": choice}


def has_attention(conn: sqlite3.Connection, job_id: str) -> bool:
    return conn.execute("SELECT 1 FROM job_steps WHERE job_id = ? AND state = ? LIMIT 1", (job_id, ATTENTION)).fetchone() is not None


def expired_attention(conn: sqlite3.Connection, now: datetime | None = None) -> list[tuple[str, str]]:
    """(job_id, step key) of every step that has waited for the user longer than the limit."""
    cutoff = _iso((now or _now()) - timedelta(seconds=ATTENTION_TIMEOUT_SECONDS))
    rows = conn.execute("SELECT job_id, key FROM job_steps WHERE state = ? AND attention_since <= ?", (ATTENTION, cutoff)).fetchall()
    return [(row["job_id"], row["key"]) for row in rows]


def fail_step(conn: sqlite3.Connection, job: Job, key: str, detail: str, *, now: datetime | None = None) -> None:
    row = conn.execute("SELECT * FROM job_steps WHERE job_id = ? AND key = ?", (job.id, key)).fetchone()
    if row is not None and row["state"] not in TERMINAL_STEP_STATES:
        _finish(conn, job, row, FAILED, detail, now or _now())
        conn.commit()


def on_job_updated(conn: sqlite3.Connection, job: Job) -> None:
    """Keep the checklist consistent with the Job's own status; called on every Job write.

    A Job type with a single step has nothing to report: that step is the Job, running while it runs.
    When a Job ends, the step in progress fails with it and, on success, whatever was never reported
    counts as done (a step a particular run had no need for)."""
    init_steps(conn, job)
    rows = _rows(conn, job.id)
    now = _now()
    if job.status == JobStatus.RUNNING and len(rows) == 1 and rows[0]["state"] == PENDING:
        conn.execute("UPDATE job_steps SET state = ?, started_at = ? WHERE job_id = ?", (RUNNING, _iso(now), job.id))
    elif job.status == JobStatus.QUEUED and len(rows) == 1 and rows[0]["state"] == RUNNING:
        conn.execute("UPDATE job_steps SET state = ?, started_at = NULL WHERE job_id = ?", (PENDING, job.id))
    elif job.status in _JOB_FINISHED:
        for row in rows:
            if row["state"] in TERMINAL_STEP_STATES:
                continue
            if job.status == JobStatus.SUCCEEDED:
                _finish(conn, job, row, SUCCEEDED, None if row["started_at"] else "not needed this time", now)
            elif row["state"] in (RUNNING, ATTENTION):
                _finish(conn, job, row, FAILED, job.error or job.status.value, now)
    conn.commit()
