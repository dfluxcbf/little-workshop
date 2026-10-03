"""Registry & Portfolio's Pillar Interface. `/v1/jobs/project.bootstrap`
and `/v1/jobs/project.remove` follow the Job-dispatch convention every
target pillar's endpoint uses (docs/design.md's dispatch diagram:
`POST /v1/jobs/<type>`); everything else is a direct read/write call for
the registry itself — `projects`/`project_sets`/`tickets` bookkeeping
doesn't need a Job's audit trail the way a real operation does.
"""

from __future__ import annotations

import sqlite3
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ValidationError

from common.job_steps import StepReporter, reporting, step
from common.paths import Paths
from common.incident import install_incident_reporting
from registry import db, github, gitops, version_client
from registry.bootstrap import BootstrapError, bootstrap_project
from registry.connect_github import ConnectGitHubError, connect_github
from registry.import_ import ProjectImportError, import_project
from registry.models import BootstrapParams, ConnectGitHubParams, ImportParams, ProjectSet, ProjectStatus, Ticket
from registry.removal import ProjectNotFoundError, remove_project


class JobRequest(BaseModel):
    job_id: str
    project_id: str | None = None
    params: dict[str, Any] = {}


class CreateProjectSetRequest(BaseModel):
    name: str
    project_names: list[str] = []


class CreateTicketRequest(BaseModel):
    project_name: str
    title: str


# github_token_status() shells out to `gh` twice, including one real
# network round trip to GitHub's own API — a credential's validity/
# expiration essentially never changes between one dashboard page view
# and the next, so /v1/secrets caches it for this long rather than
# re-checking on every call (2026-09-23 finding: this was the dominant
# cost of the Dashboard's own Pillars page, ~1.3s of it). A short TTL,
# not an eternal launch-time cache: a revoked/rotated token should still
# surface here within a few minutes, not only after the next restart.
SECRETS_CACHE_TTL_SECONDS = 300.0


