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

from common.job_steps import done, step
from common.paths import Paths
from common.projectfiles import ProjectFilesClient
from registry import claude_templates, db, gitops, github, scaffold
from registry.models import BootstrapParams, BootstrapResult, Project


class BootstrapError(Exception):
    pass


def bootstrap_project(conn: sqlite3.Connection, paths: Paths, params: BootstrapParams) -> BootstrapResult:
    with step("check_name") as handle:
        if not claude_templates.is_project_id(params.name):
            # The name becomes the project id inside the scaffolded .claude/settings.json rules (REQ-000000296).
            raise BootstrapError(
                f"{params.name!r} is not a usable project name: use letters, digits, '.', '_' and '-', "
                "starting with a letter or digit"
            )
        if db.get_project(conn, params.name) is not None:
            raise BootstrapError(f"project {params.name!r} is already registered")
        handle.detail = params.name

    dest = Path(params.dest_dir) / params.name
    with step("scaffold") as handle:
        scaffold.create_project_tree(dest, params.name, params.template)
        handle.detail = str(dest)
    with step("init_repo"):
        gitops.init_repo_and_commit(dest, author_email=f"{params.owner}@users.noreply.github.com")
        gitops.create_dev_branch(dest)

    repo_url: str | None = None
    github_created = False
    pushed = False
    labels_created: list[str] = []

    if params.create_github_repo:
        with step("create_github") as handle:
            try:
                repo = github.create_repo(
                    params.name, params.owner, token=params.github_token,
                    private=not params.public, description=params.description,
                )
            except github.NoGitHubCredentials as exc:
                raise BootstrapError(
                    f"{exc} — the project is scaffolded and committed "
                    "locally; retry with create_github_repo=false or provide a token"
                ) from exc
            github_created = True
            remote_url = f"git@{params.remote_host}:{params.owner}/{params.name}.git"
            gitops.add_remote_and_push(dest, remote_url)
            pushed = True
            repo_url = repo.get("html_url") or f"https://{params.remote_host}/{params.owner}/{params.name}"
            handle.detail = repo_url

        if params.ensure_labels:
            with step("labels") as handle:
                labels_created = github.ensure_bootstrap_labels(f"{params.owner}/{params.name}", params.github_token)
                handle.detail = f"{len(labels_created)} label(s) created"
        else:
            done("labels", "not requested")
    else:
        done("create_github", "not requested")
        done("labels", "not requested")

    with step("register"):
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
