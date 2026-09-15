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

from orchestration.models import Job, JobStatus, Pipeline, PipelineNode, PipelineRun

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
