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

from policy.models import (
    CheckOutcome, CreatedBy, DraftStatus, GatePoint, Policy, PolicyCheckResult, PolicyDraft, PolicyProject, ScopeLevel,
)

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
    checked_at TEXT NOT NULL,
    checked_state TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS policy_projects (
    policy_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    gating_enabled INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (policy_id, project_id)
);

CREATE TABLE IF NOT EXISTS policy_drafts (
    id TEXT PRIMARY KEY,
    topic TEXT NOT NULL,
    target_pillar TEXT NOT NULL,
    ears_text TEXT NOT NULL,
    ears_refined INTEGER NOT NULL DEFAULT 0,
    test_script TEXT NOT NULL,
    scope_level TEXT NOT NULL,
    scope_target TEXT,
    created_by TEXT NOT NULL,
    gate_points TEXT NOT NULL DEFAULT '[]',
    source TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    concerns INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending',
    policy_id TEXT,
    reject_reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    decided_at TEXT
);

-- The user's policy bypass (policy/bypass.py): at most one row, TRUE while expires_at is in the future,
-- and the audit log of turning it on, off, lapsing and of each violation it let through.
CREATE TABLE IF NOT EXISTS policy_bypass (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    expires_at TEXT NOT NULL,
    enabled_by TEXT NOT NULL,
    enabled_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS policy_bypass_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    action TEXT NOT NULL,
    actor TEXT NOT NULL,
    origin TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT ''
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
    # A draft's own recommended project selection, added after policy_drafts already
    # shipped — an existing database needs the column added, not just the CREATE TABLE
    # IF NOT EXISTS above, which only helps a brand-new file.
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(policy_drafts)")}
    if "projects" not in columns:
        conn.execute("ALTER TABLE policy_drafts ADD COLUMN projects TEXT NOT NULL DEFAULT '[]'")
    # Change-driven check skipping (see PolicyCheckResult.checked_state) —
    # added after policy_check_results already shipped.
    result_columns = {row["name"] for row in conn.execute("PRAGMA table_info(policy_check_results)")}
    if "checked_state" not in result_columns:
        conn.execute("ALTER TABLE policy_check_results ADD COLUMN checked_state TEXT NOT NULL DEFAULT ''")
    if "origin" not in result_columns:
        conn.execute("ALTER TABLE policy_check_results ADD COLUMN origin TEXT NOT NULL DEFAULT 'unknown'")
        conn.execute("ALTER TABLE policy_check_results ADD COLUMN bypassed INTEGER NOT NULL DEFAULT 0")
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
        """INSERT INTO policy_check_results (id, policy_id, project_id, outcome, detail, job_id, checked_at, checked_state, origin, bypassed)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            result.id, result.policy_id, result.project_id, result.outcome.value, result.detail,
            result.job_id, _iso(result.checked_at), result.checked_state, result.origin, int(result.bypassed),
        ),
    )
    conn.commit()


def _row_to_result(row: sqlite3.Row) -> PolicyCheckResult:
    return PolicyCheckResult(
        id=row["id"], policy_id=row["policy_id"], project_id=row["project_id"],
        outcome=CheckOutcome(row["outcome"]), detail=row["detail"], job_id=row["job_id"],
        checked_at=_parse_iso(row["checked_at"]), checked_state=row["checked_state"] if "checked_state" in row.keys() else "",
        origin=row["origin"], bypassed=bool(row["bypassed"]),
    )


def list_check_results(
    conn: sqlite3.Connection, *, policy_id: str | None = None, project_id: str | None = None,
    checked_since: datetime | None = None, latest: bool = False, outcome: str | None = None,
) -> list[PolicyCheckResult]:
    """Check results, oldest first: of a policy, a project, those recorded since `checked_since`. `latest`
    keeps only the newest result of each policy and project, and `outcome` then keeps those with it (a
    policy failing now, not one that failed once) -- REQ-000000390."""
    where: list[str] = []
    params: list[str] = []
    for column, value in (("policy_id", policy_id), ("project_id", project_id)):
        if value is not None:
            where.append(f"{column} = ?")
            params.append(value)
    if checked_since is not None:
        where.append("checked_at >= ?")
        params.append(_iso(checked_since))
    clause = f" WHERE {' AND '.join(where)}" if where else ""
    if latest:
        query = ("SELECT * FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY policy_id, project_id ORDER BY checked_at DESC, rowid DESC)"
                 f" AS newest FROM policy_check_results{clause}) WHERE newest = 1")
    else:
        query = f"SELECT * FROM policy_check_results{clause}"
    if outcome is not None:
        query += f" {'AND' if latest or where else 'WHERE'} outcome = ?"
        params.append(outcome)
    rows = conn.execute(query + " ORDER BY checked_at", params).fetchall()
    return [_row_to_result(row) for row in rows]


def latest_check_result(conn: sqlite3.Connection, *, policy_id: str, project_id: str) -> PolicyCheckResult | None:
    row = conn.execute(
        "SELECT * FROM policy_check_results WHERE policy_id = ? AND project_id = ? ORDER BY checked_at DESC LIMIT 1",
        (policy_id, project_id),
    ).fetchone()
    return _row_to_result(row) if row else None


# -- policy drafts: awaiting a human's Gate 4 in the Dashboard ---------------


def _row_to_draft(row: sqlite3.Row) -> PolicyDraft:
    return PolicyDraft(
        id=row["id"], topic=row["topic"], target_pillar=row["target_pillar"], ears_text=row["ears_text"],
        ears_refined=bool(row["ears_refined"]), test_script=row["test_script"],
        scope_level=ScopeLevel(row["scope_level"]), scope_target=row["scope_target"],
        created_by=CreatedBy(row["created_by"]), gate_points=[GatePoint(g) for g in json.loads(row["gate_points"])],
        projects=[PolicyProject(**p) for p in json.loads(row["projects"])],
        source=row["source"], notes=row["notes"], concerns=bool(row["concerns"]), status=DraftStatus(row["status"]),
        policy_id=row["policy_id"], reject_reason=row["reject_reason"], created_at=_parse_iso(row["created_at"]),
        decided_at=_parse_iso(row["decided_at"]) if row["decided_at"] else None,
    )


def insert_draft(conn: sqlite3.Connection, draft: PolicyDraft) -> None:
    conn.execute(
        """INSERT INTO policy_drafts
           (id, topic, target_pillar, ears_text, ears_refined, test_script, scope_level, scope_target, created_by,
            gate_points, projects, source, notes, concerns, status, policy_id, reject_reason, created_at, decided_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            draft.id, draft.topic, draft.target_pillar, draft.ears_text, int(draft.ears_refined), draft.test_script,
            draft.scope_level.value, draft.scope_target, draft.created_by.value,
            json.dumps([g.value for g in draft.gate_points]), json.dumps([p.model_dump(mode="json") for p in draft.projects]),
            draft.source, draft.notes, int(draft.concerns),
            draft.status.value, draft.policy_id, draft.reject_reason, _iso(draft.created_at),
            _iso(draft.decided_at) if draft.decided_at else None,
        ),
    )
    conn.commit()


