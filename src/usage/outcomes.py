"""Tokens per outcome: what the work produced for what it cost (docs/pillars/usage.md, "Outcomes").

Spend alone cannot say whether an optimization made the work cheaper or only shorter. Per week and
project this puts the weighted tokens beside three outcomes: agent tasks that ran to their end,
branches that were finished, and requirements that turned to done. Jobs are deleted after a while
and a requirement's history is not kept, so the pillar notes both as it sees them: each finished
branch once, and each day's count of done requirements."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta

from usage import db
from usage.rollup import monday_of

SCHEMA = """
CREATE TABLE IF NOT EXISTS branch_finishes (
    job_id TEXT PRIMARY KEY,
    project TEXT NOT NULL,
    day TEXT NOT NULL,
    job_type TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS requirement_snapshots (
    day TEXT NOT NULL,
    project TEXT NOT NULL,
    done INTEGER NOT NULL,
    total INTEGER NOT NULL,
    PRIMARY KEY (day, project)
);
"""
FINISH_TYPES = ("version.feature_finish", "version.improvement_finish", "version.hotfix_finish", "version.support_finish")


def note_finished_branches(conn: sqlite3.Connection, jobs: list[dict] | None) -> int:
    """Keep each succeeded branch finish Orchestration still holds; one row per Job, however often it is seen."""
    added = 0
    for job in jobs or []:
        if job.get("type") in FINISH_TYPES and job.get("status") == "succeeded" and job.get("project_id"):
            added += conn.execute(
                "INSERT OR IGNORE INTO branch_finishes (job_id, project, day, job_type) VALUES (?, ?, ?, ?)",
                (job["id"], job["project_id"], db.local_day(job.get("updated_at") or db.now_iso()), job["type"])).rowcount
    return added


def note_requirements(conn: sqlite3.Connection, project: str, report: dict | None, day: str) -> None:
    """Today's count of done requirements for a project (the last reading of the day wins)."""
    if not isinstance(report, dict) or "done_count" not in report:
        return
    conn.execute("INSERT OR REPLACE INTO requirement_snapshots (day, project, done, total) VALUES (?, ?, ?, ?)",
                 (day, project, int(report["done_count"]), int(report.get("total_count") or 0)))


def _done_at(conn: sqlite3.Connection, project: str, day: str) -> int | None:
    """The done count as of `day`: the latest snapshot on or before it."""
    row = conn.execute("SELECT done FROM requirement_snapshots WHERE project = ? AND day <= ? ORDER BY day DESC LIMIT 1", (project, day)).fetchone()
    return row["done"] if row else None


def _per(cost: float, count: int) -> float | None:
    return cost / count if count else None


def weekly(conn: sqlite3.Connection, *, weeks: int = 8, project: str | None = None, today: date | None = None) -> list[dict]:
    """One row per week and project with data, newest week first: weighted tokens, the outcomes and the
    weighted tokens per outcome (None where the week had no such outcome)."""
    today = today or date.today()
    first = monday_of(today) - timedelta(weeks=weeks - 1)
    out = []
    for offset in range(weeks):
        start = first + timedelta(weeks=offset)
        end = start + timedelta(days=6)
        bounds = (start.isoformat(), end.isoformat())
        spent = {row["project"]: row["weighted"] or 0.0 for row in conn.execute(
            """SELECT project, SUM(weighted_input + weighted_cache_read + weighted_cache_write + weighted_output) AS weighted
               FROM day_rollups WHERE day BETWEEN ? AND ? GROUP BY project""", bounds)}
        tasks: dict[str, dict] = {}
        for row in conn.execute("SELECT project, data_json FROM run_usage WHERE kind = 'run' AND substr(created_at, 1, 10) BETWEEN ? AND ?", bounds):
            data = json.loads(row["data_json"])
            entry = tasks.setdefault(row["project"], {"runs": 0, "cost": 0.0})
            entry["cost"] += data.get("cost_usd") or 0.0
            entry["runs"] += 0 if data.get("is_error") else 1
        branches = {row["project"]: row["n"] for row in conn.execute(
            "SELECT project, COUNT(*) AS n FROM branch_finishes WHERE day BETWEEN ? AND ? GROUP BY project", bounds)}
        for name in sorted(set(spent) | set(tasks) | set(branches)):
            if project and name != project:
                continue
            before, after = _done_at(conn, name, (start - timedelta(days=1)).isoformat()), _done_at(conn, name, end.isoformat())
            turned = max(after - before, 0) if before is not None and after is not None else None
            task = tasks.get(name, {"runs": 0, "cost": 0.0})
            weighted = spent.get(name, 0.0)
            out.append({
                "week": start.isoformat(), "project": name, "weighted": weighted,
                "tasks_finished": task["runs"], "task_cost": task["cost"], "weighted_per_task": _per(task["cost"], task["runs"]),
                "branches_finished": branches.get(name, 0), "weighted_per_branch": _per(weighted, branches.get(name, 0)),
                "requirements_done": turned, "weighted_per_requirement": _per(weighted, turned or 0),
            })
    return sorted(out, key=lambda row: (row["week"], -row["weighted"]), reverse=False)[::-1]
