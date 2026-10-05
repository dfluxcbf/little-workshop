"""Registry & Portfolio's own SQLite file — `projects`, `project_sets`,
`tickets`, `backlog_entries` (docs/architecture.md's Data model). WAL mode,
one file, opened only by this pillar.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from registry.models import Project, ProjectSet, ProjectStatus, Ticket, TicketStatus

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    name TEXT PRIMARY KEY,
    path TEXT NOT NULL,
    repo_url TEXT,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS project_sets (
    name TEXT PRIMARY KEY,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS project_set_members (
    set_name TEXT NOT NULL,
    project_name TEXT NOT NULL,
    PRIMARY KEY (set_name, project_name)
);

CREATE TABLE IF NOT EXISTS tickets (
    id TEXT PRIMARY KEY,
    project_name TEXT NOT NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS backlog_entries (
    id TEXT PRIMARY KEY,
    project_name TEXT NOT NULL,
    primary_entry TEXT NOT NULL,
    refinement_notes TEXT NOT NULL DEFAULT '',
    refined_entry TEXT NOT NULL DEFAULT '',
    task_prompt TEXT NOT NULL DEFAULT '',
    primary_updated_at TEXT NOT NULL,
    notes_updated_at TEXT,
    refined_at TEXT,
    prompt_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS backlog_by_project ON backlog_entries (project_name, created_at);
"""


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    # What is deleted is overwritten, not left in free pages: a deleted backlog entry leaves no trace.
    conn.execute("PRAGMA secure_delete=ON")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)


# -- projects ----------------------------------------------------------


def insert_project(conn: sqlite3.Connection, project: Project) -> None:
    conn.execute(
        """INSERT INTO projects (name, path, repo_url, status, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            project.name,
            project.path,
            project.repo_url,
            project.status.value,
            _iso(project.created_at),
            _iso(project.updated_at),
        ),
    )
    conn.commit()


def _row_to_project(row: sqlite3.Row) -> Project:
    return Project(
        name=row["name"],
        path=row["path"],
        repo_url=row["repo_url"],
        status=ProjectStatus(row["status"]),
        created_at=_parse_iso(row["created_at"]),
        updated_at=_parse_iso(row["updated_at"]),
    )


def get_project(conn: sqlite3.Connection, name: str) -> Project | None:
    row = conn.execute("SELECT * FROM projects WHERE name = ?", (name,)).fetchone()
    return _row_to_project(row) if row else None


def list_projects(conn: sqlite3.Connection, *, status: ProjectStatus | None = None) -> list[Project]:
    if status is None:
        rows = conn.execute("SELECT * FROM projects ORDER BY name").fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM projects WHERE status = ? ORDER BY name", (status.value,)
        ).fetchall()
    return [_row_to_project(row) for row in rows]


def update_project_repo_url(conn: sqlite3.Connection, name: str, repo_url: str | None) -> None:
    conn.execute(
        "UPDATE projects SET repo_url = ?, updated_at = ? WHERE name = ?",
        (repo_url, _iso(datetime.now(timezone.utc)), name),
    )
    conn.commit()


def delete_project(conn: sqlite3.Connection, name: str) -> None:
    """Hard delete — no soft-delete, no undo (docs/open_questions.md OQ-7)."""
    conn.execute("DELETE FROM projects WHERE name = ?", (name,))
    conn.execute("DELETE FROM project_set_members WHERE project_name = ?", (name,))
    conn.execute("DELETE FROM tickets WHERE project_name = ?", (name,))
    conn.execute("DELETE FROM backlog_entries WHERE project_name = ?", (name,))
    conn.commit()


# -- project_sets --------------------------------------------------------


def create_project_set(conn: sqlite3.Connection, project_set: ProjectSet) -> None:
    conn.execute(
        "INSERT INTO project_sets (name, created_at) VALUES (?, ?)",
        (project_set.name, _iso(project_set.created_at)),
    )
    for project_name in project_set.project_names:
        conn.execute(
            "INSERT OR IGNORE INTO project_set_members (set_name, project_name) VALUES (?, ?)",
            (project_set.name, project_name),
        )
    conn.commit()


def get_project_set(conn: sqlite3.Connection, name: str) -> ProjectSet | None:
    row = conn.execute("SELECT * FROM project_sets WHERE name = ?", (name,)).fetchone()
    if row is None:
        return None
    members = conn.execute(
        "SELECT project_name FROM project_set_members WHERE set_name = ? ORDER BY project_name",
        (name,),
    ).fetchall()
    return ProjectSet(
        name=row["name"],
        project_names=[m["project_name"] for m in members],
        created_at=_parse_iso(row["created_at"]),
    )


def add_to_project_set(conn: sqlite3.Connection, set_name: str, project_name: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO project_set_members (set_name, project_name) VALUES (?, ?)",
        (set_name, project_name),
    )
    conn.commit()


def remove_from_project_set(conn: sqlite3.Connection, set_name: str, project_name: str) -> None:
    conn.execute(
        "DELETE FROM project_set_members WHERE set_name = ? AND project_name = ?",
        (set_name, project_name),
    )
    conn.commit()


def list_project_sets(conn: sqlite3.Connection) -> list[ProjectSet]:
    rows = conn.execute("SELECT name FROM project_sets ORDER BY name").fetchall()
    return [get_project_set(conn, row["name"]) for row in rows]


# -- tickets -------------------------------------------------------------


def insert_ticket(conn: sqlite3.Connection, ticket: Ticket) -> None:
    conn.execute(
        """INSERT INTO tickets (id, project_name, title, status, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            ticket.id,
            ticket.project_name,
            ticket.title,
            ticket.status.value,
            _iso(ticket.created_at),
            _iso(ticket.updated_at),
        ),
    )
    conn.commit()


