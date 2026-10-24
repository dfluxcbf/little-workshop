"""Registry & Portfolio's Pillar Interface. `/v1/jobs/project.bootstrap`
and `/v1/jobs/project.remove` follow the Job-dispatch convention every
target pillar's endpoint uses (docs/design.md's dispatch diagram:
`POST /v1/jobs/<type>`); everything else is a direct read/write call for
the registry itself — `projects`/`project_sets`/`tickets`/backlog
bookkeeping doesn't need a Job's audit trail the way a real operation
does. The two backlog actions that ask Claude (refine an entry, write its
task prompt) are Jobs: they take a minute, and their progress shows.
"""

from __future__ import annotations

import sqlite3
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ValidationError

from common import events, serving
from common.job_steps import StepReporter, reporting, step
from common.paths import Paths
from common.incident import install_incident_reporting
from common.pillar_socket import resolve_pillar_socket
from common.uds_client import client_for_socket
from registry import agent_client, backlog, claude_sync, db, github, gitops, version_client
from registry.bootstrap import BootstrapError, bootstrap_project
from registry.assets_config import AssetsConfigError, configure_assets
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


class CreateBacklogEntryRequest(BaseModel):
    project_name: str
    primary_entry: str


class EditBacklogEntryRequest(BaseModel):
    primary_entry: str | None = None
    refinement_notes: str | None = None


# github_token_status() shells out to `gh` twice, including one real
# network round trip to GitHub's own API — a credential's validity/
# expiration essentially never changes between one dashboard page view
# and the next, so /v1/secrets caches it for this long rather than
# re-checking on every call (2026-09-23 finding: this was the dominant
# cost of the Dashboard's own Pillars page, ~1.3s of it). A short TTL,
# not an eternal launch-time cache: a revoked/rotated token should still
# surface here within a few minutes, not only after the next restart.
SECRETS_CACHE_TTL_SECONDS = 300.0


def backlog_ref(entry_id: str) -> str:
    """What the Claude sessions working on a backlog entry are recorded under at the agent."""
    return f"backlog:{entry_id}"


def start_improvement_branch(paths: Paths, project_id: str, name: str) -> dict:
    """Ask Orchestration for a `version.improvement_start` Job and return it as it settled."""
    socket_path = resolve_pillar_socket(paths, "orchestration")
    if socket_path is None:
        return {"status": "failed", "error": "Orchestration is unreachable"}
    template = {"type": "version.improvement_start", "target_pillar": "version", "project_id": project_id,
                "params": {"name": name, "issue": None, "force": False}, "queue_key": project_id, "depends_on": [],
                "created_by": "registry"}
    try:
        with client_for_socket(socket_path, timeout=60.0) as client:
            response = client.post("/v1/jobs", json=template)
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPError, OSError, ValueError) as exc:
        return {"status": "failed", "error": str(exc)}


