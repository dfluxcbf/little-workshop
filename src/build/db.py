"""Build & Packages' own SQLite file — `build_runs` and `packages`
(docs/architecture.md's Data model: "metadata; blobs stay on disk"). WAL
mode, one file, opened only by this pillar.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from build.models import BuildRun, BuildRunStatus, Package, PackageKind

SCHEMA = """
CREATE TABLE IF NOT EXISTS build_runs (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    commitish TEXT NOT NULL,
    command TEXT NOT NULL,
    status TEXT NOT NULL,
    from_fork INTEGER NOT NULL DEFAULT 0,
    exit_code INTEGER,
    log TEXT NOT NULL DEFAULT '',
    started_at TEXT NOT NULL,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS packages (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    version TEXT NOT NULL,
    filename TEXT NOT NULL,
    source_build_run_id TEXT,
    uploaded_at TEXT NOT NULL
);
"""


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
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


def new_id() -> str:
    return uuid.uuid4().hex


# -- build runs --------------------------------------------------------------


def insert_build_run(conn: sqlite3.Connection, run: BuildRun) -> None:
    conn.execute(
        """INSERT INTO build_runs
           (id, project_id, commitish, command, status, from_fork, exit_code, log, started_at, finished_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            run.id, run.project_id, run.commitish, json.dumps(run.command), run.status.value,
            int(run.from_fork), run.exit_code, run.log, _iso(run.started_at),
            _iso(run.finished_at) if run.finished_at else None,
        ),
    )
    conn.commit()


def update_build_run(conn: sqlite3.Connection, run: BuildRun) -> None:
    conn.execute(
        "UPDATE build_runs SET status = ?, exit_code = ?, log = ?, finished_at = ? WHERE id = ?",
        (
            run.status.value, run.exit_code, run.log,
            _iso(run.finished_at) if run.finished_at else None, run.id,
        ),
    )
    conn.commit()


def _row_to_build_run(row: sqlite3.Row) -> BuildRun:
    return BuildRun(
        id=row["id"], project_id=row["project_id"], commitish=row["commitish"],
        command=json.loads(row["command"]), status=BuildRunStatus(row["status"]),
        from_fork=bool(row["from_fork"]), exit_code=row["exit_code"], log=row["log"],
        started_at=_parse_iso(row["started_at"]),
        finished_at=_parse_iso(row["finished_at"]) if row["finished_at"] else None,
    )


def get_build_run(conn: sqlite3.Connection, run_id: str) -> BuildRun | None:
    row = conn.execute("SELECT * FROM build_runs WHERE id = ?", (run_id,)).fetchone()
    return _row_to_build_run(row) if row else None


def list_build_runs(conn: sqlite3.Connection, *, project_id: str | None = None) -> list[BuildRun]:
    if project_id is None:
        rows = conn.execute("SELECT * FROM build_runs ORDER BY started_at").fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM build_runs WHERE project_id = ? ORDER BY started_at", (project_id,)
        ).fetchall()
    return [_row_to_build_run(row) for row in rows]


# -- packages ------------------------------------------------------------


def insert_package(conn: sqlite3.Connection, package: Package) -> None:
    conn.execute(
        """INSERT INTO packages
           (id, project_id, kind, name, normalized_name, version, filename, source_build_run_id, uploaded_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            package.id, package.project_id, package.kind.value, package.name, package.normalized_name,
            package.version, package.filename, package.source_build_run_id, _iso(package.uploaded_at),
        ),
    )
    conn.commit()


def _row_to_package(row: sqlite3.Row) -> Package:
    return Package(
        id=row["id"], project_id=row["project_id"], kind=PackageKind(row["kind"]), name=row["name"],
        normalized_name=row["normalized_name"], version=row["version"], filename=row["filename"],
        source_build_run_id=row["source_build_run_id"], uploaded_at=_parse_iso(row["uploaded_at"]),
    )


def get_package(conn: sqlite3.Connection, package_id: str) -> Package | None:
    row = conn.execute("SELECT * FROM packages WHERE id = ?", (package_id,)).fetchone()
    return _row_to_package(row) if row else None


def get_package_by_filename(conn: sqlite3.Connection, kind: PackageKind, filename: str) -> Package | None:
    row = conn.execute(
        "SELECT * FROM packages WHERE kind = ? AND filename = ?", (kind.value, filename)
    ).fetchone()
    return _row_to_package(row) if row else None


def list_packages(
    conn: sqlite3.Connection, *, project_id: str | None = None, kind: PackageKind | None = None,
    normalized_name: str | None = None,
) -> list[Package]:
    query = "SELECT * FROM packages WHERE 1=1"
    params: list[str] = []
    if project_id is not None:
        query += " AND project_id = ?"
        params.append(project_id)
    if kind is not None:
        query += " AND kind = ?"
        params.append(kind.value)
    if normalized_name is not None:
        query += " AND normalized_name = ?"
        params.append(normalized_name)
    query += " ORDER BY uploaded_at"
    rows = conn.execute(query, params).fetchall()
    return [_row_to_package(row) for row in rows]


def list_distinct_normalized_names(conn: sqlite3.Connection, *, kind: PackageKind) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT normalized_name FROM packages WHERE kind = ? ORDER BY normalized_name", (kind.value,)
    ).fetchall()
    return [row["normalized_name"] for row in rows]
