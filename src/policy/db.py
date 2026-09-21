"""Policy & Compliance's own SQLite file — `policies`, `policy_projects`
and `policy_check_results` (docs/pillars/policy_compliance.md's Data
model). WAL mode, one file, opened only by this pillar.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from policy.models import CheckOutcome, CreatedBy, GatePoint, Policy, PolicyCheckResult, PolicyProject, ScopeLevel

SCHEMA = """
CREATE TABLE IF NOT EXISTS policies (
    id TEXT PRIMARY KEY,
    topic TEXT NOT NULL,
    target_pillar TEXT NOT NULL,
    ears_text TEXT NOT NULL,
    test_script TEXT NOT NULL,
    scope_level TEXT NOT NULL,
    scope_target TEXT,
    created_by TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    gate_points TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    amended_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS policy_check_results (
    id TEXT PRIMARY KEY,
    policy_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    outcome TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    job_id TEXT,
    checked_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS policy_projects (
    policy_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    gating_enabled INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (policy_id, project_id)
);

-- A policy whose project selection has been initialised (seeded once from
-- its authoring scope, or set explicitly) — so deselecting every project is
-- a real choice that a restart never undoes.
CREATE TABLE IF NOT EXISTS policy_seeded (
    policy_id TEXT PRIMARY KEY
);
"""


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    # Waivers were replaced by explicit project selection; drop the legacy
    # table only when it holds nothing, never discarding a recorded exception.
    legacy = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='policy_waivers'").fetchone()
    if legacy and conn.execute("SELECT COUNT(*) FROM policy_waivers").fetchone()[0] == 0:
        conn.execute("DROP TABLE policy_waivers")
    conn.commit()
    return conn


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)


def new_id() -> str:
    return uuid.uuid4().hex


# -- policies ----------------------------------------------------------------


def insert_policy(conn: sqlite3.Connection, policy: Policy) -> None:
    conn.execute(
        """INSERT INTO policies
           (id, topic, target_pillar, ears_text, test_script, scope_level, scope_target, created_by,
            enabled, gate_points, created_at, amended_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            policy.id, policy.topic, policy.target_pillar, policy.ears_text, policy.test_script,
            policy.scope_level.value, policy.scope_target, policy.created_by.value,
            int(policy.enabled), json.dumps([g.value for g in policy.gate_points]),
            _iso(policy.created_at), _iso(policy.amended_at),
        ),
    )
    conn.commit()


def replace_policy(conn: sqlite3.Connection, policy: Policy) -> None:
    """An edit: "replace this policy's EARS text/script" — same id,
    fresh `amended_at`, everything else about the row overwritten."""
    policy.amended_at = datetime.now(timezone.utc)
    conn.execute(
        """UPDATE policies SET topic = ?, target_pillar = ?, ears_text = ?, test_script = ?,
           scope_level = ?, scope_target = ?, created_by = ?, enabled = ?, gate_points = ?, amended_at = ? WHERE id = ?""",
        (
            policy.topic, policy.target_pillar, policy.ears_text, policy.test_script,
            policy.scope_level.value, policy.scope_target, policy.created_by.value,
            int(policy.enabled), json.dumps([g.value for g in policy.gate_points]),
            _iso(policy.amended_at), policy.id,
        ),
    )
    conn.commit()


def set_policy_configuration(
    conn: sqlite3.Connection, policy_id: str, *, enabled: bool | None = None, gate_points: list[GatePoint] | None = None,
) -> None:
    """Toggling `enabled` or choosing `gate_points` never touches
    `ears_text`/`test_script`/`topic` and never re-runs any gate — the
    EARS-compliance/enforceability/conflict checks are about the
    requirement's *meaning*, which this doesn't change."""
    policy = get_policy(conn, policy_id)
    if policy is None:
        return
    if enabled is not None:
        policy.enabled = enabled
    if gate_points is not None:
        policy.gate_points = gate_points
    policy.amended_at = datetime.now(timezone.utc)
    conn.execute(
        "UPDATE policies SET enabled = ?, gate_points = ?, amended_at = ? WHERE id = ?",
        (int(policy.enabled), json.dumps([g.value for g in policy.gate_points]), _iso(policy.amended_at), policy_id),
    )
    conn.commit()


def _projects_of(conn: sqlite3.Connection, policy_id: str) -> list[PolicyProject]:
    rows = conn.execute(
        "SELECT project_id, gating_enabled FROM policy_projects WHERE policy_id = ? ORDER BY project_id", (policy_id,)
    ).fetchall()
    return [PolicyProject(project_id=r["project_id"], gating_enabled=bool(r["gating_enabled"])) for r in rows]


def _row_to_policy(conn: sqlite3.Connection, row: sqlite3.Row) -> Policy:
    return Policy(
        id=row["id"], topic=row["topic"], target_pillar=row["target_pillar"], ears_text=row["ears_text"],
        test_script=row["test_script"], scope_level=ScopeLevel(row["scope_level"]), scope_target=row["scope_target"],
        created_by=CreatedBy(row["created_by"]), enabled=bool(row["enabled"]),
        gate_points=[GatePoint(g) for g in json.loads(row["gate_points"])],
        projects=_projects_of(conn, row["id"]),
        created_at=_parse_iso(row["created_at"]), amended_at=_parse_iso(row["amended_at"]),
    )


def get_policy(conn: sqlite3.Connection, policy_id: str) -> Policy | None:
    row = conn.execute("SELECT * FROM policies WHERE id = ?", (policy_id,)).fetchone()
    return _row_to_policy(conn, row) if row else None


def list_policies(conn: sqlite3.Connection, *, topic: str | None = None) -> list[Policy]:
    if topic is None:
        rows = conn.execute("SELECT * FROM policies ORDER BY created_at").fetchall()
    else:
        rows = conn.execute("SELECT * FROM policies WHERE topic = ? ORDER BY created_at", (topic,)).fetchall()
    return [_row_to_policy(conn, row) for row in rows]


def find_by_scope(
    conn: sqlite3.Connection, *, topic: str, scope_level: ScopeLevel, scope_target: str | None,
    exclude_id: str | None = None,
) -> Policy | None:
    """Gate 3a's own structural check: an exact (topic, scope_level,
    scope_target) match — never a fuzzy one."""
    query = "SELECT * FROM policies WHERE topic = ? AND scope_level = ? AND (scope_target IS ? OR scope_target = ?)"
    params: list = [topic, scope_level.value, scope_target, scope_target]
    if exclude_id is not None:
        query += " AND id != ?"
        params.append(exclude_id)
    row = conn.execute(query, params).fetchone()
    return _row_to_policy(conn, row) if row else None


def find_topic_owner_conflict(
    conn: sqlite3.Connection, *, topic: str, target_pillar: str, exclude_id: str | None = None
) -> Policy | None:
    """Gate 3a2's own ownership check: does this topic already exist
    anywhere, targeting a *different* pillar?"""
    query = "SELECT * FROM policies WHERE topic = ? AND target_pillar != ?"
    params: list = [topic, target_pillar]
    if exclude_id is not None:
        query += " AND id != ?"
        params.append(exclude_id)
    row = conn.execute(query, params).fetchone()
    return _row_to_policy(conn, row) if row else None


def delete_policy(conn: sqlite3.Connection, policy_id: str) -> None:
    conn.execute("DELETE FROM policies WHERE id = ?", (policy_id,))
    conn.execute("DELETE FROM policy_check_results WHERE policy_id = ?", (policy_id,))
    conn.execute("DELETE FROM policy_projects WHERE policy_id = ?", (policy_id,))
    conn.execute("DELETE FROM policy_seeded WHERE policy_id = ?", (policy_id,))
    conn.commit()


# -- policy projects: the explicit selection a policy applies to ---------------


def set_policy_projects(conn: sqlite3.Connection, policy_id: str, projects: list[PolicyProject]) -> None:
    """Replace the whole selection — a project left out is deselected."""
    conn.execute("DELETE FROM policy_projects WHERE policy_id = ?", (policy_id,))
    for project in projects:
        conn.execute(
            "INSERT OR REPLACE INTO policy_projects (policy_id, project_id, gating_enabled) VALUES (?, ?, ?)",
            (policy_id, project.project_id, int(project.gating_enabled)),
        )
    conn.execute("INSERT OR IGNORE INTO policy_seeded (policy_id) VALUES (?)", (policy_id,))
    conn.commit()


def find_policy_project(conn: sqlite3.Connection, *, policy_id: str, project_id: str) -> PolicyProject | None:
    row = conn.execute(
        "SELECT project_id, gating_enabled FROM policy_projects WHERE policy_id = ? AND project_id = ?",
        (policy_id, project_id),
    ).fetchone()
    return PolicyProject(project_id=row["project_id"], gating_enabled=bool(row["gating_enabled"])) if row else None


def list_policy_projects(
    conn: sqlite3.Connection, *, policy_id: str | None = None, project_id: str | None = None
) -> list[dict]:
    query = "SELECT policy_id, project_id, gating_enabled FROM policy_projects WHERE 1=1"
    params: list[str] = []
    if policy_id is not None:
        query += " AND policy_id = ?"
        params.append(policy_id)
    if project_id is not None:
        query += " AND project_id = ?"
        params.append(project_id)
    query += " ORDER BY policy_id, project_id"
    return [
        {"policy_id": r["policy_id"], "project_id": r["project_id"], "gating_enabled": bool(r["gating_enabled"])}
        for r in conn.execute(query, params).fetchall()
    ]


def unseeded_policies(conn: sqlite3.Connection) -> list[Policy]:
    rows = conn.execute(
        "SELECT * FROM policies WHERE id NOT IN (SELECT policy_id FROM policy_seeded) ORDER BY created_at"
    ).fetchall()
    return [_row_to_policy(conn, row) for row in rows]


# -- policy check results -----------------------------------------------------


def insert_check_result(conn: sqlite3.Connection, result: PolicyCheckResult) -> None:
    conn.execute(
        """INSERT INTO policy_check_results (id, policy_id, project_id, outcome, detail, job_id, checked_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            result.id, result.policy_id, result.project_id, result.outcome.value, result.detail,
            result.job_id, _iso(result.checked_at),
        ),
    )
    conn.commit()


def _row_to_result(row: sqlite3.Row) -> PolicyCheckResult:
    return PolicyCheckResult(
        id=row["id"], policy_id=row["policy_id"], project_id=row["project_id"],
        outcome=CheckOutcome(row["outcome"]), detail=row["detail"], job_id=row["job_id"],
        checked_at=_parse_iso(row["checked_at"]),
    )


def list_check_results(
    conn: sqlite3.Connection, *, policy_id: str | None = None, project_id: str | None = None
) -> list[PolicyCheckResult]:
    query = "SELECT * FROM policy_check_results WHERE 1=1"
    params: list[str] = []
    if policy_id is not None:
        query += " AND policy_id = ?"
        params.append(policy_id)
    if project_id is not None:
        query += " AND project_id = ?"
        params.append(project_id)
    query += " ORDER BY checked_at"
    rows = conn.execute(query, params).fetchall()
    return [_row_to_result(row) for row in rows]


def latest_check_result(conn: sqlite3.Connection, *, policy_id: str, project_id: str) -> PolicyCheckResult | None:
    row = conn.execute(
        "SELECT * FROM policy_check_results WHERE policy_id = ? AND project_id = ? ORDER BY checked_at DESC LIMIT 1",
        (policy_id, project_id),
    ).fetchone()
    return _row_to_result(row) if row else None
