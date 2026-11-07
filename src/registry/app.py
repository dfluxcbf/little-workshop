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
from common.job_steps import StepReporter, done, reporting, step
from common.paths import Paths
from common.task_queue import run_task_queue_key
from common.incident import install_incident_reporting
from common.pillar_socket import resolve_pillar_socket
from common.uds_client import client_for_socket
from registry import agent_client, backlog, claude_sync, db, github, gitops, toolkit, version_client
from registry.bootstrap import BootstrapError, bootstrap_project
from registry.assets_config import AssetsConfigError, configure_assets
from registry.connect_github import ConnectGitHubError, connect_github
from registry.import_ import ProjectImportError, import_project
from registry.models import GOVERNANCE_LEVELS, BootstrapParams, ConnectGitHubParams, ImportParams, ProjectSet, ProjectStatus, Ticket
from registry.removal import ProjectNotFoundError, archive_project, remove_project, restore_project


class JobRequest(BaseModel):
    job_id: str
    project_id: str | None = None
    params: dict[str, Any] = {}


class RegenerateClaudeFilesRequest(BaseModel):
    checkout: str
    settings_sides: list[dict] = []


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
    status: str | None = None  # open | done | dropped (REQ-000000524)
    priority: str | None = None  # high | normal | low


class BacklogTaskRequest(BaseModel):
    job_id: str


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


def submit_job(paths: Paths, template: dict, *, timeout: float = 60.0) -> dict:
    """Ask Orchestration for a Job and return it as it settled (or as it was accepted, for a long one)."""
    socket_path = resolve_pillar_socket(paths, "orchestration")
    if socket_path is None:
        return {"status": "failed", "error": "Orchestration is unreachable"}
    try:
        with client_for_socket(socket_path, timeout=timeout) as client:
            response = client.post("/v1/jobs", json={"depends_on": [], "created_by": "registry", **template})
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPError, OSError, ValueError) as exc:
        return {"status": "failed", "error": str(exc)}


def start_improvement_branch(paths: Paths, project_id: str, name: str) -> dict:
    """Ask Orchestration for a `version.improvement_start` Job and return it as it settled."""
    return submit_job(paths, {"type": "version.improvement_start", "target_pillar": "version", "project_id": project_id,
                              "params": {"name": name, "issue": None, "force": False}, "queue_key": project_id})


PREFLIGHT_TIMEOUT_SECONDS = 900.0


def run_preflight(paths: Paths, project_id: str, gate_point: str = "branch_finish") -> dict:
    """The policies gating `gate_point`, run now for the project (Policy & Compliance's evaluate; nothing is
    held): {"blocking", "results": [{topic, outcome, detail}]} or {"error"}."""
    socket_path = resolve_pillar_socket(paths, "policy")
    if socket_path is None:
        return {"error": "Policy & Compliance is unreachable"}
    try:
        with client_for_socket(socket_path, timeout=PREFLIGHT_TIMEOUT_SECONDS) as client:
            response = client.post("/v1/policies/evaluate", json={"project_id": project_id, "gate_point": gate_point, "origin": "automation"})
        response.raise_for_status()
        body = response.json()
    except (httpx.HTTPError, OSError, ValueError) as exc:
        return {"error": str(exc)}
    return {"gate_point": gate_point, "blocking": body.get("blocking") or [],
            "results": [{key: r.get(key) for key in ("topic", "outcome", "detail")} for r in body.get("results") or []]}


def after_import(paths: Paths, project_id: str) -> tuple[dict, dict]:
    """A freshly imported project's Claude files are brought up to date (a `registry.sync_claude_files` Job,
    writing on an improvement branch when they differ) and a branch_finish preflight says what its policies
    would hold (REQ-000000520)."""
    with step("sync_claude_files") as handle:
        job = submit_job(paths, {"type": "registry.sync_claude_files", "target_pillar": "registry", "project_id": project_id,
                                 "params": {"write": True}, "queue_key": project_id}, timeout=300.0)
        reports = ((job.get("result") or {}).get("projects") or [{}])
        sync = {"job_id": job.get("id"), "status": job.get("status"), "error": job.get("error"), **{
            key: reports[0].get(key) for key in ("changed", "written", "branch", "detail") if key in reports[0]}}
        handle.detail = sync.get("error") or sync.get("detail") or sync.get("status")
    with step("preflight") as handle:
        preflight = run_preflight(paths, project_id)
        handle.detail = preflight.get("error") or (
            f"would hold: {', '.join(preflight['blocking'])}" if preflight["blocking"] else f"{len(preflight['results'])} policy(ies), none would hold")
    return sync, preflight


