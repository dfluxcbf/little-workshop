"""Whole-project requirement data: what is stored per project, a backup file of one project's data, putting
such a file back (under its own project id, a new one, or another project's), and deleting it all.

A backup is plain JSON (`FORMAT`, `VERSION`) holding the project's use cases, milestones, test suites,
requirements (with the suites that cover each), test results and nothing derived (conflicts are found
again by the next analysis). An import never reuses an id from the file: every row gets a new id, and a
requirement whose key the target project already has is renumbered to the next free key.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone

from pydantic import ValidationError

from requirements import db
from requirements.models import Milestone, Requirement, TestResult, TestSuite, UseCase

FORMAT = "little-workshop-requirements"
VERSION = 1
PROJECT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
_TRAILING_NUMBER = re.compile(r"^(.*?)(\d+)$")


class TransferError(Exception):
    pass


def project_summaries(conn: sqlite3.Connection) -> list[dict]:
    """Every project id with stored requirement data and what is stored for it, most requirements first."""
    projects: dict[str, dict] = {}

    def entry(project_id: str) -> dict:
        return projects.setdefault(project_id, {
            "project_id": project_id, "requirements": 0, "published": 0, "test_suites": 0, "use_cases": 0, "milestones": 0,
            "test_results": 0, "open_conflicts": 0, "updated_at": None,
        })

    for row in conn.execute("SELECT project_id, COUNT(*) AS n, SUM(status = 'published') AS published, MAX(updated_at) AS at "
                            "FROM requirements GROUP BY project_id"):
        item = entry(row["project_id"])
        item["requirements"], item["published"], item["updated_at"] = row["n"], row["published"] or 0, row["at"]
    for table in ("test_suites", "use_cases", "milestones", "test_results"):
        for row in conn.execute(f"SELECT project_id, COUNT(*) AS n FROM {table} GROUP BY project_id"):
            entry(row["project_id"])[table] = row["n"]
    for row in conn.execute("SELECT project_id, COUNT(*) AS n FROM requirement_conflicts WHERE status = 'open' GROUP BY project_id"):
        entry(row["project_id"])["open_conflicts"] = row["n"]
    return sorted(projects.values(), key=lambda p: (-p["requirements"], p["project_id"]))


def export_project(conn: sqlite3.Connection, project_id: str) -> dict:
    requirements = db.list_requirements(conn, project_id=project_id)
    mappings = db.suites_for_requirements(conn, [r.id for r in requirements])
    return {
        "format": FORMAT, "version": VERSION, "project_id": project_id,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "use_cases": [u.model_dump(mode="json") for u in db.list_use_cases(conn, project_id=project_id)],
        "milestones": [m.model_dump(mode="json") for m in db.list_milestones(conn, project_id=project_id)],
        "test_suites": [s.model_dump(mode="json") for s in db.list_test_suites(conn, project_id=project_id)],
        "requirements": [{**r.model_dump(mode="json"), "suite_ids": mappings.get(r.id, [])} for r in requirements],
        "test_results": [t.model_dump(mode="json") for t in db.list_test_results(conn, project_id=project_id)],
    }


def next_free_key(key: str, taken: set[str]) -> str:
    """`key` renumbered so it is not in `taken`: its trailing number (keeping its width) moves past the
    highest taken number of the same prefix; a key without a trailing number gets `-2`, `-3`, ..."""
    match = _TRAILING_NUMBER.match(key)
    if match is None:
        number = 2
        while f"{key}-{number}" in taken:
            number += 1
        return f"{key}-{number}"
    prefix, digits = match.groups()
    highest = max([int(m.group(2)) for k in taken if (m := _TRAILING_NUMBER.match(k)) and m.group(1) == prefix] + [int(digits)])
    return f"{prefix}{str(highest + 1).zfill(len(digits))}"


def import_project(conn: sqlite3.Connection, data: dict, *, project_id: str) -> dict:
    """Put a backup's data into `project_id` (new ids throughout, keys renumbered where taken) and return
    {project_id, imported: {...counts}, renumbered: [{old, new}]}. Nothing is written if the file is invalid."""
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise TransferError("this is not a requirements backup file")
    if data.get("version") != VERSION:
        raise TransferError(f"unsupported backup version {data.get('version')!r} (this build reads version {VERSION})")
    if not PROJECT_ID.match(project_id or ""):
        raise TransferError("the project id must be letters, digits, '.', '_' or '-', starting with a letter or digit")

    try:
        use_cases = [UseCase(**row) for row in data.get("use_cases", [])]
        milestones = [Milestone(**row) for row in data.get("milestones", [])]
        suites = [TestSuite(**row) for row in data.get("test_suites", [])]
        requirement_rows = data.get("requirements", [])
        requirements = [Requirement(**{k: v for k, v in row.items() if k != "suite_ids"}) for row in requirement_rows]
        results = [TestResult(**row) for row in data.get("test_results", [])]
    except (ValidationError, TypeError) as exc:
        raise TransferError(f"the backup file is damaged: {exc}") from exc

    new_ids: dict[str, str] = {}
    for item in (*use_cases, *milestones, *suites, *requirements):
        new_ids[item.id] = db.new_id()

    taken = {r.key for r in db.list_requirements(conn, project_id=project_id)}
    renumbered = []
    for requirement in requirements:
        key = requirement.key
        if key in taken:
            key = next_free_key(key, taken)
            renumbered.append({"old": requirement.key, "new": key})
        taken.add(key)
        requirement.key = key

    with conn:  # one transaction: a failure leaves the target as it was
        conn.executemany(
            "INSERT INTO use_cases (id, project_id, name, description, created_at) VALUES (?, ?, ?, ?, ?)",
            [(new_ids[u.id], project_id, u.name, u.description, db._iso(u.created_at)) for u in use_cases])
        conn.executemany(
            "INSERT INTO milestones (id, project_id, name, description, created_at) VALUES (?, ?, ?, ?, ?)",
            [(new_ids[m.id], project_id, m.name, m.description, db._iso(m.created_at)) for m in milestones])
        conn.executemany(
            "INSERT INTO test_suites (id, project_id, name, description, created_at) VALUES (?, ?, ?, ?, ?)",
            [(new_ids[s.id], project_id, s.name, s.description, db._iso(s.created_at)) for s in suites])
        conn.executemany(
            """INSERT INTO requirements (id, project_id, key, title, description, category, status, use_case_id, milestone_id,
               created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [(new_ids[r.id], project_id, r.key, r.title, r.description, r.category, r.status.value,
              new_ids.get(r.use_case_id) if r.use_case_id else None, new_ids.get(r.milestone_id) if r.milestone_id else None,
              db._iso(r.created_at), db._iso(r.updated_at)) for r in requirements])
        conn.executemany(
            "INSERT OR IGNORE INTO requirement_test_mappings (requirement_id, suite_id) VALUES (?, ?)",
            [(new_ids[r.id], new_ids[suite_id]) for r, row in zip(requirements, requirement_rows)
             for suite_id in row.get("suite_ids", []) if suite_id in new_ids])
        conn.executemany(
            """INSERT INTO test_results (id, project_id, suite_id, test_name, verdict, detail, source, executed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            [(db.new_id(), project_id, new_ids[t.suite_id], t.test_name, t.verdict.value, t.detail, t.source, db._iso(t.executed_at))
             for t in results if t.suite_id in new_ids])
        if requirements:
            db.stamp_conflict_scan_due(conn, project_id)

    return {
        "project_id": project_id, "renumbered": renumbered,
        "imported": {"requirements": len(requirements), "test_suites": len(suites), "use_cases": len(use_cases),
                     "milestones": len(milestones), "test_results": sum(1 for t in results if t.suite_id in new_ids)},
    }


def delete_project(conn: sqlite3.Connection, project_id: str) -> dict:
    """Remove every requirement row stored for a project id. Irreversible; the caller confirms it."""
    ids = [r["id"] for r in conn.execute("SELECT id FROM requirements WHERE project_id = ?", (project_id,))]
    suite_ids = [r["id"] for r in conn.execute("SELECT id FROM test_suites WHERE project_id = ?", (project_id,))]
    with conn:
        conn.executemany("DELETE FROM requirement_test_mappings WHERE requirement_id = ?", [(i,) for i in ids])
        conn.executemany("DELETE FROM requirement_test_mappings WHERE suite_id = ?", [(i,) for i in suite_ids])
        removed = {
            table: conn.execute(f"DELETE FROM {table} WHERE project_id = ?", (project_id,)).rowcount
            for table in ("requirements", "test_suites", "use_cases", "milestones", "test_results", "requirement_conflicts")
        }
        conn.execute("DELETE FROM conflict_scan_due WHERE project_id = ?", (project_id,))  # nothing left to analyze
    return removed
