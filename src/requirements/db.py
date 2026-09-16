"""Requirements & Quality's own SQLite file — `requirements`,
`test_suites`, `test_results`, `use_cases`, `milestones`, plus the join
table linking a requirement to the suites that cover it
(docs/architecture.md's Data model). WAL mode, one file, opened only by
this pillar — carries `little-requirements`' N-per-project-SQLite-files
model into one pillar-owned, `project_id`-scoped database.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from requirements.models import (
    Milestone,
    Requirement,
    RequirementStatus,
    TestResult,
    TestSuite,
    TestVerdict,
    UseCase,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS test_suites (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS use_cases (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS milestones (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS requirements (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    key TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    use_case_id TEXT,
    milestone_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS requirement_test_mappings (
    requirement_id TEXT NOT NULL,
    suite_id TEXT NOT NULL,
    PRIMARY KEY (requirement_id, suite_id)
);

CREATE TABLE IF NOT EXISTS test_results (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    suite_id TEXT NOT NULL,
    test_name TEXT NOT NULL,
    verdict TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'manual',
    executed_at TEXT NOT NULL
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


# -- test suites -----------------------------------------------------------


def insert_test_suite(conn: sqlite3.Connection, suite: TestSuite) -> None:
    conn.execute(
        "INSERT INTO test_suites (id, project_id, name, description, created_at) VALUES (?, ?, ?, ?, ?)",
        (suite.id, suite.project_id, suite.name, suite.description, _iso(suite.created_at)),
    )
    conn.commit()


def _row_to_suite(row: sqlite3.Row) -> TestSuite:
    return TestSuite(
        id=row["id"], project_id=row["project_id"], name=row["name"],
        description=row["description"], created_at=_parse_iso(row["created_at"]),
    )


def get_test_suite(conn: sqlite3.Connection, suite_id: str) -> TestSuite | None:
    row = conn.execute("SELECT * FROM test_suites WHERE id = ?", (suite_id,)).fetchone()
    return _row_to_suite(row) if row else None


def list_test_suites(conn: sqlite3.Connection, *, project_id: str | None = None) -> list[TestSuite]:
    if project_id is None:
        rows = conn.execute("SELECT * FROM test_suites ORDER BY created_at").fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM test_suites WHERE project_id = ? ORDER BY created_at", (project_id,)
        ).fetchall()
    return [_row_to_suite(row) for row in rows]


def delete_test_suite(conn: sqlite3.Connection, suite_id: str) -> None:
    conn.execute("DELETE FROM test_suites WHERE id = ?", (suite_id,))
    conn.execute("DELETE FROM requirement_test_mappings WHERE suite_id = ?", (suite_id,))
    conn.execute("DELETE FROM test_results WHERE suite_id = ?", (suite_id,))
    conn.commit()


# -- use cases ---------------------------------------------------------------


def insert_use_case(conn: sqlite3.Connection, use_case: UseCase) -> None:
    conn.execute(
        "INSERT INTO use_cases (id, project_id, name, description, created_at) VALUES (?, ?, ?, ?, ?)",
        (use_case.id, use_case.project_id, use_case.name, use_case.description, _iso(use_case.created_at)),
    )
    conn.commit()


def _row_to_use_case(row: sqlite3.Row) -> UseCase:
    return UseCase(
        id=row["id"], project_id=row["project_id"], name=row["name"],
        description=row["description"], created_at=_parse_iso(row["created_at"]),
    )


def get_use_case(conn: sqlite3.Connection, use_case_id: str) -> UseCase | None:
    row = conn.execute("SELECT * FROM use_cases WHERE id = ?", (use_case_id,)).fetchone()
    return _row_to_use_case(row) if row else None


def list_use_cases(conn: sqlite3.Connection, *, project_id: str | None = None) -> list[UseCase]:
    if project_id is None:
        rows = conn.execute("SELECT * FROM use_cases ORDER BY created_at").fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM use_cases WHERE project_id = ? ORDER BY created_at", (project_id,)
        ).fetchall()
    return [_row_to_use_case(row) for row in rows]


# -- milestones ----------------------------------------------------------


def insert_milestone(conn: sqlite3.Connection, milestone: Milestone) -> None:
    conn.execute(
        "INSERT INTO milestones (id, project_id, name, description, created_at) VALUES (?, ?, ?, ?, ?)",
        (milestone.id, milestone.project_id, milestone.name, milestone.description, _iso(milestone.created_at)),
    )
    conn.commit()


def _row_to_milestone(row: sqlite3.Row) -> Milestone:
    return Milestone(
        id=row["id"], project_id=row["project_id"], name=row["name"],
        description=row["description"], created_at=_parse_iso(row["created_at"]),
    )


def get_milestone(conn: sqlite3.Connection, milestone_id: str) -> Milestone | None:
    row = conn.execute("SELECT * FROM milestones WHERE id = ?", (milestone_id,)).fetchone()
    return _row_to_milestone(row) if row else None


def list_milestones(conn: sqlite3.Connection, *, project_id: str | None = None) -> list[Milestone]:
    if project_id is None:
        rows = conn.execute("SELECT * FROM milestones ORDER BY created_at").fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM milestones WHERE project_id = ? ORDER BY created_at", (project_id,)
        ).fetchall()
    return [_row_to_milestone(row) for row in rows]


# -- requirements ----------------------------------------------------------


def insert_requirement(conn: sqlite3.Connection, requirement: Requirement) -> None:
    conn.execute(
        """INSERT INTO requirements
           (id, project_id, key, title, description, category, status, use_case_id, milestone_id, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            requirement.id, requirement.project_id, requirement.key, requirement.title,
            requirement.description, requirement.category, requirement.status.value,
            requirement.use_case_id, requirement.milestone_id,
            _iso(requirement.created_at), _iso(requirement.updated_at),
        ),
    )
    conn.commit()


