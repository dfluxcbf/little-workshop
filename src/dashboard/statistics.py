"""Per-day statistics of little-workshop's operations, for the Statistics page.

The plots outlive what they are built from: Orchestration's job cleanup
deletes old Jobs, so the counts are kept apart from the pillars, in one small
SQLite file under the Dashboard's own state directory. The Dashboard updates
them by itself (on a visit of the page, every few minutes while it runs, and
before it deletes old Jobs), so nobody has to ask. An update reads the
incidents the pillars hold, and the Jobs changed and policy check results
recorded since the last update (Jobs as summaries; all of them once a day,
which is when what was deleted since is forgotten), and adds the ones not
counted yet to their day; `seen` remembers what was counted, so updating twice counts
nothing twice, and a day whose Jobs were since deleted keeps its numbers.

Each table is one plot's data, one row per day (the local date the thing
happened on):

- `job_days`: per job type, how often it was triggered, how many runs
  succeeded and failed, and the seconds those finished runs took. All three
  counts are kept, since triggered and succeeded + failed can diverge (a
  cancelled Job, one still running).
- `policy_days`: per policy, the checks that passed, failed, or did not apply.
- `incident_days`: the incidents raised.
- `test_days`: the test executions (one Job running many tests) that
  succeeded and failed.

Every row also says where it belongs, as two separate columns the page can
filter by, one or the other: `project` (the project it is about, '' when it
has none) and `pillar` (the pillar that did it).
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path

from common.paths import PILLARS, Paths

SETTLED_STATUSES = ("succeeded", "failed", "cancelled")
TEST_JOB_TYPES = ("requirements.run_tests", "requirements.import_junit")
FAILING_VERDICTS = ("failed", "error")
PILLAR_IDS = (*PILLARS, "core")
TABLES = ("seen", "job_days", "policy_days", "incident_days", "test_days", "meta")

SCHEMA = """
CREATE TABLE IF NOT EXISTS seen (
    kind TEXT NOT NULL,
    id TEXT NOT NULL,
    settled INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (kind, id)
);
CREATE TABLE IF NOT EXISTS job_days (
    day TEXT NOT NULL,
    job TEXT NOT NULL,
    project TEXT NOT NULL,
    pillar TEXT NOT NULL,
    triggered INTEGER NOT NULL DEFAULT 0,
    succeeded INTEGER NOT NULL DEFAULT 0,
    failed INTEGER NOT NULL DEFAULT 0,
    duration_seconds REAL NOT NULL DEFAULT 0,
    duration_count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, job, project, pillar)
);
CREATE TABLE IF NOT EXISTS policy_days (
    day TEXT NOT NULL,
    policy TEXT NOT NULL,
    project TEXT NOT NULL,
    pillar TEXT NOT NULL,
    passed INTEGER NOT NULL DEFAULT 0,
    failed INTEGER NOT NULL DEFAULT 0,
    not_applicable INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, policy, project, pillar)
);
CREATE TABLE IF NOT EXISTS incident_days (
    day TEXT NOT NULL,
    project TEXT NOT NULL,
    pillar TEXT NOT NULL,
    incidents INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, project, pillar)
);
CREATE TABLE IF NOT EXISTS test_days (
    day TEXT NOT NULL,
    project TEXT NOT NULL,
    pillar TEXT NOT NULL,
    succeeded INTEGER NOT NULL DEFAULT 0,
    failed INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, project, pillar)
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

_lock = threading.Lock()


def _file(paths: Paths) -> Path:
    return paths.state_home / "dashboard" / "statistics.db"


def _connect(paths: Paths) -> sqlite3.Connection:
    target = _file(paths)
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target)
    conn.row_factory = sqlite3.Row
    # A store from before rows carried a project and a pillar apart cannot be converted
    # (it kept one or the other): start over, and the next update recounts what the pillars hold.
    if "scope" in [column["name"] for column in conn.execute("PRAGMA table_info(job_days)")]:
        conn.executescript("".join(f"DROP TABLE IF EXISTS {table};" for table in TABLES))
    conn.executescript(SCHEMA)
    return conn


