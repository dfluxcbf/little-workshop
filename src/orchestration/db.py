"""Orchestration's own SQLite file — `jobs`, `job_runs`, `pipelines`,
`pipeline_runs` (docs/architecture.md's Data model). WAL mode, one file,
opened only by this pillar — no other pillar, and no other process,
ever queries it directly.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from orchestration import steps
from orchestration.models import (
    Job,
    JobStatus,
    JobTemplate,
    Pipeline,
    PipelineNode,
    PipelineRun,
    Schedule,
    ScheduledTask,
    ScheduledTaskState,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    target_pillar TEXT NOT NULL,
    project_id TEXT,
    params TEXT NOT NULL,
    status TEXT NOT NULL,
    queue_key TEXT,
    depends_on TEXT NOT NULL,
    created_by TEXT NOT NULL,
    result TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS job_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    outcome TEXT NOT NULL,
    detail TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    FOREIGN KEY (job_id) REFERENCES jobs (id)
);

CREATE TABLE IF NOT EXISTS job_steps (
    job_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    key TEXT NOT NULL,
    title TEXT NOT NULL,
    needs_attention INTEGER NOT NULL DEFAULT 0,
    state TEXT NOT NULL DEFAULT 'pending',
    started_at TEXT,
    finished_at TEXT,
    waited_seconds REAL NOT NULL DEFAULT 0,
    attention_since TEXT,
    detail TEXT,
    prompt TEXT,
    answer TEXT,
    PRIMARY KEY (job_id, key)
);

-- Runtimes of steps that finished OK, for each step's expected runtime; kept
-- apart from job_steps so deleting old jobs does not forget them.
CREATE TABLE IF NOT EXISTS step_durations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_type TEXT NOT NULL,
    step_key TEXT NOT NULL,
    seconds REAL NOT NULL,
    finished_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS step_durations_by_step ON step_durations (job_type, step_key, id);

CREATE TABLE IF NOT EXISTS pipelines (
    name TEXT NOT NULL,
    version INTEGER NOT NULL,
    nodes TEXT NOT NULL,
    PRIMARY KEY (name, version)
);

CREATE TABLE IF NOT EXISTS pipeline_runs (
    id TEXT PRIMARY KEY,
    pipeline_name TEXT NOT NULL,
    pipeline_version INTEGER NOT NULL,
    project_id TEXT,
    created_by TEXT NOT NULL,
    job_ids TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scheduled_tasks (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    job_template TEXT NOT NULL,
    schedule TEXT NOT NULL,
    state TEXT NOT NULL,
    next_run_at TEXT,
    missed_run_at TEXT,
    last_job_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- docs/milestones.md M9's `queue` operation: queue_key gains a
-- configurable capacity (default 1, i.e. the original mutex) instead of
-- a second, parallel concurrency mechanism. Absence of a row means
-- capacity 1 — see queue.get_capacity.
CREATE TABLE IF NOT EXISTS queue_capacities (
    queue_key TEXT PRIMARY KEY,
    capacity INTEGER NOT NULL
);
"""


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False: FastAPI runs sync endpoint functions in a
    # threadpool, so the connection is legitimately used from more than
    # one thread. Callers that need real concurrency safety (the PI app)
    # serialize access with their own lock — see orchestration/app.py.
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)


def job_to_row(job: Job) -> dict:
    return {
        "id": job.id,
        "type": job.type,
        "target_pillar": job.target_pillar,
        "project_id": job.project_id,
        "params": json.dumps(job.params),
        "status": job.status.value,
        "queue_key": job.queue_key,
        "depends_on": json.dumps(job.depends_on),
        "created_by": job.created_by,
        "result": json.dumps(job.result) if job.result is not None else None,
        "error": job.error,
        "created_at": _iso(job.created_at),
        "updated_at": _iso(job.updated_at),
    }


def row_to_job(row: sqlite3.Row) -> Job:
    return Job(
        id=row["id"],
        type=row["type"],
        target_pillar=row["target_pillar"],
        project_id=row["project_id"],
        params=json.loads(row["params"]),
        status=JobStatus(row["status"]),
        queue_key=row["queue_key"],
        depends_on=json.loads(row["depends_on"]),
        created_by=row["created_by"],
        result=json.loads(row["result"]) if row["result"] is not None else None,
        error=row["error"],
        created_at=_parse_iso(row["created_at"]),
        updated_at=_parse_iso(row["updated_at"]),
    )


def insert_job(conn: sqlite3.Connection, job: Job) -> None:
    row = job_to_row(job)
    conn.execute(
        """INSERT INTO jobs
           (id, type, target_pillar, project_id, params, status, queue_key,
            depends_on, created_by, result, error, created_at, updated_at)
           VALUES (:id, :type, :target_pillar, :project_id, :params, :status,
            :queue_key, :depends_on, :created_by, :result, :error,
            :created_at, :updated_at)""",
        row,
    )
    conn.commit()
    steps.init_steps(conn, job)