INTERVIEW_INTENT = "interview"


def interview_task(project_id: str) -> dict:
    """The `agent.run_task` Job that interviews the owner of a new project (REQ-000000519): manual review, so
    its branch with `docs/design.md` waits for the owner's finish."""
    params = {
        "title": f"Interview the owner about {project_id}", "intent": INTERVIEW_INTENT, "review_mode": "manual",
        "description": f"{project_id} was just created. Interview its owner and write docs/design.md from the answers.",
        "include_standards": False,
    }
    return {"type": "agent.run_task", "target_pillar": "agent", "project_id": project_id, "params": params,
            "queue_key": run_task_queue_key(project_id, params)}


def fetch_usage_findings(paths: Paths, project_id: str) -> list[dict]:
    """The project's open usage findings, or none when the Usage pillar is unreachable (recommendations only)."""
    socket_path = resolve_pillar_socket(paths, "usage")
    if socket_path is None:
        return []
    try:
        with client_for_socket(socket_path, timeout=5.0) as client:
            response = client.get("/v1/findings", params={"project": project_id})
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPError, OSError, ValueError):
        return []


def build_app(conn: sqlite3.Connection, paths: Paths, *, start_branch=None, fetcher: toolkit.Fetcher | None = None,
              runner=None, usage_findings=None) -> FastAPI:
    app = FastAPI(title="little-workshop Registry & Portfolio")
    install_incident_reporting(app, paths, pillar="registry")
    lock = threading.Lock()
    start_branch = start_branch or (lambda project_id, name: start_improvement_branch(paths, project_id, name))
    fetcher = fetcher or toolkit.GhFetcher()
    runner = runner or toolkit.DEFAULT_RUNNER
    usage_findings = usage_findings or (lambda project_id: fetch_usage_findings(paths, project_id))

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
        if bootstrap_params.interview:
            with reported(request):
                with step("start_interview") as handle:
                    job = submit_job(paths, interview_task(result.name))
                    if job.get("status") in ("failed", "cancelled", None) or not job.get("id"):
                        result.interview_error = job.get("error") or "the interview task did not start"
                        handle.detail = f"not started: {result.interview_error}"
                    else:
                        result.interview_job_id = job["id"]
                        handle.detail = f"task job {job['id'][:8]}: answer its questions in the Claude app"
        else:
            with reported(request):
                done("start_interview", "not requested")
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
        with reported(request):
            result.sync, result.preflight = after_import(paths, result.name)
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

    @app.post("/v1/jobs/project.set_governance")
    def job_set_governance(request: JobRequest) -> dict:
        """Params: `governance`, `light` or `full` (REQ-000000495): how much requirement work little-workshop asks
        of the project. The policies read it at each gate; the generated Claude files follow at the next sync."""
        governance = request.params.get("governance")
        if not request.project_id:
            return {"outcome": "failed", "error": "missing project name"}
        if governance not in GOVERNANCE_LEVELS:
            return {"outcome": "failed", "error": f"'governance' must be one of {', '.join(GOVERNANCE_LEVELS)}"}
        with reported(request), lock:
            if db.get_project(conn, request.project_id) is None:
                return {"outcome": "failed", "error": f"project {request.project_id!r} not found"}
            db.update_project_governance(conn, request.project_id, governance)
            project = db.get_project(conn, request.project_id)
        return {"outcome": "succeeded", "result": project.model_dump(mode="json")}

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

    @app.post("/v1/jobs/project.archive")
    def job_archive(request: JobRequest) -> dict:
        """Drops a project out of the active list (Dashboard home, `status=active` queries) without
        touching its registry rows, files directory or git checkout — the reversible counterpart to
        `project.remove`, for a project whose code already lives on a remote."""
        name = request.params.get("name") or request.project_id
        if not name:
            return {"outcome": "failed", "error": "missing project name"}
        with reported(request), lock:
            try:
                archive_project(conn, name)
            except ProjectNotFoundError:
                return {"outcome": "failed", "error": f"project {name!r} not found"}
        return {"outcome": "succeeded", "result": {"name": name}}

    @app.post("/v1/jobs/project.restore")
    def job_restore(request: JobRequest) -> dict:
        name = request.params.get("name") or request.project_id
        if not name:
            return {"outcome": "failed", "error": "missing project name"}
        with reported(request), lock:
            try:
                restore_project(conn, name)
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
                reports.append(claude_sync.sync_on_branch(checkout, project.name, start_branch, governance=project.governance))
            else:
                report = claude_sync.pending(checkout, project.name, governance=project.governance)
                reports.append({"project_id": project.name, "changed": report["changed"], "note": report["note"], "written": False,
                                "detail": "in sync" if not report["changed"] else f"{len(report['changed'])} file(s) differ"})
        return {"outcome": "succeeded", "result": {"projects": reports, "differing": sum(1 for r in reports if r["changed"])}}

    @app.post("/v1/projects/{name}/claude_files/regenerated")
    def regenerated_claude_files(name: str, request: RegenerateClaudeFilesRequest) -> dict:
        """The generated Claude files a checkout of `name` should hold, for the Version pillar to settle a
        rebase that stopped on them (docs/pillars/registry_portfolio.md, "Claude files sync")."""
        checkout = Path(request.checkout)
        if not checkout.is_dir():
            raise HTTPException(status_code=404, detail=f"checkout {request.checkout!r} not found")
        with lock:
            project = db.get_project(conn, name)
        try:
            return claude_sync.regenerated(checkout, name, request.settings_sides,
                                           governance=project.governance if project is not None else None)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

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
    def list_backlog(project_name: str | None = None, status: str | None = None) -> list[dict]:
        """Highest priority first; `status` (open, done, dropped) filters."""
        with lock:
            return [entry_view(e) for e in db.list_backlog_entries(conn, project_name=project_name, status=status)]

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
        state = {key: fields.pop(key) for key in ("status", "priority") if key in fields}
        if "primary_entry" in fields and not fields["primary_entry"].strip():
            raise HTTPException(status_code=422, detail="the primary entry cannot be empty")
        with lock:
            try:
                entry = db.set_backlog_state(conn, entry_id, **state) if state else None
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc))
            entry = db.update_backlog_entry(conn, entry_id, **fields) if fields else db.get_backlog_entry(conn, entry_id)
            if entry is None:
                raise HTTPException(status_code=404, detail="no such backlog entry")
            backlog_changed()
        return entry_view(entry)

    @app.post("/v1/backlog/{entry_id}/task")
    def backlog_task_launched(entry_id: str, request: BacklogTaskRequest) -> dict:
        """The task launched from the entry (its Job), so the entry can offer "mark done" once that task's
        run succeeded. The entry stays open: only the owner closes it (REQ-000000524)."""
        with lock:
            if not db.set_backlog_activity(conn, entry_id, task_job=request.job_id):
                raise HTTPException(status_code=404, detail="no such backlog entry")
            backlog_changed()
            return entry_view(db.get_backlog_entry(conn, entry_id))

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
        """Claude writes the task prompt from the refined entry, reading the project's checkout. The task never
        closes the entry: the owner marks it done (REQ-000000524)."""
        return _ask_claude_for_entry(
            request, write="task_prompt", build_prompt=backlog.task_prompt_prompt, finish=lambda text, entry: text.strip(),
            activity=PROMPTING,
        )

    # -- toolkit: public skills, agents, commands and plugins per project (registry/toolkit.py) ------------

    def _pin(repo: str) -> dict:
        """Register `repo` (or re-pin it) at its default branch's newest commit, with the items found there."""
        meta = fetcher.repo(toolkit.check_repo(repo))
        sha = fetcher.head(repo, meta["default_branch"])
        items = toolkit.discover(fetcher, repo, sha)
        with lock:
            return db.save_toolkit_source(conn, repo, sha=sha, licence=meta["licence"], default_branch=meta["default_branch"], items=items)

    def _source(repo: str) -> dict:
        with lock:
            source = db.get_toolkit_source(conn, repo)
        if source is None:
            raise toolkit.ToolkitError(f"{repo} is not a registered toolkit source (add it with registry.toolkit_add_source)")
        return source

    def _checkout(project_id: str | None) -> Path:
        with lock:
            project = db.get_project(conn, project_id or "")
        if project is None or not Path(project.path).is_dir():
            raise toolkit.ToolkitError(f"project {project_id!r} not found, or its checkout is missing")
        return Path(project.path)

    def _on_branch(request: JobRequest, label: str, what: str, write_files) -> dict:
        checkout = _checkout(request.project_id)
        message = f"impr (-1): {label}\n\n{what}, by little-workshop's toolkit. REQ-NONE."
        with step("write") as handle:
            result = claude_sync.commit_on_branch(checkout, request.project_id, start_branch, label, message, write_files)
            if not result["written"]:
                raise toolkit.ToolkitError(result["detail"])
            handle.detail = result["detail"]
        return result

    def _toolkit_job(request: JobRequest, work) -> dict:
        try:
            with reported(request):
                result = work()
        except toolkit.ToolkitError as exc:
            return {"outcome": "failed", "error": str(exc)}
        return {"outcome": "succeeded", "result": result}

    @app.post("/v1/jobs/registry.normalize_eol")
    def job_normalize_eol(request: JobRequest) -> dict:
        """Line endings settled once, on an improvement branch of its own (REQ-000000511): `core.autocrlf=false`
        for a Windows-hosted checkout, `* text=auto eol=lf` first in `.gitattributes` (a Unity project keeps its
        template's rules) and every tracked file renormalized, so both gits stop reporting line-ending noise."""
        def work() -> dict:
            checkout = _checkout(request.project_id)

            def write_files() -> list[str]:
                gitops.pin_autocrlf(checkout)
                written = []
                if claude_sync.detect_template(checkout) != "unity":
                    path = checkout / ".gitattributes"
                    attributes = gitops.eol_gitattributes(path.read_text(encoding="utf-8") if path.is_file() else None)
                    if attributes is not None:
                        path.write_text(attributes, encoding="utf-8")
                        written.append(".gitattributes")
                try:
                    gitops.run(["git", "add", "--renormalize", "."], cwd=checkout)
                    staged = gitops.run(["git", "diff", "--cached", "--name-only", "-z"], cwd=checkout).stdout.split("\0")
                except subprocess.CalledProcessError as exc:
                    raise toolkit.ToolkitError(f"renormalizing failed: {(exc.stderr or exc.stdout or '').strip()}") from exc
                return sorted({*written, *(p for p in staged if p)})

            return _on_branch(request, "normalize-eol", "Line endings renormalized to LF", write_files)

        return _toolkit_job(request, work)

    @app.post("/v1/jobs/registry.toolkit_add_source")
    def job_toolkit_add_source(request: JobRequest) -> dict:
        """Params: `repo` (owner/repo), or `seed: true` for the default sources not registered yet. Each is pinned
        to its newest commit; nothing is installed anywhere."""
        def work() -> dict:
            if request.params.get("seed") is True:
                with lock:
                    have = {s["repo"] for s in db.list_toolkit_sources(conn)}
                repos = [repo for repo in toolkit.DEFAULT_SOURCES if repo not in have]
            else:
                repos = [request.params.get("repo") or ""]
            added = []
            with step("pin") as handle:
                for repo in repos:
                    source = _pin(repo)
                    added.append({"repo": repo, "sha": source["sha"], "licence": source["licence"], "items": len(source["items"])})
                handle.detail = ", ".join(f"{a['repo']} at {a['sha'][:12]} ({a['items']} items)" for a in added) or "nothing to add"
            return {"added": added}
        return _toolkit_job(request, work)

    @app.post("/v1/jobs/registry.toolkit_install")
    def job_toolkit_install(request: JobRequest) -> dict:
        """Params: `repo`, `items` (ids or names), `confirm_code` (required when an item runs code). Committed on
        `improvement/toolkit-<item>` in the project's checkout, left for the owner to finish."""
        def work() -> dict:
            with step("check") as handle:
                source = _source(request.params.get("repo") or "")
                items = toolkit.pick(source, list(request.params.get("items") or []))
                if not items:
                    raise toolkit.ToolkitError("name the items to install")
                code = toolkit.code_files(items)
                if code and request.params.get("confirm_code") is not True:  # refused before any branch is started
                    raise toolkit.ToolkitError("these items run code; install them with confirm_code after reading: " + "; ".join(code))
                checkout = _checkout(request.project_id)
                names = [i["id"] for i in items]
                handle.detail = f"{', '.join(names)} at {source['sha'][:12]} ({source['licence']})"
            return {**_on_branch(request, toolkit.branch_name(source["repo"], names), f"Install {', '.join(names)} from {source['repo']}",
                                 lambda: toolkit.install(checkout, fetcher, source, items, confirm_code=True, runner=runner)),
                    "installed": names, "sha": source["sha"], "licence": source["licence"]}
        return _toolkit_job(request, work)

    @app.post("/v1/jobs/registry.toolkit_remove")
    def job_toolkit_remove(request: JobRequest) -> dict:
        """Params: `repo`, `items` (none: everything from it). Deletes exactly the files the lock records."""
        def work() -> dict:
            repo = request.params.get("repo") or ""
            names = list(request.params.get("items") or []) or None
            checkout = _checkout(request.project_id)
            if repo not in toolkit.read_lock(checkout)["sources"]:
                raise toolkit.ToolkitError(f"nothing from {repo} is installed in {request.project_id}")
            label = "toolkit-remove-" + toolkit.branch_name(repo, names or []).removeprefix("toolkit-")
            return {**_on_branch(request, label, f"Remove {', '.join(names or ['everything'])} of {repo}",
                                 lambda: toolkit.remove(checkout, repo, names, runner=runner)), "removed": names or "all"}
        return _toolkit_job(request, work)

    @app.post("/v1/jobs/registry.toolkit_update")
    def job_toolkit_update(request: JobRequest) -> dict:
        """Params: `repo`, `confirm_code`. Re-pins the source to its newest commit and lists the files that changed;
        with a project, brings that project's items to the new pin on a branch (the diff then from the project's pin)."""
        def work() -> dict:
            old = _source(request.params.get("repo") or "")
            with step("pin") as handle:
                source = _pin(old["repo"])
                diff = fetcher.changed(source["repo"], old["sha"], source["sha"]) if old["sha"] != source["sha"] else []
                handle.detail = f"{old['sha'][:12]} -> {source['sha'][:12]}, {len(diff)} files changed"
            result = {"repo": source["repo"], "from": old["sha"], "to": source["sha"], "changed": diff}
            if request.project_id:
                checkout = _checkout(request.project_id)
                entry = toolkit.read_lock(checkout)["sources"].get(source["repo"])
                if entry is None or entry.get("sha") == source["sha"]:
                    return {**result, "project": "up to date"}
                result.update({"from": entry["sha"], "changed": fetcher.changed(source["repo"], entry["sha"], source["sha"])})
                code = toolkit.update_plan(checkout, fetcher, source)["code"]
                if code and request.params.get("confirm_code") is not True:  # refused before any branch is started
                    raise toolkit.ToolkitError("the update changes code; run it with confirm_code after reading: " + "; ".join(code))
                summary: dict = {}

                def write_files() -> list[str]:
                    summary.update(toolkit.refresh(checkout, fetcher, source, confirm_code=True, runner=runner))
                    return summary["paths"]
                label = "toolkit-update-" + source["repo"].split("/", 1)[1].lower()
                result.update(_on_branch(request, label, f"Update {source['repo']} to {source['sha'][:12]}", write_files),
                              project_changed=summary.get("changed", []))
            return result
        return _toolkit_job(request, work)

    @app.get("/v1/toolkit/sources")
    def toolkit_sources() -> list[dict]:
        with lock:
            return db.list_toolkit_sources(conn)

    @app.get("/v1/toolkit/items")
    def toolkit_items(repo: str) -> list[dict]:
        with lock:
            source = db.get_toolkit_source(conn, repo)
        if source is None:
            raise HTTPException(status_code=404, detail=f"{repo} is not a registered toolkit source")
        return source["items"]

    @app.get("/v1/toolkit/status/{name}")
    def toolkit_status(name: str) -> dict:
        """What the project's lock records, and the registered items recommended for it (its kind, its usage
        findings), never installed on their own."""
        try:
            checkout = _checkout(name)
        except toolkit.ToolkitError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        installed = toolkit.status(checkout)
        has_ui = (paths.project_dir(name) / "manifest.deployment.toml").is_file() or (checkout / "tests" / "ui").is_dir()
        with lock:
            sources = db.list_toolkit_sources(conn)
        kinds = toolkit.project_kind(checkout, has_ui)
        return {"project_id": name, "kinds": sorted(kinds), "installed": installed,
                "recommended": toolkit.recommend(kinds, usage_findings(name), installed, sources)}

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