def _moment(value: str | None) -> datetime | None:
    try:
        moment = datetime.fromisoformat((value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _day(value: str | None) -> str | None:
    """The local date of a pillar's (UTC) timestamp, as YYYY-MM-DD."""
    moment = _moment(value)
    return moment.astimezone().date().isoformat() if moment else None


def _where(projects: set[str], project_id: str | None, pillar: str | None) -> dict:
    """The two things a row can be filtered by: the project it is about and the pillar that did it.
    Only a registered project is one: a Job can name something else there (a pillar on a pillar
    redeploy, the workspace session, a mistyped id), which is '' here, as is no project at all."""
    return {"project": project_id if project_id in projects else "", "pillar": pillar if pillar in PILLAR_IDS else "unknown"}


def test_execution_failed(job: dict) -> bool:
    """Whether a settled test Job counts as a failed execution: the Job failed, or any test in it did."""
    if job.get("status") != "succeeded":
        return True
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    if isinstance(result.get("verdicts"), dict):  # a run's counts (and the brief of a listed Job keeps them)
        return any(result["verdicts"].get(verdict) for verdict in FAILING_VERDICTS)
    for suite in result.get("suites") or []:
        if any((suite.get("verdicts") or {}).get(verdict) for verdict in FAILING_VERDICTS):
            return True
    return any(row.get("verdict") in FAILING_VERDICTS for row in result.get("results") or [])


def _add(conn: sqlite3.Connection, table: str, key: dict, amounts: dict) -> None:
    columns = [*key, *amounts]
    conn.execute(
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)}) "
        f"ON CONFLICT ({', '.join(key)}) DO UPDATE SET {', '.join(f'{c} = {c} + excluded.{c}' for c in amounts)}",
        [*key.values(), *amounts.values()],
    )


def _seen(conn: sqlite3.Connection, kind: str) -> dict[str, int]:
    return {row["id"]: row["settled"] for row in conn.execute("SELECT id, settled FROM seen WHERE kind = ?", (kind,))}


def _remember(conn: sqlite3.Connection, kind: str, marks: dict[str, int], present: set[str] | None) -> None:
    """Store what was counted, and forget what the pillar no longer holds (it cannot come back); `present`
    None means only part of what it holds was read, which says nothing of the rest."""
    conn.executemany(
        "INSERT INTO seen (kind, id, settled) VALUES (?, ?, ?) ON CONFLICT (kind, id) DO UPDATE SET settled = excluded.settled",
        [(kind, item_id, settled) for item_id, settled in marks.items()],
    )
    if present is None:
        return
    gone = [item_id for item_id in _seen(conn, kind) if item_id not in present]
    conn.executemany("DELETE FROM seen WHERE kind = ? AND id = ?", [(kind, item_id) for item_id in gone])


def _undecided(job: dict) -> bool:
    """Whether a listed Job's summary left out what says if its tests failed (a run from before runs
    answered with their counts kept them only in `results` or `suites`)."""
    result = job.get("result")
    return bool(job.get("summary")) and job.get("status") == "succeeded" and isinstance(result, dict) \
        and "verdicts" not in result and bool({"results", "suites"} & set(result.get("_omitted") or ()))


def _count_jobs(conn: sqlite3.Connection, jobs: list[dict], projects: set[str], *, complete: bool = True,
                full_job: Callable[[str], dict | None] | None = None) -> None:
    """`complete`: `jobs` is every Job Orchestration holds, so the ones not among them were deleted.
    `full_job` reads a Job whose summary cannot say whether its tests failed."""
    seen, marks = _seen(conn, "job"), {}
    for job in jobs:
        day = _day(job.get("created_at"))
        if not day or not job.get("id"):
            continue
        known = seen.get(job["id"])
        settled = job.get("status") in SETTLED_STATUSES
        if known == 1 or (known == 0 and not settled):
            continue
        where = _where(projects, job.get("project_id"), job.get("target_pillar"))
        key = {"day": day, "job": job.get("type") or "unknown", **where}
        amounts = {"triggered": 1} if known is None else {}
        if settled and job["status"] != "cancelled":
            amounts[job["status"]] = 1
            start, end = _moment(job.get("created_at")), _moment(job.get("updated_at"))
            if start and end:
                amounts["duration_seconds"] = max((end - start).total_seconds(), 0.0)
                amounts["duration_count"] = 1
            if job.get("type") in TEST_JOB_TYPES:
                judged = (full_job(job["id"]) if full_job is not None and _undecided(job) else None) or job
                _add(conn, "test_days", {"day": day, **where},
                     {"failed": 1} if test_execution_failed(judged) else {"succeeded": 1})
        if amounts:
            _add(conn, "job_days", key, amounts)
        marks[job["id"]] = int(settled)
    _remember(conn, "job", marks, {job["id"] for job in jobs if job.get("id")} if complete else None)


