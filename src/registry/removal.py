"""`project.remove` — hard delete, immediate, cascading
(docs/open_questions.md OQ-7): this pillar purges its own rows
(`projects`, `project_set_members`, `tickets`) and the project-files
directory it created during bootstrap/import. No soft-delete, no undo.
Every *other* pillar implements the same "purge my data for this
project_id" as part of its own milestone — not retrofitted here.

The actual git repository on disk is left untouched: that's the user's
code, not this pillar's data to delete.
"""

from __future__ import annotations

import shutil
import sqlite3

from common.paths import Paths
from registry import db


class ProjectNotFoundError(Exception):
    pass


def remove_project(conn: sqlite3.Connection, paths: Paths, name: str) -> None:
    if db.get_project(conn, name) is None:
        raise ProjectNotFoundError(name)

    db.delete_project(conn, name)

    project_dir = paths.project_dir(name)
    if project_dir.exists():
        shutil.rmtree(project_dir)
