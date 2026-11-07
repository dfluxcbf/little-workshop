"""Requirements & Quality's own SQLite file — `requirements`,
`test_suites`, `test_results`, `use_cases`, `milestones`, plus the join
table linking a requirement to the suites that cover it
(docs/architecture.md's Data model). WAL mode, one file, opened only by
this pillar — carries `little-requirements`' N-per-project-SQLite-files
model into one pillar-owned, `project_id`-scoped database.
"""

from __future__ import annotations

import hashlib
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
    superseded_by TEXT,
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

-- `latest_result_for_suite` below is called once per requirement's own
-- suite mapping to build the Dashboard's /v1/report — without this,
-- every call is a full scan of the whole table (2026-09-23 finding:
-- little-workshop's own 41k+ accumulated rows made that report take
-- ~9.7s, the dominant cost of the Dashboard's home page).
CREATE INDEX IF NOT EXISTS idx_test_results_suite_executed ON test_results (suite_id, executed_at);

-- Conflicts between two requirements found by `requirements/conflicts.py`: one row per conflict id, kept while
-- the analysis still finds it (`open`, or `resolving` once the user chose a resolution) and `resolved` once a
-- later analysis no longer does.
CREATE TABLE IF NOT EXISTS requirement_conflicts (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    older_requirement_id TEXT NOT NULL,
    newer_requirement_id TEXT NOT NULL,
    older_key TEXT NOT NULL,
    newer_key TEXT NOT NULL,
    older_text TEXT NOT NULL,
    newer_text TEXT NOT NULL,
    summary TEXT NOT NULL,
    status TEXT NOT NULL,
    decision TEXT,
    guidance TEXT NOT NULL DEFAULT '',
    job_id TEXT,
    detected_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    decided_at TEXT,
    resolved_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_requirement_conflicts_project ON requirement_conflicts (project_id, status);

-- A project whose requirements changed since its last conflict analysis, and when they last changed
-- (REQ-000000473): analyzed once the change is old enough, then removed.
CREATE TABLE IF NOT EXISTS conflict_scan_due (
    project_id TEXT PRIMARY KEY,
    changed_at TEXT NOT NULL
);
"""


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    # An existing database predates the index above — CREATE INDEX IF NOT
    # EXISTS in the schema script only helps a fresh file's initial
    # executescript pass in some sqlite3 driver states; make it explicit
    # and idempotent here too, the same belt-and-suspenders pattern this
    # project already uses for a column added after a table shipped.
    conn.execute("CREATE INDEX IF NOT EXISTS idx_test_results_suite_executed ON test_results (suite_id, executed_at)")
    # `superseded_by` came after the table shipped (REQ-000000448).
    if "superseded_by" not in {row["name"] for row in conn.execute("PRAGMA table_info(requirements)")}:
        conn.execute("ALTER TABLE requirements ADD COLUMN superseded_by TEXT")
    # `requirement_key` came after the table shipped (REQ-000000500).
    if "requirement_key" not in {row["name"] for row in conn.execute("PRAGMA table_info(test_results)")}:
        conn.execute("ALTER TABLE test_results ADD COLUMN requirement_key TEXT")
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
           (id, project_id, key, title, description, category, status, use_case_id, milestone_id, superseded_by,
            created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            requirement.id, requirement.project_id, requirement.key, requirement.title,
            requirement.description, requirement.category, requirement.status.value,
            requirement.use_case_id, requirement.milestone_id, requirement.superseded_by,
            _iso(requirement.created_at), _iso(requirement.updated_at),
        ),
    )
    stamp_conflict_scan_due(conn, requirement.project_id)
    conn.commit()


def _row_to_requirement(row: sqlite3.Row) -> Requirement:
    return Requirement(
        id=row["id"], project_id=row["project_id"], key=row["key"], title=row["title"],
        description=row["description"], category=row["category"],
        status=RequirementStatus(row["status"]),
        use_case_id=row["use_case_id"], milestone_id=row["milestone_id"], superseded_by=row["superseded_by"],
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
           use_case_id = ?, milestone_id = ?, superseded_by = ?, updated_at = ? WHERE id = ?""",
        (
            requirement.title, requirement.description, requirement.category, requirement.status.value,
            requirement.use_case_id, requirement.milestone_id, requirement.superseded_by,
            _iso(requirement.updated_at), requirement.id,
        ),
    )
    stamp_conflict_scan_due(conn, requirement.project_id)
    conn.commit()


def delete_requirement(conn: sqlite3.Connection, requirement_id: str) -> None:
    row = conn.execute("SELECT project_id FROM requirements WHERE id = ?", (requirement_id,)).fetchone()
    if row is not None:
        stamp_conflict_scan_due(conn, row["project_id"])
    conn.execute("DELETE FROM requirements WHERE id = ?", (requirement_id,))
    conn.execute("DELETE FROM requirement_test_mappings WHERE requirement_id = ?", (requirement_id,))
    conn.commit()


# -- conflict analysis due (REQ-000000473) -----------------------------------


def _clock() -> datetime:
    return datetime.now(timezone.utc)


def stamp_conflict_scan_due(conn: sqlite3.Connection, project_id: str, at: datetime | None = None) -> None:
    """Record that the project's requirements changed now (or `at`); the caller commits."""
    conn.execute(
        "INSERT INTO conflict_scan_due (project_id, changed_at) VALUES (?, ?) "
        "ON CONFLICT(project_id) DO UPDATE SET changed_at = excluded.changed_at",
        (project_id, _iso(at or _clock())),
    )


def conflict_scans_due(conn: sqlite3.Connection, *, settled_before: datetime) -> list[tuple[str, str]]:
    """(project id, its stamp as stored) for every project whose last change is at or before `settled_before`."""
    rows = conn.execute("SELECT project_id, changed_at FROM conflict_scan_due ORDER BY project_id").fetchall()
    return [(r["project_id"], r["changed_at"]) for r in rows if _parse_iso(r["changed_at"]) <= settled_before]


def clear_conflict_scan_due(conn: sqlite3.Connection, project_id: str, changed_at: str) -> bool:
    """Remove the project's stamp only if it is still `changed_at`: a change made during the analysis stays due."""
    cleared = conn.execute(
        "DELETE FROM conflict_scan_due WHERE project_id = ? AND changed_at = ?", (project_id, changed_at),
    ).rowcount
    conn.commit()
    return bool(cleared)


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


def suites_for_requirements(conn: sqlite3.Connection, requirement_ids: list[str]) -> dict[str, list[str]]:
    """`{requirement_id: [suite_id, ...]}` for every id given, one query
    instead of one per requirement — the Definition-of-Done report
    (`/v1/report`) builds this for every requirement in a project at
    once; looping `list_suites_for_requirement` per requirement was part
    of the same N+1 pattern `latest_results_for_suites` below fixes."""
    result: dict[str, list[str]] = {rid: [] for rid in requirement_ids}
    if not requirement_ids:
        return result
    placeholders = ",".join("?" * len(requirement_ids))
    rows = conn.execute(
        f"SELECT requirement_id, suite_id FROM requirement_test_mappings "
        f"WHERE requirement_id IN ({placeholders}) ORDER BY suite_id",
        requirement_ids,
    ).fetchall()
    for row in rows:
        result[row["requirement_id"]].append(row["suite_id"])
    return result


# -- test results ------------------------------------------------------------


def insert_test_result(conn: sqlite3.Connection, result: TestResult) -> None:
    conn.execute(
        """INSERT INTO test_results (id, project_id, suite_id, test_name, verdict, detail, source, executed_at,
           requirement_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            result.id, result.project_id, result.suite_id, result.test_name, result.verdict.value,
            result.detail, result.source, _iso(result.executed_at), result.requirement_key,
        ),
    )
    conn.commit()


def _row_to_result(row: sqlite3.Row) -> TestResult:
    return TestResult(
        id=row["id"], project_id=row["project_id"], suite_id=row["suite_id"], test_name=row["test_name"],
        verdict=TestVerdict(row["verdict"]), detail=row["detail"], source=row["source"],
        executed_at=_parse_iso(row["executed_at"]), requirement_key=row["requirement_key"],
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
    the latest `executed_at` batch, not just an arbitrary single row.
    Two indexed lookups (the index above makes both an index-only range
    scan) rather than pulling every row a suite has ever recorded and
    sorting in Python — a suite accumulates one row per test per run
    forever, so that used to mean scanning the whole table's history on
    every single call."""
    latest_row = conn.execute(
        "SELECT MAX(executed_at) AS ts FROM test_results WHERE suite_id = ?", (suite_id,)
    ).fetchone()
    latest_ts = latest_row["ts"] if latest_row else None
    if latest_ts is None:
        return None
    batch = conn.execute(
        "SELECT * FROM test_results WHERE suite_id = ? AND executed_at = ?", (suite_id, latest_ts)
    ).fetchall()
    priority = {TestVerdict.ERROR: 0, TestVerdict.FAILED: 1, TestVerdict.SKIPPED: 2, TestVerdict.PASSED: 3}
    worst = min(batch, key=lambda r: priority[TestVerdict(r["verdict"])])
    return _row_to_result(worst)


_PRIORITY = {TestVerdict.ERROR: 0, TestVerdict.FAILED: 1, TestVerdict.SKIPPED: 2, TestVerdict.PASSED: 3}


def worst(results: list[TestResult]) -> TestResult:
    return min(results, key=lambda r: _PRIORITY[r.verdict])


def latest_runs_for_suites(conn: sqlite3.Connection, suite_ids: list[str]) -> dict[str, list[TestResult]]:
    """`{suite_id: [every result of its latest run]}`, one query; a suite never run is absent (REQ-000000500)."""
    if not suite_ids:
        return {}
    unique_ids = list(dict.fromkeys(suite_ids))
    placeholders = ",".join("?" * len(unique_ids))
    rows = conn.execute(
        f"""
        SELECT tr.* FROM test_results tr
        JOIN (
            SELECT suite_id, MAX(executed_at) AS ts FROM test_results
            WHERE suite_id IN ({placeholders}) GROUP BY suite_id
        ) latest ON tr.suite_id = latest.suite_id AND tr.executed_at = latest.ts
        WHERE tr.suite_id IN ({placeholders})
        """,
        [*unique_ids, *unique_ids],
    ).fetchall()
    runs: dict[str, list[TestResult]] = {}
    for row in rows:
        runs.setdefault(row["suite_id"], []).append(_row_to_result(row))
    return runs


def latest_results_for_suites(conn: sqlite3.Connection, suite_ids: list[str]) -> dict[str, TestResult]:
    """`{suite_id: TestResult}` for every id given (a suite with no
    results yet is simply absent, same as `latest_result_for_suite`
    returning None) — one join instead of calling
    `latest_result_for_suite` once per suite. The Definition-of-Done
    report (`/v1/report`) can involve hundreds of suites at once; even
    with the index above, hundreds of separate round trips each carry
    their own fixed per-query overhead (2026-09-23 finding: little-
    workshop's own report, ~420 requirement/suite mappings, still took
    ~0.5s from this alone after the index fix — this dropped it to a
    handful of milliseconds)."""
    if not suite_ids:
        return {}
    unique_ids = list(dict.fromkeys(suite_ids))
    placeholders = ",".join("?" * len(unique_ids))
    rows = conn.execute(
        f"""
        SELECT tr.* FROM test_results tr
        JOIN (
            SELECT suite_id, MAX(executed_at) AS ts FROM test_results
            WHERE suite_id IN ({placeholders}) GROUP BY suite_id
        ) latest ON tr.suite_id = latest.suite_id AND tr.executed_at = latest.ts
        WHERE tr.suite_id IN ({placeholders})
        """,
        [*unique_ids, *unique_ids],
    ).fetchall()
    by_suite: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        by_suite.setdefault(row["suite_id"], []).append(row)
    priority = {TestVerdict.ERROR: 0, TestVerdict.FAILED: 1, TestVerdict.SKIPPED: 2, TestVerdict.PASSED: 3}
    return {
        suite_id: _row_to_result(min(batch, key=lambda r: priority[TestVerdict(r["verdict"])]))
        for suite_id, batch in by_suite.items()
    }


# -- requirement conflicts ---------------------------------------------------------

CONFLICT_OPEN = "open"
CONFLICT_RESOLVING = "resolving"
CONFLICT_RESOLVED = "resolved"
CONFLICT_STATUSES = (CONFLICT_OPEN, CONFLICT_RESOLVING, CONFLICT_RESOLVED)


def record_conflict_analysis(conn: sqlite3.Connection, project_id: str, found: list) -> dict:
    """Bring a project's conflicts in line with an analysis (`conflicts.find_conflicts`' answer for it): a new
    one is recorded `open`, one resolved earlier and found again is `open` again, one no longer found is
    `resolved`. Answers {found, new, reopened, resolved} counts."""
    now = _iso(datetime.now(timezone.utc))
    # A `semantic` conflict was recorded by the user from Claude's check (REQ-000000523): the lexical analysis
    # neither finds nor resolves it.
    known = {row["id"]: row["status"] for row in conn.execute(
        "SELECT id, status FROM requirement_conflicts WHERE project_id = ? AND kind != 'semantic'", (project_id,))}
    new = reopened = 0
    for c in found:
        status = known.get(c.id)
        if status is None:
            new += 1
            conn.execute(
                """INSERT INTO requirement_conflicts (id, project_id, kind, older_requirement_id, newer_requirement_id,
                   older_key, newer_key, older_text, newer_text, summary, status, detected_at, last_seen_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (c.id, project_id, c.kind, c.older.id, c.newer.id, c.older.key, c.newer.key, c.older_text,
                 c.newer_text, c.summary, CONFLICT_OPEN, now, now),
            )
        elif status == CONFLICT_RESOLVED:
            reopened += 1
            conn.execute(
                """UPDATE requirement_conflicts SET status = ?, decision = NULL, guidance = '', job_id = NULL,
                   decided_at = NULL, resolved_at = NULL, detected_at = ?, last_seen_at = ? WHERE id = ?""",
                (CONFLICT_OPEN, now, now, c.id),
            )
        else:
            conn.execute("UPDATE requirement_conflicts SET last_seen_at = ? WHERE id = ?", (now, c.id))
    gone = [cid for cid, status in known.items() if status != CONFLICT_RESOLVED and cid not in {c.id for c in found}]
    # A semantic conflict is resolved once either requirement was deleted, superseded or edited after it was recorded.
    gone += [row["id"] for row in conn.execute(
        """SELECT c.id FROM requirement_conflicts c
           LEFT JOIN requirements o ON o.id = c.older_requirement_id LEFT JOIN requirements n ON n.id = c.newer_requirement_id
           WHERE c.project_id = ? AND c.kind = 'semantic' AND c.status != ?
             AND (o.id IS NULL OR n.id IS NULL OR o.status = 'superseded' OR n.status = 'superseded'
                  OR o.updated_at > c.last_seen_at OR n.updated_at > c.last_seen_at)""",
        (project_id, CONFLICT_RESOLVED))]
    conn.executemany(
        "UPDATE requirement_conflicts SET status = ?, resolved_at = ? WHERE id = ?",
        [(CONFLICT_RESOLVED, now, cid) for cid in gone],
    )
    conn.commit()
    return {"found": len(found), "new": new, "reopened": reopened, "resolved": len(gone)}


def record_semantic_conflict(conn: sqlite3.Connection, project_id: str, suspect: dict) -> dict:
    """Record a pair Claude's semantic check suspected (REQ-000000523) as an `open` conflict of kind `semantic`;
    recording the same pair and texts again reopens it rather than adding another."""
    digest = hashlib.sha256("\x1f".join((
        "semantic", suspect["older_requirement_id"], suspect["newer_requirement_id"], suspect["older_text"], suspect["newer_text"],
    )).encode()).hexdigest()[:32]
    now = _iso(datetime.now(timezone.utc))
    summary = suspect["summary"] + (f" ({suspect['reason']})" if suspect.get("reason") else "")
    conn.execute(
        """INSERT INTO requirement_conflicts (id, project_id, kind, older_requirement_id, newer_requirement_id,
           older_key, newer_key, older_text, newer_text, summary, status, detected_at, last_seen_at)
           VALUES (?, ?, 'semantic', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET status = excluded.status, decision = NULL, guidance = '', job_id = NULL,
           decided_at = NULL, resolved_at = NULL, last_seen_at = excluded.last_seen_at""",
        (digest, project_id, suspect["older_requirement_id"], suspect["newer_requirement_id"], suspect["older_key"],
         suspect["newer_key"], suspect["older_text"], suspect["newer_text"], summary, CONFLICT_OPEN, now, now),
    )
    conn.commit()
    return get_conflict(conn, digest)


def list_conflicts(conn: sqlite3.Connection, *, project_id: str | None = None, status: str | None = None) -> list[dict]:
    query, params = "SELECT * FROM requirement_conflicts WHERE 1=1", []
    if project_id is not None:
        query += " AND project_id = ?"
        params.append(project_id)
    if status is not None:
        query += " AND status = ?"
        params.append(status)
    rows = conn.execute(query + " ORDER BY detected_at, id", params).fetchall()
    return [dict(row) for row in rows]


def get_conflict(conn: sqlite3.Connection, conflict_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM requirement_conflicts WHERE id = ?", (conflict_id,)).fetchone()
    return dict(row) if row else None


def decide_conflict(conn: sqlite3.Connection, conflict_id: str, *, decision: str, guidance: str, job_id: str | None) -> None:
    """The user chose how to resolve a conflict and a Claude task is fixing it."""
    conn.execute(
        "UPDATE requirement_conflicts SET status = ?, decision = ?, guidance = ?, job_id = ?, decided_at = ? WHERE id = ?",
        (CONFLICT_RESOLVING, decision, guidance, job_id, _iso(datetime.now(timezone.utc)), conflict_id),
    )
    conn.commit()


def conflicts_version(conn: sqlite3.Connection) -> str:
    """Changes whenever a conflict is found, decided or resolved: a watcher's one cheap read."""
    row = conn.execute(
        """SELECT COUNT(*), COALESCE(SUM(status = 'open'), 0), COALESCE(MAX(detected_at), ''),
           COALESCE(MAX(decided_at), ''), COALESCE(MAX(resolved_at), '') FROM requirement_conflicts"""
    ).fetchone()
    return ":".join(str(value) for value in tuple(row))