def _row_to_requirement(row: sqlite3.Row) -> Requirement:
    return Requirement(
        id=row["id"], project_id=row["project_id"], key=row["key"], title=row["title"],
        description=row["description"], category=row["category"],
        status=RequirementStatus(row["status"]),
        use_case_id=row["use_case_id"], milestone_id=row["milestone_id"],
        created_at=_parse_iso(row["created_at"]), updated_at=_parse_iso(row["updated_at"]),
    )


def get_requirement(conn: sqlite3.Connection, requirement_id: str) -> Requirement | None:
    row = conn.execute("SELECT * FROM requirements WHERE id = ?", (requirement_id,)).fetchone()
    return _row_to_requirement(row) if row else None


def list_requirements(
    conn: sqlite3.Connection, *, project_id: str | None = None, status: RequirementStatus | None = None
) -> list[Requirement]:
    query = "SELECT * FROM requirements WHERE 1=1"
    params: list[str] = []
    if project_id is not None:
        query += " AND project_id = ?"
        params.append(project_id)
    if status is not None:
        query += " AND status = ?"
        params.append(status.value)
    query += " ORDER BY created_at"
    rows = conn.execute(query, params).fetchall()
    return [_row_to_requirement(row) for row in rows]


def update_requirement(conn: sqlite3.Connection, requirement: Requirement) -> None:
    requirement.updated_at = datetime.now(timezone.utc)
    conn.execute(
        """UPDATE requirements SET title = ?, description = ?, category = ?, status = ?,
           use_case_id = ?, milestone_id = ?, updated_at = ? WHERE id = ?""",
        (
            requirement.title, requirement.description, requirement.category, requirement.status.value,
            requirement.use_case_id, requirement.milestone_id, _iso(requirement.updated_at), requirement.id,
        ),
    )
    conn.commit()


def delete_requirement(conn: sqlite3.Connection, requirement_id: str) -> None:
    conn.execute("DELETE FROM requirements WHERE id = ?", (requirement_id,))
    conn.execute("DELETE FROM requirement_test_mappings WHERE requirement_id = ?", (requirement_id,))
    conn.commit()


# -- requirement <-> test suite mappings ------------------------------------


def link_requirement_to_suite(conn: sqlite3.Connection, requirement_id: str, suite_id: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO requirement_test_mappings (requirement_id, suite_id) VALUES (?, ?)",
        (requirement_id, suite_id),
    )
    conn.commit()


def unlink_requirement_from_suite(conn: sqlite3.Connection, requirement_id: str, suite_id: str) -> None:
    conn.execute(
        "DELETE FROM requirement_test_mappings WHERE requirement_id = ? AND suite_id = ?",
        (requirement_id, suite_id),
    )
    conn.commit()


def list_suites_for_requirement(conn: sqlite3.Connection, requirement_id: str) -> list[str]:
    rows = conn.execute(
        "SELECT suite_id FROM requirement_test_mappings WHERE requirement_id = ? ORDER BY suite_id",
        (requirement_id,),
    ).fetchall()
    return [row["suite_id"] for row in rows]


# -- test results ------------------------------------------------------------


def insert_test_result(conn: sqlite3.Connection, result: TestResult) -> None:
    conn.execute(
        """INSERT INTO test_results (id, project_id, suite_id, test_name, verdict, detail, source, executed_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            result.id, result.project_id, result.suite_id, result.test_name, result.verdict.value,
            result.detail, result.source, _iso(result.executed_at),
        ),
    )
    conn.commit()


def _row_to_result(row: sqlite3.Row) -> TestResult:
    return TestResult(
        id=row["id"], project_id=row["project_id"], suite_id=row["suite_id"], test_name=row["test_name"],
        verdict=TestVerdict(row["verdict"]), detail=row["detail"], source=row["source"],
        executed_at=_parse_iso(row["executed_at"]),
    )


def list_test_results(
    conn: sqlite3.Connection, *, project_id: str | None = None, suite_id: str | None = None
) -> list[TestResult]:
    query = "SELECT * FROM test_results WHERE 1=1"
    params: list[str] = []
    if project_id is not None:
        query += " AND project_id = ?"
        params.append(project_id)
    if suite_id is not None:
        query += " AND suite_id = ?"
        params.append(suite_id)
    query += " ORDER BY executed_at"
    rows = conn.execute(query, params).fetchall()
    return [_row_to_result(row) for row in rows]


def latest_result_for_suite(conn: sqlite3.Connection, suite_id: str) -> TestResult | None:
    """The most recently recorded result for a suite, taken as a whole:
    the suite's own verdict for the definition-of-done report is
    "passed" only when every test recorded in its most recent run
    passed — so this returns the worst verdict among results sharing
    the latest `executed_at` batch, not just an arbitrary single row."""
    rows = conn.execute(
        "SELECT * FROM test_results WHERE suite_id = ? ORDER BY executed_at DESC", (suite_id,)
    ).fetchall()
    if not rows:
        return None
    latest_ts = rows[0]["executed_at"]
    batch = [r for r in rows if r["executed_at"] == latest_ts]
    priority = {TestVerdict.ERROR: 0, TestVerdict.FAILED: 1, TestVerdict.SKIPPED: 2, TestVerdict.PASSED: 3}
    worst = min(batch, key=lambda r: priority[TestVerdict(r["verdict"])])
    return _row_to_result(worst)