def update_job(conn: sqlite3.Connection, job: Job) -> None:
    job.updated_at = datetime.now(timezone.utc)
    row = job_to_row(job)
    conn.execute(
        """UPDATE jobs SET type=:type, target_pillar=:target_pillar,
           project_id=:project_id, params=:params, status=:status,
           queue_key=:queue_key, depends_on=:depends_on, created_by=:created_by,
           result=:result, error=:error, updated_at=:updated_at
           WHERE id=:id""",
        row,
    )
    conn.commit()
    steps.on_job_updated(conn, job)


def get_job(conn: sqlite3.Connection, job_id: str) -> Job | None:
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return row_to_job(row) if row else None


def list_jobs(conn: sqlite3.Connection, *, status: JobStatus | None = None) -> list[Job]:
    if status is None:
        rows = conn.execute("SELECT * FROM jobs ORDER BY created_at").fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE status = ? ORDER BY created_at", (status.value,)
        ).fetchall()
    return [row_to_job(row) for row in rows]


def insert_job_run(
    conn: sqlite3.Connection,
    job_id: str,
    outcome: str,
    detail: str | None,
    started_at: datetime,
    finished_at: datetime | None,
) -> None:
    conn.execute(
        """INSERT INTO job_runs (job_id, outcome, detail, started_at, finished_at)
           VALUES (?, ?, ?, ?, ?)""",
        (
            job_id,
            outcome,
            detail,
            _iso(started_at),
            _iso(finished_at) if finished_at else None,
        ),
    )
    conn.commit()


def list_job_runs(conn: sqlite3.Connection, job_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM job_runs WHERE job_id = ? ORDER BY started_at", (job_id,)
    ).fetchall()
    return [dict(row) for row in rows]


def save_pipeline(conn: sqlite3.Connection, pipeline: Pipeline) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO pipelines (name, version, nodes) VALUES (?, ?, ?)",
        (
            pipeline.name,
            pipeline.version,
            json.dumps([node.model_dump() for node in pipeline.nodes]),
        ),
    )
    conn.commit()


def get_pipeline(
    conn: sqlite3.Connection, name: str, version: int | None = None
) -> Pipeline | None:
    if version is None:
        row = conn.execute(
            "SELECT * FROM pipelines WHERE name = ? ORDER BY version DESC LIMIT 1", (name,)
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM pipelines WHERE name = ? AND version = ?", (name, version)
        ).fetchone()
    if row is None:
        return None
    nodes = [PipelineNode(**node) for node in json.loads(row["nodes"])]
    return Pipeline(name=row["name"], version=row["version"], nodes=nodes)