def _row_to_ticket(row: sqlite3.Row) -> Ticket:
    return Ticket(
        id=row["id"],
        project_name=row["project_name"],
        title=row["title"],
        status=TicketStatus(row["status"]),
        created_at=_parse_iso(row["created_at"]),
        updated_at=_parse_iso(row["updated_at"]),
    )


def get_ticket(conn: sqlite3.Connection, ticket_id: str) -> Ticket | None:
    row = conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    return _row_to_ticket(row) if row else None


def list_tickets(conn: sqlite3.Connection, *, project_name: str | None = None) -> list[Ticket]:
    if project_name is None:
        rows = conn.execute("SELECT * FROM tickets ORDER BY created_at").fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM tickets WHERE project_name = ? ORDER BY created_at", (project_name,)
        ).fetchall()
    return [_row_to_ticket(row) for row in rows]


def update_ticket_status(conn: sqlite3.Connection, ticket_id: str, status: TicketStatus) -> None:
    conn.execute(
        "UPDATE tickets SET status = ?, updated_at = ? WHERE id = ?",
        (status.value, _iso(datetime.now(timezone.utc)), ticket_id),
    )
    conn.commit()


def new_ticket_id() -> str:
    return uuid.uuid4().hex


# -- backlog_entries -------------------------------------------------------

# The fields a backlog entry's owner may edit directly; the refined entry and the task prompt are Claude's.
BACKLOG_EDITABLE = ("primary_entry", "refinement_notes")


def new_backlog_id() -> str:
    return uuid.uuid4().hex


def insert_backlog_entry(conn: sqlite3.Connection, entry_id: str, project_name: str, primary_entry: str) -> dict:
    now = _iso(datetime.now(timezone.utc))
    conn.execute(
        """INSERT INTO backlog_entries (id, project_name, primary_entry, primary_updated_at, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (entry_id, project_name, primary_entry, now, now, now),
    )
    conn.commit()
    return get_backlog_entry(conn, entry_id)


def get_backlog_entry(conn: sqlite3.Connection, entry_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM backlog_entries WHERE id = ?", (entry_id,)).fetchone()
    return dict(row) if row else None


def list_backlog_entries(conn: sqlite3.Connection, *, project_name: str | None = None) -> list[dict]:
    if project_name is None:
        rows = conn.execute("SELECT * FROM backlog_entries ORDER BY created_at").fetchall()
    else:
        rows = conn.execute("SELECT * FROM backlog_entries WHERE project_name = ? ORDER BY created_at", (project_name,)).fetchall()
    return [dict(row) for row in rows]


def update_backlog_entry(conn: sqlite3.Connection, entry_id: str, **fields) -> dict | None:
    """Set the given fields (`primary_entry`, `refinement_notes`, `refined_entry`, `task_prompt`), with
    the time each changed. None when the entry no longer exists: nothing is written then."""
    stamps = {"primary_entry": "primary_updated_at", "refinement_notes": "notes_updated_at",
              "refined_entry": "refined_at", "task_prompt": "prompt_at"}
    unknown = set(fields) - set(stamps)
    if unknown:
        raise ValueError(f"not a backlog entry field: {', '.join(sorted(unknown))}")
    now = _iso(datetime.now(timezone.utc))
    values = {**fields, **{stamps[key]: now for key in fields}, "updated_at": now}
    assignments = ", ".join(f"{key} = ?" for key in values)
    changed = conn.execute(f"UPDATE backlog_entries SET {assignments} WHERE id = ?", (*values.values(), entry_id)).rowcount
    conn.commit()
    return get_backlog_entry(conn, entry_id) if changed else None


def delete_backlog_entry(conn: sqlite3.Connection, entry_id: str) -> bool:
    """Delete the entry and everything it holds; the write-ahead log is folded back too, so no copy
    of it outlives the deletion in the database's files."""
    deleted = conn.execute("DELETE FROM backlog_entries WHERE id = ?", (entry_id,)).rowcount
    conn.commit()
    if deleted:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    return bool(deleted)
