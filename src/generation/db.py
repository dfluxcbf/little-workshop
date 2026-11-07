"""Image Generation's own SQLite file (docs/pillars/generation.md, "Store"). WAL mode, opened only by this pillar.

A request is a set of images; an image is one picture to render, with its settings resolved when the request was
taken (seed included), so a retry renders the same picture. The queue's order is the requests' arrival order,
then each request's image order. `state` holds the queue's pause (the one row a failure writes and only the
user's resume clears) and the hand-over of ComfyUI to the user. An image holds the graph it renders (filled
when the request was taken) and its outputs, one file per role; `file_name`, `sha256` and `bytes` are the
primary output's. `recipes` holds the saved and imported recipes (generation/recipes.py).
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

QUEUED, RUNNING, SUCCESS, FAILED, CANCELLED = "queued", "running", "success", "failed", "cancelled"
REQUEST_STATUSES = (QUEUED, RUNNING, SUCCESS, FAILED, CANCELLED)
OPEN_STATUSES = (QUEUED, RUNNING)
PENDING, IMG_RUNNING, DONE, IMG_FAILED, IMG_CANCELLED = "pending", "running", "done", "failed", "cancelled"

SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    project_id TEXT NOT NULL,
    name TEXT,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    error TEXT,
    files_deleted_at TEXT
);
CREATE TABLE IF NOT EXISTS images (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL,
    idx INTEGER NOT NULL,
    settings TEXT NOT NULL,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    file_name TEXT,
    sha256 TEXT,
    bytes INTEGER,
    last_check TEXT,
    error TEXT,
    finished_at TEXT,
    graph TEXT,
    outputs TEXT,
    UNIQUE (request_id, idx)
);
CREATE INDEX IF NOT EXISTS images_status ON images (status, request_id, idx);
CREATE TABLE IF NOT EXISTS batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    model TEXT NOT NULL,
    size INTEGER NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    restart_seconds REAL,
    render_seconds REAL,
    good INTEGER NOT NULL DEFAULT 0,
    bad INTEGER NOT NULL DEFAULT 0,
    outcome TEXT,
    detail TEXT
);
CREATE TABLE IF NOT EXISTS state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS recipes (
    name TEXT PRIMARY KEY,
    body TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""

# Columns added after the first release, for a database created before them.
ADDED_COLUMNS = {"images": (("graph", "TEXT"), ("outputs", "TEXT"))}

PAUSE_KEY = "pause"
LEND_KEY = "lend"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    for table, columns in ADDED_COLUMNS.items():
        present = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        for name, kind in columns:
            if name not in present:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")
    conn.commit()
    return conn


def _request(row: sqlite3.Row) -> dict:
    return {key: row[key] for key in ("id", "project_id", "name", "status", "created_at", "started_at", "finished_at", "error", "files_deleted_at")}


def _image(row: sqlite3.Row) -> dict:
    return {"id": row["id"], "request_id": row["request_id"], "idx": row["idx"], "settings": json.loads(row["settings"]),
            "status": row["status"], "attempts": row["attempts"], "file_name": row["file_name"], "sha256": row["sha256"],
            "bytes": row["bytes"], "last_check": json.loads(row["last_check"]) if row["last_check"] else None,
            "error": row["error"], "finished_at": row["finished_at"], "graph": json.loads(row["graph"]) if row["graph"] else None,
            "outputs": json.loads(row["outputs"]) if row["outputs"] else None}


def add_request(conn: sqlite3.Connection, project_id: str, name: str | None, images: list[dict]) -> str:
    request_id = uuid.uuid4().hex[:16]
    conn.execute("INSERT INTO requests (id, project_id, name, status, created_at) VALUES (?, ?, ?, ?, ?)",
                 (request_id, project_id, name, QUEUED, now()))
    conn.executemany("INSERT INTO images (request_id, idx, settings, graph, status) VALUES (?, ?, ?, ?, ?)",
                     [(request_id, idx, json.dumps({k: v for k, v in settings.items() if k != "graph"}),
                       json.dumps(settings["graph"]) if settings.get("graph") else None, PENDING) for idx, settings in enumerate(images)])
    conn.commit()
    return request_id


def get_request(conn: sqlite3.Connection, request_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM requests WHERE id = ?", (request_id,)).fetchone()
    return _request(row) if row else None


def list_requests(conn: sqlite3.Connection, *, project_id: str | None = None, statuses: tuple[str, ...] | None = None,
                  limit: int = 50, offset: int = 0) -> list[dict]:
    sql, args = "SELECT * FROM requests WHERE 1 = 1", []
    if project_id:
        sql += " AND project_id = ?"
        args.append(project_id)
    if statuses:
        sql += f" AND status IN ({','.join('?' * len(statuses))})"
        args.extend(statuses)
    sql += " ORDER BY seq DESC LIMIT ? OFFSET ?"
    args.extend((limit, offset))
    return [_request(row) for row in conn.execute(sql, args)]


def images_of(conn: sqlite3.Connection, request_id: str) -> list[dict]:
    return [_image(row) for row in conn.execute("SELECT * FROM images WHERE request_id = ? ORDER BY idx", (request_id,))]


def image_counts(conn: sqlite3.Connection, request_id: str) -> dict[str, int]:
    return {row[0]: row[1] for row in conn.execute("SELECT status, COUNT(*) FROM images WHERE request_id = ? GROUP BY status", (request_id,))}


def pending_images(conn: sqlite3.Connection) -> list[dict]:
    """Every image still to render, in queue order, from requests that are still open."""
    rows = conn.execute(
        "SELECT images.* FROM images JOIN requests ON requests.id = images.request_id "
        "WHERE images.status = ? AND requests.status IN (?, ?) ORDER BY requests.seq, images.idx",
        (PENDING, QUEUED, RUNNING))
    return [_image(row) for row in rows]


def set_image(conn: sqlite3.Connection, image_id: int, **fields) -> None:
    for key in ("last_check", "outputs"):
        if key in fields and fields[key] is not None:
            fields[key] = json.dumps(fields[key])
    assignments = ", ".join(f"{key} = ?" for key in fields)
    conn.execute(f"UPDATE images SET {assignments} WHERE id = ?", (*fields.values(), image_id))


def set_request(conn: sqlite3.Connection, request_id: str, **fields) -> None:
    assignments = ", ".join(f"{key} = ?" for key in fields)
    conn.execute(f"UPDATE requests SET {assignments} WHERE id = ?", (*fields.values(), request_id))


def reset_running(conn: sqlite3.Connection) -> int:
    """At start: an image left running by a stopped process goes back to the queue, its attempt not counted."""
    changed = conn.execute("UPDATE images SET status = ? WHERE status = ?", (PENDING, IMG_RUNNING)).rowcount
    conn.commit()
    return changed


def get_state(conn: sqlite3.Connection, key: str) -> dict | None:
    row = conn.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
    return json.loads(row["value"]) if row else None


def set_state(conn: sqlite3.Connection, key: str, value: dict | None) -> None:
    if value is None:
        conn.execute("DELETE FROM state WHERE key = ?", (key,))
    else:
        conn.execute("INSERT OR REPLACE INTO state (key, value) VALUES (?, ?)", (key, json.dumps(value)))
    conn.commit()


def get_pause(conn: sqlite3.Connection) -> dict | None:
    return get_state(conn, PAUSE_KEY)


def set_pause(conn: sqlite3.Connection, pause: dict | None) -> None:
    set_state(conn, PAUSE_KEY, pause)


def get_lend(conn: sqlite3.Connection) -> dict | None:
    """The hand-over of ComfyUI to the user, while it lasts."""
    return get_state(conn, LEND_KEY)


def set_lend(conn: sqlite3.Connection, lend: dict | None) -> None:
    set_state(conn, LEND_KEY, lend)


def list_recipes(conn: sqlite3.Connection) -> list[dict]:
    return [{**json.loads(row["body"]), "created_at": row["created_at"]} for row in conn.execute("SELECT * FROM recipes ORDER BY name")]


def get_recipe(conn: sqlite3.Connection, name: str) -> dict | None:
    row = conn.execute("SELECT * FROM recipes WHERE name = ?", (name,)).fetchone()
    return {**json.loads(row["body"]), "created_at": row["created_at"]} if row else None


def put_recipe(conn: sqlite3.Connection, recipe: dict) -> None:
    conn.execute("INSERT OR REPLACE INTO recipes (name, body, created_at) VALUES (?, ?, ?)", (recipe["name"], json.dumps(recipe), now()))
    conn.commit()


def delete_recipe(conn: sqlite3.Connection, name: str) -> bool:
    deleted = conn.execute("DELETE FROM recipes WHERE name = ?", (name,)).rowcount
    conn.commit()
    return bool(deleted)


def start_batch(conn: sqlite3.Connection, model: str, size: int) -> int:
    cursor = conn.execute("INSERT INTO batches (model, size, started_at) VALUES (?, ?, ?)", (model, size, now()))
    conn.commit()
    return int(cursor.lastrowid or 0)


def finish_batch(conn: sqlite3.Connection, batch_id: int, **fields) -> None:
    fields["finished_at"] = now()
    if isinstance(fields.get("detail"), (dict, list)):
        fields["detail"] = json.dumps(fields["detail"])
    assignments = ", ".join(f"{key} = ?" for key in fields)
    conn.execute(f"UPDATE batches SET {assignments} WHERE id = ?", (*fields.values(), batch_id))
    conn.commit()


def measured_batches(conn: sqlite3.Connection, limit: int = 20) -> list[dict]:
    """The latest batches that rendered (a restart and a render measured), newest first."""
    rows = conn.execute("SELECT model, size, restart_seconds, render_seconds FROM batches "
                        "WHERE render_seconds IS NOT NULL ORDER BY id DESC LIMIT ?", (limit * 4,))
    return [dict(row) for row in rows]


def purge_project(conn: sqlite3.Connection, project_id: str) -> list[str]:
    ids = [row[0] for row in conn.execute("SELECT id FROM requests WHERE project_id = ?", (project_id,))]
    for request_id in ids:
        conn.execute("DELETE FROM images WHERE request_id = ?", (request_id,))
    conn.execute("DELETE FROM requests WHERE project_id = ?", (project_id,))
    conn.commit()
    return ids