def insert_pipeline_run(conn: sqlite3.Connection, run: PipelineRun) -> None:
    conn.execute(
        """INSERT INTO pipeline_runs
           (id, pipeline_name, pipeline_version, project_id, created_by, job_ids, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            run.id,
            run.pipeline_name,
            run.pipeline_version,
            run.project_id,
            run.created_by,
            json.dumps(run.job_ids),
            _iso(run.created_at),
        ),
    )
    conn.commit()


def _iso_naive(dt: datetime | None) -> str | None:
    """`ScheduledTask`'s own fields are naive, host-local-time datetimes
    (`docs/pillars/orchestration.md`'s "Scheduled tasks") — never the
    UTC-aware `_iso`/`_parse_iso` pair every other timestamp in this
    module uses."""
    return dt.isoformat() if dt is not None else None


def _parse_iso_naive(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value is not None else None


def scheduled_task_to_row(task: ScheduledTask) -> dict:
    return {
        "id": task.id,
        "name": task.name,
        "job_template": task.job_template.model_dump_json(),
        "schedule": task.schedule.model_dump_json(),
        "state": task.state.value,
        "next_run_at": _iso_naive(task.next_run_at),
        "missed_run_at": _iso_naive(task.missed_run_at),
        "last_job_id": task.last_job_id,
        "created_at": _iso(task.created_at),
        "updated_at": _iso(task.updated_at),
    }


def row_to_scheduled_task(row: sqlite3.Row) -> ScheduledTask:
    return ScheduledTask(
        id=row["id"],
        name=row["name"],
        job_template=JobTemplate.model_validate_json(row["job_template"]),
        schedule=Schedule.model_validate_json(row["schedule"]),
        state=ScheduledTaskState(row["state"]),
        next_run_at=_parse_iso_naive(row["next_run_at"]),
        missed_run_at=_parse_iso_naive(row["missed_run_at"]),
        last_job_id=row["last_job_id"],
        created_at=_parse_iso(row["created_at"]),
        updated_at=_parse_iso(row["updated_at"]),
    )


def insert_scheduled_task(conn: sqlite3.Connection, task: ScheduledTask) -> None:
    row = scheduled_task_to_row(task)
    conn.execute(
        """INSERT INTO scheduled_tasks
           (id, name, job_template, schedule, state, next_run_at, missed_run_at,
            last_job_id, created_at, updated_at)
           VALUES (:id, :name, :job_template, :schedule, :state, :next_run_at,
            :missed_run_at, :last_job_id, :created_at, :updated_at)""",
        row,
    )
    conn.commit()


def update_scheduled_task(conn: sqlite3.Connection, task: ScheduledTask) -> None:
    task.updated_at = datetime.now(timezone.utc)
    row = scheduled_task_to_row(task)
    conn.execute(
        """UPDATE scheduled_tasks SET name=:name, job_template=:job_template,
           schedule=:schedule, state=:state, next_run_at=:next_run_at,
           missed_run_at=:missed_run_at, last_job_id=:last_job_id, updated_at=:updated_at
           WHERE id=:id""",
        row,
    )
    conn.commit()


def get_scheduled_task(conn: sqlite3.Connection, task_id: str) -> ScheduledTask | None:
    row = conn.execute("SELECT * FROM scheduled_tasks WHERE id = ?", (task_id,)).fetchone()
    return row_to_scheduled_task(row) if row else None


def list_scheduled_tasks(
    conn: sqlite3.Connection, *, state: ScheduledTaskState | None = None
) -> list[ScheduledTask]:
    if state is None:
        rows = conn.execute("SELECT * FROM scheduled_tasks ORDER BY created_at").fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM scheduled_tasks WHERE state = ? ORDER BY created_at", (state.value,)
        ).fetchall()
    return [row_to_scheduled_task(row) for row in rows]


def delete_scheduled_task(conn: sqlite3.Connection, task_id: str) -> None:
    conn.execute("DELETE FROM scheduled_tasks WHERE id = ?", (task_id,))
    conn.commit()


def get_queue_capacity(conn: sqlite3.Connection, queue_key: str) -> int:
    row = conn.execute(
        "SELECT capacity FROM queue_capacities WHERE queue_key = ?", (queue_key,)
    ).fetchone()
    return row["capacity"] if row else 1


def set_queue_capacity(conn: sqlite3.Connection, queue_key: str, capacity: int) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO queue_capacities (queue_key, capacity) VALUES (?, ?)",
        (queue_key, capacity),
    )
    conn.commit()


def get_pipeline_run(conn: sqlite3.Connection, run_id: str) -> PipelineRun | None:
    row = conn.execute("SELECT * FROM pipeline_runs WHERE id = ?", (run_id,)).fetchone()
    if row is None:
        return None
    return PipelineRun(
        id=row["id"],
        pipeline_name=row["pipeline_name"],
        pipeline_version=row["pipeline_version"],
        project_id=row["project_id"],
        created_by=row["created_by"],
        job_ids=json.loads(row["job_ids"]),
        created_at=_parse_iso(row["created_at"]),
    )


# -- cleanup of old, finished jobs ---------------------------------------------

FINISHED_STATUSES = (JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.STOPPED)


def _plan_cleanup(conn: sqlite3.Connection, before: datetime) -> tuple[set[str], list[str], int]:
    """(job ids to delete, pipeline run ids to delete, eligible jobs kept for safety).

    Only *finished* jobs last updated before `before` are eligible. Kept regardless:
    a job an unfinished job still depends on (the queue reads its status), a
    scheduled task's last job, and every job of a pipeline run that is not wholly
    eligible — a run is removed whole or kept whole, never left partial."""
    jobs = {job.id: job for job in list_jobs(conn)}
    eligible = {i for i, job in jobs.items() if job.status in FINISHED_STATUSES and job.updated_at < before}

    protected: set[str] = set()
    for job in jobs.values():
        if job.status not in FINISHED_STATUSES:
            protected.update(job.depends_on)
    for row in conn.execute("SELECT last_job_id FROM scheduled_tasks WHERE last_job_id IS NOT NULL"):
        protected.add(row["last_job_id"])

    def run_job_ids(raw: str) -> list[str]:
        loaded = json.loads(raw)  # {node_id: job_id}
        return [i for i in (loaded.values() if isinstance(loaded, dict) else loaded) if i in jobs]

    runs = [
        (row["id"], run_job_ids(row["job_ids"]), _parse_iso(row["created_at"]))
        for row in conn.execute("SELECT id, job_ids, created_at FROM pipeline_runs")
    ]
    changed = True
    while changed:
        changed = False
        for _, present, _created in runs:
            if any(i not in eligible or i in protected for i in present):
                for i in present:
                    if i not in protected:
                        protected.add(i)
                        changed = True

    to_delete = eligible - protected
    runs_to_delete = [
        run_id for run_id, present, created in runs
        if (all(i in to_delete for i in present) if present else created < before)
    ]
    return to_delete, runs_to_delete, len(eligible & protected)


def count_prunable_jobs(conn: sqlite3.Connection, before: datetime) -> int:
    return len(_plan_cleanup(conn, before)[0])


def prune_finished_jobs(conn: sqlite3.Connection, before: datetime) -> dict:
    to_delete, runs_to_delete, kept = _plan_cleanup(conn, before)
    steps.delete_steps(conn, list(to_delete))
    conn.executemany("DELETE FROM job_runs WHERE job_id = ?", [(i,) for i in to_delete])
    conn.executemany("DELETE FROM jobs WHERE id = ?", [(i,) for i in to_delete])
    conn.executemany("DELETE FROM pipeline_runs WHERE id = ?", [(i,) for i in runs_to_delete])
    conn.commit()
    return {"deleted": len(to_delete), "pipeline_runs_deleted": len(runs_to_delete), "kept_for_safety": kept}