def build_app(conn: sqlite3.Connection, paths: Paths, *, start_branch=None) -> FastAPI:
    app = FastAPI(title="little-workshop Registry & Portfolio")
    install_incident_reporting(app, paths, pillar="registry")
    lock = threading.Lock()
    start_branch = start_branch or (lambda project_id, name: start_improvement_branch(paths, project_id, name))

    def reported(request: "JobRequest"):
        return reporting(StepReporter(paths, request.job_id))

    @app.get("/v1/health")
    def health() -> dict:
        with serving.held_or_busy(lock) as free:  # busy work never makes it look hung (REQ-000000409)
            count = len(db.list_projects(conn, status=ProjectStatus.ACTIVE)) if free else None
        return {"ok": True, "active_projects": count, **({} if free else {"busy": True})}

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
        events.emit_in_background(paths, "registry.project_registered", result.name, {"how": "bootstrap"})
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
        events.emit_in_background(paths, "registry.project_registered", result.name, {"how": "import"})
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

    @app.post("/v1/jobs/project.configure_assets")
    def job_configure_assets(request: JobRequest) -> dict:
        """Params: `assets_dir` (folder of the checkout to show; empty turns asset review off), `mcp` (default true:
        declare the project's `asset-review` MCP server in its `.mcp.json`)."""
        if not request.project_id:
            return {"outcome": "failed", "error": "missing project name"}
        with reported(request), lock:
            try:
                result = configure_assets(conn, request.project_id, request.params.get("assets_dir"), mcp=request.params.get("mcp", True) is not False)
            except AssetsConfigError as exc:
                return {"outcome": "failed", "error": str(exc)}
        return {"outcome": "succeeded", "result": result}

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

    @app.post("/v1/jobs/registry.sync_claude_files")
    def job_sync_claude_files(request: JobRequest) -> dict:
        """What differs between each registered project's generated Claude files and the templates; with
        `params.write`, each differing project gets them on an improvement branch of its own, committed
        and left for the owner to finish. `project_id` limits it to one project."""
        with lock:
            projects = [p for p in db.list_projects(conn) if not request.project_id or p.name == request.project_id]
        if request.project_id and not projects:
            return {"outcome": "failed", "error": f"project {request.project_id!r} not found"}
        reports = []
        for project in projects:
            checkout = Path(project.path)
            if not checkout.is_dir():
                reports.append({"project_id": project.name, "changed": [], "written": False, "detail": "skipped: checkout not found"})
            elif request.params.get("write") is True:
                reports.append(claude_sync.sync_on_branch(checkout, project.name, start_branch))
            else:
                report = claude_sync.pending(checkout, project.name)
                reports.append({"project_id": project.name, "changed": report["changed"], "note": report["note"], "written": False,
                                "detail": "in sync" if not report["changed"] else f"{len(report['changed'])} file(s) differ"})
        return {"outcome": "succeeded", "result": {"projects": reports, "differing": sum(1 for r in reports if r["changed"])}}

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

    # -- backlog: a project's entries, refined with Claude into tasks (registry/backlog.py) ---------------

    # Changes every time an entry is added, edited or deleted, so a watcher (the Dashboard's live pages)
    # can tell something changed with one cheap call; a restart starts a new count under a new boot id.
    backlog_version = {"boot": uuid.uuid4().hex[:8], "count": 0}

    def backlog_changed() -> None:
        backlog_version["count"] += 1

    def entry_view(entry: dict) -> dict:
        return {**entry, "title": backlog.entry_title(entry)}

    @app.get("/v1/backlog")
    def list_backlog(project_name: str | None = None) -> list[dict]:
        with lock:
            return [entry_view(e) for e in db.list_backlog_entries(conn, project_name=project_name)]

    @app.get("/v1/backlog/version")
    def get_backlog_version() -> dict:
        return {"version": f"{backlog_version['boot']}:{backlog_version['count']}"}

    @app.post("/v1/backlog")
    def add_backlog_entry(request: CreateBacklogEntryRequest) -> dict:
        if not request.primary_entry.strip():
            raise HTTPException(status_code=422, detail="the entry is empty")
        with lock:
            if db.get_project(conn, request.project_name) is None:
                raise HTTPException(status_code=404, detail=f"project {request.project_name!r} is not registered")
            entry = db.insert_backlog_entry(conn, db.new_backlog_id(), request.project_name, request.primary_entry.strip())
            backlog_changed()
        return entry_view(entry)

    @app.get("/v1/backlog/{entry_id}")
    def get_backlog_entry(entry_id: str) -> dict:
        with lock:
            entry = db.get_backlog_entry(conn, entry_id)
        if entry is None:
            raise HTTPException(status_code=404, detail="no such backlog entry")
        return entry_view(entry)

    @app.patch("/v1/backlog/{entry_id}")
    def edit_backlog_entry(entry_id: str, request: EditBacklogEntryRequest) -> dict:
        fields = request.model_dump(exclude_none=True)
        if "primary_entry" in fields and not fields["primary_entry"].strip():
            raise HTTPException(status_code=422, detail="the primary entry cannot be empty")
        with lock:
            entry = db.update_backlog_entry(conn, entry_id, **fields) if fields else db.get_backlog_entry(conn, entry_id)
            if entry is None:
                raise HTTPException(status_code=404, detail="no such backlog entry")
            backlog_changed()
        return entry_view(entry)

    @app.delete("/v1/backlog/{entry_id}")
    def delete_backlog_entry(entry_id: str) -> dict:
        """The entry and everything it holds, and the transcripts of the Claude sessions that worked on it
        (`transcripts_deleted` is None when the agent could not be reached to delete them)."""
        with lock:
            if not db.delete_backlog_entry(conn, entry_id):
                raise HTTPException(status_code=404, detail="no such backlog entry")
            backlog_changed()
        return {"deleted": True, "id": entry_id, "transcripts_deleted": agent_client.forget(paths, backlog_ref(entry_id))}

    def _activity(entry_id: str, **fields) -> None:
        with lock:
            if db.set_backlog_activity(conn, entry_id, **fields):
                backlog_changed()

    # The entry's activity columns of each kind of work: (since, job, error).
    REFINING = ("refining_since", "refining_job", "refine_error")
    PROMPTING = ("prompting_since", "prompting_job", "prompt_error")

    def _ask_claude_for_entry(request: JobRequest, *, write: str, build_prompt, finish, activity: tuple[str, str, str]) -> dict:
        """Read the entry, ask Claude without holding `lock` (a minute or more), then write the answer
        into `write` — unless the entry was deleted meanwhile, which keeps nothing of the answer.
        Meanwhile the entry says Claude is at it, since when and in which Job (`activity`), and
        afterwards why it failed, if it did. A refusal is raised inside its step, so the checklist shows
        which step failed."""
        entry_id = request.params.get("entry_id") or ""
        since, job, error = activity
        try:
            with reported(request):
                with step("read_entry") as handle:
                    with lock:
                        entry = db.get_backlog_entry(conn, entry_id)
                    if entry is None:
                        raise backlog.BacklogError(f"no backlog entry {entry_id!r}")
                    prompt = build_prompt(entry["project_name"], entry)
                    handle.detail = backlog.entry_title(entry)
                _activity(entry_id, **{since: datetime.now(timezone.utc).isoformat(), job: request.job_id, error: None})
                with step("ask_claude") as handle:
                    text = agent_client.query(paths, prompt, job_id=request.job_id, project_id=entry["project_name"],
                                              ref=backlog_ref(entry_id), read_checkout=True)
                    handle.detail = f"{len(text)} characters"
                with step("save") as handle:
                    with lock:
                        saved = db.update_backlog_entry(conn, entry_id, **{write: finish(text, entry)})
                        if saved is not None:
                            db.set_backlog_activity(conn, entry_id, **{since: None, job: None})
                            backlog_changed()
                    if saved is None:
                        raise backlog.BacklogError("the entry was deleted while Claude worked on it; nothing was kept")
                    handle.detail = f"replaced the {write.replace('_', ' ')}"
        except (backlog.BacklogError, agent_client.AgentClientError) as exc:
            _activity(entry_id, **{since: None, job: None, error: str(exc)})
            return {"outcome": "failed", "error": str(exc)}
        return {"outcome": "succeeded", "result": {"entry_id": entry_id, "project": entry["project_name"], "chars": len(saved[write])}}

    @app.post("/v1/jobs/registry.backlog_refine")
    def job_backlog_refine(request: JobRequest) -> dict:
        """Claude writes the refined entry from the primary entry, the current refined entry and the notes,
        reading the project's checkout."""
        return _ask_claude_for_entry(request, write="refined_entry", build_prompt=backlog.refine_prompt, finish=lambda text, entry: text,
                                     activity=REFINING)

    @app.post("/v1/jobs/registry.backlog_task_prompt")
    def job_backlog_task_prompt(request: JobRequest) -> dict:
        """Claude writes the task prompt from the refined entry, reading the project's checkout; the delete
        instruction is appended here."""
        return _ask_claude_for_entry(
            request, write="task_prompt", build_prompt=backlog.task_prompt_prompt,
            finish=lambda text, entry: backlog.with_delete_instruction(text, entry["project_name"], entry["id"]), activity=PROMPTING,
        )

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