def _count_policy_results(conn: sqlite3.Connection, results: list[dict], policies: list[dict], projects: set[str], *,
                          complete: bool = True) -> None:
    by_id = {policy["id"]: policy for policy in policies}
    seen, marks = _seen(conn, "policy_result"), {}
    columns = {"pass": "passed", "fail": "failed", "not_applicable": "not_applicable"}
    for result in results:
        day, column = _day(result.get("checked_at")), columns.get(result.get("outcome"))
        if not day or not column or not result.get("id") or result["id"] in seen:
            continue
        # The pillar is the one that ran the check, as for a Job: always Policy & Compliance.
        key = {"day": day, "policy": by_id.get(result.get("policy_id"), {}).get("topic") or result.get("policy_id") or "unknown",
               **_where(projects, result.get("project_id"), "policy")}
        _add(conn, "policy_days", key, {column: 1})
        marks[result["id"]] = 1
    _remember(conn, "policy_result", marks, {result["id"] for result in results if result.get("id")} if complete else None)


def _count_incidents(conn: sqlite3.Connection, incidents: list[dict], projects: set[str]) -> None:
    seen, marks = _seen(conn, "incident"), {}
    for incident in incidents:
        day = _day(incident.get("created_at"))
        if not day or not incident.get("id") or incident["id"] in seen:
            continue
        _add(conn, "incident_days", {"day": day, **_where(projects, incident.get("project_id"), incident.get("pillar"))}, {"incidents": 1})
        marks[incident["id"]] = 1
    _remember(conn, "incident", marks, {incident["id"] for incident in incidents if incident.get("id")})


# Jobs and policy check results are read by time: those changed (Jobs) or recorded (results) since the last
# read, less a margin for a write that landed just as it ran, and all of them once a day, the read that
# forgets what the pillars no longer hold.
READ_MARGIN = timedelta(minutes=10)
READ_ALL_EVERY = timedelta(days=1)
READ_BY_TIME = ("jobs", "policy_results")


def _meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def _set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value", (key, value))


def to_read(paths: Paths, now: datetime | None = None) -> dict[str, datetime | None]:
    """{source: the time from which the next update needs it} for each of READ_BY_TIME, None for one it
    reads all of: the first time, and once every READ_ALL_EVERY."""
    with _lock:
        conn = _connect(paths)
        try:
            marks = {kind: (_moment(_meta(conn, f"{kind}_read_through")), _moment(_meta(conn, f"{kind}_read_all_at")))
                     for kind in READ_BY_TIME}
        finally:
            conn.close()
    now = now or datetime.now(timezone.utc)
    return {kind: None if through is None or everything is None or now - everything >= READ_ALL_EVERY else through - READ_MARGIN
            for kind, (through, everything) in marks.items()}


def update(paths: Paths, *, projects: list[str] | None, jobs: list[dict] | None, policy_results: list[dict] | None,
           policies: list[dict] | None, incidents: list[dict] | None, now: datetime | None = None,
           since: dict[str, datetime | None] | None = None, read_at: datetime | None = None,
           full_job: Callable[[str], dict | None] | None = None) -> None:
    """Add what is not counted yet. A source given as None (its pillar is down) is left as it was; without
    the registered `projects` nothing can be placed, so nothing is counted until they can be read.
    `since` is what `to_read` gave for this read: a source read since a time is not all its pillar holds
    (so nothing of it is forgotten); `read_at` is when the read began."""
    since = since or {}
    stamp_read = (read_at or now or datetime.now(timezone.utc)).isoformat()

    def read_marks(kind: str) -> None:
        _set_meta(conn, f"{kind}_read_through", stamp_read)
        if since.get(kind) is None:
            _set_meta(conn, f"{kind}_read_all_at", stamp_read)

    with _lock:
        conn = _connect(paths)
        try:
            if projects is not None:
                names = set(projects)
                if jobs is not None:
                    _count_jobs(conn, jobs, names, complete=since.get("jobs") is None, full_job=full_job)
                    read_marks("jobs")
                if policy_results is not None:
                    _count_policy_results(conn, policy_results, policies or [], names, complete=since.get("policy_results") is None)
                    read_marks("policy_results")
                if incidents is not None:
                    _count_incidents(conn, incidents, names)
            stamp = (now or datetime.now(timezone.utc)).isoformat()
            conn.execute("INSERT INTO meta (key, value) VALUES ('updated_at', ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value", (stamp,))
            conn.commit()
        finally:
            conn.close()


def load(paths: Paths) -> dict:
    """Everything the page plots: {updated_at, jobs, policies, incidents, tests}, each a list of per-day rows."""
    with _lock:
        conn = _connect(paths)
        try:
            def rows(table: str) -> list[dict]:
                return [dict(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY day")]

            updated = conn.execute("SELECT value FROM meta WHERE key = 'updated_at'").fetchone()
            return {
                "updated_at": updated["value"] if updated else None,
                "jobs": rows("job_days"), "policies": rows("policy_days"),
                "incidents": rows("incident_days"), "tests": rows("test_days"),
            }
        finally:
            conn.close()