def get_draft(conn: sqlite3.Connection, draft_id: str) -> PolicyDraft | None:
    row = conn.execute("SELECT * FROM policy_drafts WHERE id = ?", (draft_id,)).fetchone()
    return _row_to_draft(row) if row else None


def list_drafts(conn: sqlite3.Connection, *, status: DraftStatus | None = None) -> list[PolicyDraft]:
    if status is None:
        rows = conn.execute("SELECT * FROM policy_drafts ORDER BY created_at, topic").fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM policy_drafts WHERE status = ? ORDER BY created_at, topic", (status.value,)
        ).fetchall()
    return [_row_to_draft(row) for row in rows]


def find_open_draft_by_topic(conn: sqlite3.Connection, topic: str) -> PolicyDraft | None:
    """A draft still awaiting a decision (pending) for this topic — what a
    re-import replaces instead of duplicating."""
    row = conn.execute(
        "SELECT * FROM policy_drafts WHERE topic = ? AND status = 'pending' ORDER BY created_at LIMIT 1", (topic,)
    ).fetchone()
    return _row_to_draft(row) if row else None


def replace_draft_content(conn: sqlite3.Connection, draft: PolicyDraft) -> None:
    """Refresh a pending draft's content (a re-import) without touching its status or id."""
    conn.execute(
        """UPDATE policy_drafts SET target_pillar = ?, ears_text = ?, ears_refined = ?, test_script = ?, scope_level = ?,
           scope_target = ?, created_by = ?, gate_points = ?, projects = ?, source = ?, notes = ?, concerns = ? WHERE id = ?""",
        (
            draft.target_pillar, draft.ears_text, int(draft.ears_refined), draft.test_script, draft.scope_level.value,
            draft.scope_target, draft.created_by.value, json.dumps([g.value for g in draft.gate_points]),
            json.dumps([p.model_dump(mode="json") for p in draft.projects]),
            draft.source, draft.notes, int(draft.concerns), draft.id,
        ),
    )
    conn.commit()


def set_draft_decision(
    conn: sqlite3.Connection, draft_id: str, *, status: DraftStatus, policy_id: str | None = None, reason: str = "",
) -> None:
    decided = None if status == DraftStatus.PENDING else _iso(datetime.now(timezone.utc))
    conn.execute(
        "UPDATE policy_drafts SET status = ?, policy_id = ?, reject_reason = ?, decided_at = ? WHERE id = ?",
        (status.value, policy_id, reason, decided, draft_id),
    )
    conn.commit()
