"""`project.import` — the other half of M2 (docs/milestones.md M8): an
existing repository, already brought into conformance by a Claude CLI
session (human-interactive, or Agentic Development's own internal
dispatch — the identical mechanism either way, both reaching this
pillar through the same MCP-exposed entry point, `workshop_project_import`)
gets registered exactly the way `project.bootstrap` registers a
brand-new one.

Deliberately mechanical, the same way `bootstrap_project`'s own
registration step is: whether the repository actually *conforms* to
policy is the calling session's judgment to make (and, later, Policy &
Compliance's to verify, M11) — not this function's. It only checks that
there is something real to register: a directory that exists and is a
git repository. Once registered, the project is resolvable like any
other — a normal `agent.run_task` (M6) is how the "bring into
conformance" work actually gets done, no new agent machinery needed
here.

Named `import_.py`, not `import.py` — `import` is a Python keyword and
can't be a module name.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from common.paths import Paths
from common.projectfiles import ProjectFilesClient
from registry import db, gitops
from registry.models import ImportParams, ImportResult, Project


class ProjectImportError(Exception):
    pass


def import_project(conn: sqlite3.Connection, paths: Paths, params: ImportParams) -> ImportResult:
    if db.get_project(conn, params.name) is not None:
        raise ProjectImportError(f"project {params.name!r} is already registered")

    path = Path(params.path)
    if not path.is_dir():
        raise ProjectImportError(f"{path} does not exist or is not a directory")
    if not (path / ".git").exists():
        raise ProjectImportError(f"{path} is not a git repository (no .git)")

    repo_url = gitops.remote_url(path)

    project = Project(name=params.name, path=str(path), repo_url=repo_url)
    db.insert_project(conn, project)

    files = ProjectFilesClient(paths, caller_pillar="registry")
    files.write(
        params.name,
        "info.registry.json",
        json.dumps({"name": params.name, "path": str(path), "repo_url": repo_url}),
    )

    return ImportResult(name=params.name, path=str(path), repo_url=repo_url)
