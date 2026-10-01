"""`project.connect_github` — give an already-registered project the
GitHub setup `project.bootstrap` would have given it with
`create_github_repo=true` (REQ-000000279..282), without removing and
re-importing it:

- no `origin` remote: create the repository, add it as `origin`, push
  `master` and `dev`;
- an `origin` remote already there (e.g. created by hand): adopt it,
  never create or push;

then ensure the bootstrap labels and record `repo_url`. Running it again
is harmless — the second run just adopts what the first one created.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from common.paths import Paths
from common.projectfiles import ProjectFilesClient
from registry import db, github, gitops
from registry.models import ConnectGitHubParams, ConnectGitHubResult


class ConnectGitHubError(Exception):
    pass


def connect_github(conn: sqlite3.Connection, paths: Paths, params: ConnectGitHubParams) -> ConnectGitHubResult:
    project = db.get_project(conn, params.name)
    if project is None:
        raise ConnectGitHubError(f"project {params.name!r} is not registered")
    path = Path(project.path)
    if not (path / ".git").exists():
        raise ConnectGitHubError(f"{path} is not a git repository (no .git)")

    existing = gitops.remote_url(path)
    github_created = pushed = False
    if existing is None:
        try:
            repo = github.create_repo(
                params.name, params.owner, token=params.github_token,
                private=not params.public, description=params.description,
            )
        except github.GitHubError as exc:
            raise ConnectGitHubError(str(exc)) from exc
        github_created = True
        gitops.add_remote_and_push(path, f"git@{params.remote_host}:{params.owner}/{params.name}.git")
        pushed = True
        repo_url = repo.get("html_url") or f"https://{params.remote_host}/{params.owner}/{params.name}"
    else:
        repo_url = github.web_url(existing)

    labels_created: list[str] = []
    full_name = github.full_name_from_url(repo_url)
    if params.ensure_labels and full_name:
        try:
            labels_created = github.ensure_bootstrap_labels(full_name, params.github_token)
        except github.GitHubError as exc:
            raise ConnectGitHubError(f"connected {repo_url}, but ensuring its labels failed: {exc}") from exc

    db.update_project_repo_url(conn, params.name, repo_url)
    ProjectFilesClient(paths, caller_pillar="registry").write(
        params.name,
        "info.registry.json",
        json.dumps({"name": params.name, "path": project.path, "repo_url": repo_url}),
    )

    return ConnectGitHubResult(
        name=params.name, path=project.path, repo_url=repo_url, adopted_remote=existing is not None,
        github_created=github_created, pushed=pushed, labels_created=labels_created,
    )