def build_app(conn: sqlite3.Connection, paths: Paths) -> FastAPI:
    app = FastAPI(title="little-workshop Registry & Portfolio")
    install_incident_reporting(app, paths, pillar="registry")
    lock = threading.Lock()

    def reported(request: "JobRequest"):
        return reporting(StepReporter(paths, request.job_id))

    @app.get("/v1/health")
    def health() -> dict:
        with lock:
            count = len(db.list_projects(conn, status=ProjectStatus.ACTIVE))
        return {"ok": True, "active_projects": count}

    @app.post("/v1/jobs/project.bootstrap")
    def job_bootstrap(request: JobRequest) -> dict:
        try:
            bootstrap_params = BootstrapParams(**request.params)
        except ValidationError as exc:
            return {"outcome": "failed", "error": str(exc)}
        with reported(request), lock:
            try:
                result = bootstrap_project(conn, paths, bootstrap_params)
            except BootstrapError as exc:
                return {"outcome": "failed", "error": str(exc)}
        # Outside `lock`, deliberately: version.init calls back into this
        # pillar's own PI to resolve the project it just registered
        # (common/registry_client.py) -- doing that while still holding
        # `lock` above would deadlock against this same endpoint's own
        # GET /v1/projects/{name} handler. M16's bootstrap-little-project
        # parity gap (docs/requirements.md): the old tool always ran
        # `lvx init` as part of `create`; little-workshop's own `version`
        # pillar already writes a byte-compatible `.lvx/config.json`.
        project_dir = Path(result.path)
        with reported(request):
            with step("init_version") as handle:
                if version_client.init_version_tracking(paths, result.name):
                    gitops.run(["git", "add", "-A"], cwd=project_dir)
                    gitops.run(["git", "commit", "-m", "chore: version init"], cwd=project_dir)
                    gitops.run(["git", "checkout", "dev"], cwd=project_dir)
                    gitops.run(["git", "merge", "--ff-only", "master"], cwd=project_dir)
                    # Deliberately left checked out on `dev`, not `master` — the
                    # ready-to-start-working gitflow state, matching what a
                    # caller doing this same "bootstrap then version.init" dance
                    # by hand already expected before this was automated.
                    result.version_initialized = True
                    if result.pushed:
                        gitops.run(["git", "push", "origin", "master:master"], cwd=project_dir)
                        gitops.run(["git", "push", "origin", "dev:dev"], cwd=project_dir)
                    handle.detail = "gitflow branches ready, checked out on dev"
                else:
                    handle.detail = "version tracking was not initialized"
        return {"outcome": "succeeded", "result": result.model_dump(mode="json")}

    @app.post("/v1/jobs/project.import")
    def job_import(request: JobRequest) -> dict:
        try:
            import_params = ImportParams(**request.params)
        except ValidationError as exc:
            return {"outcome": "failed", "error": str(exc)}
        with reported(request), lock:
            try:
                result = import_project(conn, paths, import_params)
            except ProjectImportError as exc:
                return {"outcome": "failed", "error": str(exc)}
        return {"outcome": "succeeded", "result": result.model_dump(mode="json")}

    @app.post("/v1/jobs/project.connect_github")
    def job_connect_github(request: JobRequest) -> dict:
        try:
            connect_params = ConnectGitHubParams(**{"name": request.project_id, **request.params})
        except ValidationError as exc:
            return {"outcome": "failed", "error": str(exc)}
        with reported(request), lock:
            try:
                result = connect_github(conn, paths, connect_params)
            except (ConnectGitHubError, subprocess.CalledProcessError) as exc:
                detail = exc.stderr.strip() if isinstance(exc, subprocess.CalledProcessError) and exc.stderr else str(exc)
                return {"outcome": "failed", "error": detail}
        return {"outcome": "succeeded", "result": result.model_dump(mode="json")}

    @app.post("/v1/jobs/project.remove")
    def job_remove(request: JobRequest) -> dict:
        name = request.params.get("name") or request.project_id
        if not name:
            return {"outcome": "failed", "error": "missing project name"}
        with reported(request), lock:
            try:
                remove_project(conn, paths, name)
            except ProjectNotFoundError:
                return {"outcome": "failed", "error": f"project {name!r} not found"}
        return {"outcome": "succeeded", "result": {"name": name}}

    @app.get("/v1/projects")
    def list_projects(status: str | None = None) -> list[dict]:
        status_enum = ProjectStatus(status) if status else None
        with lock:
            return [p.model_dump(mode="json") for p in db.list_projects(conn, status=status_enum)]

    @app.get("/v1/projects/{name}")
    def get_project(name: str) -> dict:
        with lock:
            project = db.get_project(conn, name)
        if project is None:
            raise HTTPException(status_code=404, detail="project not found")
        return project.model_dump(mode="json")

    @app.post("/v1/project_sets")
    def create_project_set(request: CreateProjectSetRequest) -> dict:
        project_set = ProjectSet(name=request.name, project_names=request.project_names)
        with lock:
            db.create_project_set(conn, project_set)
        return project_set.model_dump(mode="json")

    @app.get("/v1/project_sets")
    def list_project_sets() -> list[dict]:
        with lock:
            return [s.model_dump(mode="json") for s in db.list_project_sets(conn)]

    @app.get("/v1/project_sets/{name}")
    def get_project_set(name: str) -> dict:
        with lock:
            project_set = db.get_project_set(conn, name)
        if project_set is None:
            raise HTTPException(status_code=404, detail="project set not found")
        return project_set.model_dump(mode="json")

    @app.post("/v1/project_sets/{name}/members/{project_name}")
    def add_project_set_member(name: str, project_name: str) -> dict:
        with lock:
            db.add_to_project_set(conn, name, project_name)
            project_set = db.get_project_set(conn, name)
        if project_set is None:
            raise HTTPException(status_code=404, detail="project set not found")
        return project_set.model_dump(mode="json")

    @app.delete("/v1/project_sets/{name}/members/{project_name}")
    def remove_project_set_member(name: str, project_name: str) -> dict:
        with lock:
            db.remove_from_project_set(conn, name, project_name)
            project_set = db.get_project_set(conn, name)
        if project_set is None:
            raise HTTPException(status_code=404, detail="project set not found")
        return project_set.model_dump(mode="json")

    @app.post("/v1/tickets")
    def create_ticket(request: CreateTicketRequest) -> dict:
        ticket = Ticket(id=db.new_ticket_id(), project_name=request.project_name, title=request.title)
        with lock:
            db.insert_ticket(conn, ticket)
        return ticket.model_dump(mode="json")

    @app.get("/v1/tickets")
    def list_tickets(project_name: str | None = None) -> list[dict]:
        with lock:
            return [t.model_dump(mode="json") for t in db.list_tickets(conn, project_name=project_name)]

    secrets_cache: dict[str, Any] = {"value": None, "at": 0.0}

    @app.get("/v1/secrets")
    def list_secrets() -> list[dict]:
        """System health's own secrets/tokens panel — never the token
        itself, only whether it authenticates and, when it carries one,
        its expiration. See SECRETS_CACHE_TTL_SECONDS for why this is
        cached rather than re-checked on every call."""
        now = time.monotonic()
        if secrets_cache["value"] is None or now - secrets_cache["at"] > SECRETS_CACHE_TTL_SECONDS:
            secrets_cache["value"] = [{"name": "github", **github.github_token_status()}]
            secrets_cache["at"] = now
        return secrets_cache["value"]

    return app
