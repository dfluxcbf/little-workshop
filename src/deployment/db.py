"""Deployment & Servers' own SQLite file — `servers`, `deployments`,
`deploy_confirmations` (docs/architecture.md's Data model). Replaces
`little-runner`'s loose per-server JSON files (`deploy_state.py`,
`deploy_confirm.py`) with a pillar-owned database, same as every other
pillar's operational data.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS servers (
    project_id TEXT PRIMARY KEY,
    branch TEXT NOT NULL,
    version TEXT NOT NULL,
    commit_sha TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS deployments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    branch TEXT NOT NULL,
    commit_sha TEXT NOT NULL,
    version TEXT NOT NULL,
    outcome TEXT NOT NULL,
    detail TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pillar_deployments (
    pillar TEXT PRIMARY KEY,
    install_dir TEXT NOT NULL,
    commit_sha TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS deploy_confirmations (
    project_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    deploy_id TEXT NOT NULL DEFAULT '',
    deadline TEXT,
    confirmed_branch TEXT NOT NULL DEFAULT '?',
    confirmed_commit TEXT NOT NULL DEFAULT '?',
    confirmed_version TEXT NOT NULL DEFAULT '?',
    pending_branch TEXT NOT NULL DEFAULT '',
    pending_commit TEXT NOT NULL DEFAULT '',
    pending_version TEXT NOT NULL DEFAULT ''
);
"""

PENDING = "pending"
CONFIRMED = "confirmed"


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# -- servers (current deployed-state cache) ------------------------------


def save_server_state(conn: sqlite3.Connection, project_id: str, branch: str, version: str, commit: str) -> None:
    conn.execute(
        """INSERT INTO servers (project_id, branch, version, commit_sha, updated_at) VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(project_id) DO UPDATE SET branch=excluded.branch, version=excluded.version,
           commit_sha=excluded.commit_sha, updated_at=excluded.updated_at""",
        (project_id, branch, version, commit, _now()),
    )
    conn.commit()


def get_server_state(conn: sqlite3.Connection, project_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM servers WHERE project_id = ?", (project_id,)).fetchone()
    return dict(row) if row else None


def list_server_states(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT * FROM servers ORDER BY project_id").fetchall()
    return [dict(row) for row in rows]


# -- pillar_deployments (M5: a pillar's own stable code location) -------


def save_pillar_deployment(conn: sqlite3.Connection, pillar: str, install_dir: str, commit: str) -> None:
    conn.execute(
        """INSERT INTO pillar_deployments (pillar, install_dir, commit_sha, updated_at) VALUES (?, ?, ?, ?)
           ON CONFLICT(pillar) DO UPDATE SET install_dir=excluded.install_dir, commit_sha=excluded.commit_sha,
           updated_at=excluded.updated_at""",
        (pillar, install_dir, commit, _now()),
    )
    conn.commit()


def get_pillar_deployment(conn: sqlite3.Connection, pillar: str) -> dict | None:
    row = conn.execute("SELECT * FROM pillar_deployments WHERE pillar = ?", (pillar,)).fetchone()
    return dict(row) if row else None


# -- deployments (audit trail) -------------------------------------------


def record_deployment(
    conn: sqlite3.Connection, project_id: str, branch: str, commit: str, version: str, outcome: str, detail: str | None
) -> None:
    conn.execute(
        """INSERT INTO deployments (project_id, branch, commit_sha, version, outcome, detail, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (project_id, branch, commit, version, outcome, detail, _now()),
    )
    conn.commit()


def list_deployments(conn: sqlite3.Connection, project_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM deployments WHERE project_id = ? ORDER BY created_at DESC", (project_id,)
    ).fetchall()
    return [dict(row) for row in rows]


# -- deploy_confirmations (the confirm-gate state machine) ----------------


def seed_confirmation(conn: sqlite3.Connection, project_id: str, branch: str, commit: str, version: str) -> None:
    """Establish (or reset) the confirmed baseline directly, nothing
    pending — used once when a server first opts into confirm-gating."""
    conn.execute(
        """INSERT INTO deploy_confirmations (project_id, status, confirmed_branch, confirmed_commit, confirmed_version)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(project_id) DO UPDATE SET status=excluded.status, confirmed_branch=excluded.confirmed_branch,
           confirmed_commit=excluded.confirmed_commit, confirmed_version=excluded.confirmed_version""",
        (project_id, CONFIRMED, branch, commit, version),
    )
    conn.commit()


def mark_pending(
    conn: sqlite3.Connection, project_id: str, branch: str, commit: str, version: str, timeout_seconds: float
) -> dict:
    """A second deploy before the first pending one was confirmed simply
    supersedes it — the new deploy_id is what a later rollback watchdog
    must match."""
    existing = get_confirmation(conn, project_id)
    deploy_id = uuid.uuid4().hex
    deadline = datetime.now(timezone.utc).timestamp() + timeout_seconds
    if existing is None:
        conn.execute(
            """INSERT INTO deploy_confirmations
               (project_id, status, deploy_id, deadline, pending_branch, pending_commit, pending_version)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (project_id, PENDING, deploy_id, str(deadline), branch, commit, version),
        )
    else:
        conn.execute(
            """UPDATE deploy_confirmations SET status=?, deploy_id=?, deadline=?,
               pending_branch=?, pending_commit=?, pending_version=? WHERE project_id=?""",
            (PENDING, deploy_id, str(deadline), branch, commit, version, project_id),
        )
    conn.commit()
    return get_confirmation(conn, project_id)


def get_confirmation(conn: sqlite3.Connection, project_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM deploy_confirmations WHERE project_id = ?", (project_id,)).fetchone()
    return dict(row) if row else None


def confirm(conn: sqlite3.Connection, project_id: str) -> bool:
    """Promote the pending deploy to confirmed. False (no-op) if nothing
    is pending."""
    state = get_confirmation(conn, project_id)
    if state is None or state["status"] != PENDING:
        return False
    conn.execute(
        """UPDATE deploy_confirmations SET status=?, confirmed_branch=?, confirmed_commit=?, confirmed_version=?,
           pending_branch='', pending_commit='', pending_version='', deploy_id='', deadline=NULL
           WHERE project_id=?""",
        (CONFIRMED, state["pending_branch"], state["pending_commit"], state["pending_version"], project_id),
    )
    conn.commit()
    return True


def settle_after_rollback(conn: sqlite3.Connection, project_id: str) -> None:
    """Called only after the redeploy of the confirmed state has actually
    succeeded — clears pending_* and marks confirmed again."""
    conn.execute(
        """UPDATE deploy_confirmations SET status=?, pending_branch='', pending_commit='', pending_version='',
           deploy_id='', deadline=NULL WHERE project_id=?""",
        (CONFIRMED, project_id),
    )
    conn.commit()


def list_pending_confirmations(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT * FROM deploy_confirmations WHERE status = ?", (PENDING,)).fetchall()
    return [dict(row) for row in rows]
