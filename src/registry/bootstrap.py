"""`project.bootstrap` — the deterministic path: scaffold, `git init`,
`dev` branch, optional GitHub repo + push + label creation, then
register the project (docs/milestones.md M2). The project-files
directory is created here, as a *consequence* of registering the row,
never the other way around (docs/architecture.md's Data model boundary
note).

Gitflow tracking (`.lvx/config.json`, M16's `bootstrap-little-project`
parity gap) is initialized by `registry/app.py`'s own `job_bootstrap`
endpoint, deliberately *not* here — it needs a real cross-pillar call
to Version Control & Release that must happen after this function's own
caller releases its lock, or it deadlocks against this same pillar's
own `GET /v1/projects/{name}` (see `registry/version_client.py`).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from common.paths import Paths
from common.projectfiles import ProjectFilesClient
from registry import db, gitops, github, scaffold
from registry.models import BootstrapParams, BootstrapResult, Project


class BootstrapError(Exception):
    pass


def bootstrap_project(conn: sqlite3.Connection, paths: Paths, params: BootstrapParams) -> BootstrapResult:
    if db.get_project(conn, params.name) is not None:
        raise BootstrapError(f"project {params.name!r} is already registered")

    dest = Path(params.dest_dir) / params.name
    scaffold.create_project_tree(dest, params.name, params.template)
    gitops.init_repo_and_commit(dest, author_email=f"{params.owner}@users.noreply.github.com")
    gitops.create_dev_branch(dest)

    repo_url: str | None = None
    github_created = False
    pushed = False
    labels_created: list[str] = []

    if params.create_github_repo:
        if params.github_token:
            repo = github.create_repository(
                params.name,
                params.github_token,
                private=not params.public,
                description=params.description,
            )
        elif github.gh_available():
            repo = github.create_repository_with_gh(
                params.name, params.owner, private=not params.public, description=params.description
            )
        else:
            raise BootstrapError(
                "no way to create the GitHub repo: no token given and `gh` is not "
                "installed/logged in — the project is scaffolded and committed "
                "locally; retry with create_github_repo=false or provide a token"
            )
        github_created = True
        remote_url = f"git@{params.remote_host}:{params.owner}/{params.name}.git"
        gitops.add_remote_and_push(dest, remote_url)
        pushed = True
        repo_url = repo.get("html_url") or f"https://{params.remote_host}/{params.owner}/{params.name}"

        if params.ensure_labels:
            repo_full_name = f"{params.owner}/{params.name}"
            for label_name, color, description in github.BOOTSTRAP_LABELS:
                if params.github_token:
                    github.ensure_label(repo_full_name, params.github_token, label_name, color, description)
                elif github.gh_available():
                    github.ensure_label_with_gh(repo_full_name, label_name, color, description)
                labels_created.append(label_name)

    project = Project(name=params.name, path=str(dest), repo_url=repo_url)
    db.insert_project(conn, project)

    files = ProjectFilesClient(paths, caller_pillar="registry")
    files.write(
        params.name,
        "info.registry.json",
        json.dumps({"name": params.name, "path": str(dest), "repo_url": repo_url}),
    )

    return BootstrapResult(
        name=params.name, path=str(dest), repo_url=repo_url, github_created=github_created, pushed=pushed,
        labels_created=labels_created,
    )
