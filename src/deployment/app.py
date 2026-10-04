"""Deployment & Servers' Pillar Interface. Deploy/stop follow the
`POST /v1/jobs/<type>` dispatch convention; status/manifest/deployment-
history reads are plain `GET`s; `/v1/rollback-timeout/{project_id}` is a
dedicated (non-Job) endpoint the confirm-gate watchdog calls directly,
so a rollback fires even if Orchestration itself is down. Worktree
provisioning (`/v1/worktrees`) is the shared capability other pillars
call into (M9, M11).
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any, Callable

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from common import redeploy_plan
from common.job_steps import StepReporter, reporting
from common.paths import Paths
from common.pillar_socket import resolve_pillar_socket
from common.uds_client import client_for_socket
from common.incident import install_incident_reporting
from common.projectfiles import ProjectFilesClient
from common.registry_client import ProjectResolutionError, resolve_project_path
from deployment import db, gitutil, worktrees
from deployment.confirm import rollback_guard
from deployment.flow import DeploymentError, DeploymentFlow, SelfRedeployRequiresDispatch
from deployment.gitutil import list_branches
from deployment.manifest import ManifestError, ServerManifest, parse_manifest
from deployment.workshop_repo import workshop_repo_dir

MANIFEST_FILENAME = "manifest.deployment.toml"


class JobRequest(BaseModel):
    job_id: str
    project_id: str | None = None
    params: dict[str, Any] = {}


class LocateScriptRequest(BaseModel):
    script: str


class RollbackTimeoutRequest(BaseModel):
    deploy_id: str


class ProvisionWorktreeRequest(BaseModel):
    project_id: str
    commitish: str = "HEAD"


ResolveFn = Callable[[Paths, str | None], Path]


def _load_manifest(paths: Paths, project_id: str) -> ServerManifest:
    files = ProjectFilesClient(paths, caller_pillar="deployment")
    try:
        text = files.read(project_id, MANIFEST_FILENAME)
    except FileNotFoundError as exc:
        raise DeploymentError(f"no {MANIFEST_FILENAME} for project {project_id!r}") from exc
    try:
        return parse_manifest(text)
    except ManifestError as exc:
        raise DeploymentError(f"invalid {MANIFEST_FILENAME} for {project_id!r}: {exc}") from exc


def core_live_pillars(paths: Paths) -> list[str]:
    """The pillars Core runs (every one it does not report disabled); none when Core is unreachable."""
    try:
        with client_for_socket(paths.core_socket_path, timeout=5.0) as client:
            response = client.get("/v1/status")
        response.raise_for_status()
        return [name for name, info in response.json().get("pillars", {}).items() if info.get("status") != "disabled"]
    except (httpx.HTTPError, OSError, ValueError):
        return []


def create_orchestration_job(paths: Paths, template: dict) -> dict:
    socket_path = resolve_pillar_socket(paths, "orchestration")
    if socket_path is None:
        return {"status": "failed", "error": "Orchestration is unreachable"}
    try:
        with client_for_socket(socket_path, timeout=330.0) as client:
            response = client.post("/v1/jobs", json=template)
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPError, OSError, ValueError) as exc:
        return {"status": "failed", "error": str(exc)}


def build_app(
    paths: Paths, conn: sqlite3.Connection, resolve: ResolveFn = resolve_project_path, flow: DeploymentFlow | None = None,
    *, live_pillars: Callable[[], list[str]] | None = None, create_job: Callable[[dict], object] | None = None,
) -> FastAPI:
    app = FastAPI(title="little-workshop Deployment & Servers")
    install_incident_reporting(app, paths, pillar="deployment")
    lock = threading.Lock()
    flow = flow or DeploymentFlow(paths=paths)
    live_pillars = live_pillars or (lambda: core_live_pillars(paths))
    create_job = create_job or (lambda template: create_orchestration_job(paths, template))

    db_row = conn.execute("PRAGMA database_list").fetchone()
    db_file = db_row[2] if db_row else ""

    def read(fn, *args, **kwargs):
        """Reads must never queue behind `lock`: a deploy, stop or pillar
        redeploy holds it for the whole operation (a cold `bazel run
        //:install` takes minutes), and the Dashboard's server panel would
        otherwise time out and show no server at all for exactly as long as
        one is launching. A short-lived read-only connection (WAL) sidesteps
        it — agent/app.py's same pattern; an in-memory database (tests) has
        no file to reopen, so it falls back to the shared one."""
        if not db_file:
            with lock:
                return fn(conn, *args, **kwargs)
        reader = sqlite3.connect(f"file:{db_file}?mode=ro", uri=True)
        reader.row_factory = conn.row_factory
        try:
            return fn(reader, *args, **kwargs)
        finally:
            reader.close()

    @app.get("/v1/health")
    def health() -> dict:
        return {"ok": True}

    @app.post("/v1/jobs/deployment.deploy")
    def job_deploy(request: JobRequest) -> dict:
        if not request.project_id:
            return {"outcome": "failed", "error": "missing project_id"}
        branch = request.params.get("branch", "dev")
        with lock, reporting(StepReporter(paths, request.job_id)):
            try:
                manifest = _load_manifest(paths, request.project_id)
                project_dir = resolve(paths, request.project_id)
                result = flow.up(conn, request.project_id, manifest, project_dir, branch)
            except (DeploymentError, ProjectResolutionError) as exc:
                db.record_deployment(conn, request.project_id, branch, "?", "?", "failed", str(exc))
                return {"outcome": "failed", "error": str(exc)}
        payload = {"branch": result.branch, "version": result.version, "commit": result.commit, "how": result.how}
        if result.exposure:
            payload["exposure"] = result.exposure
        if result.confirm_message:
            payload["confirm_message"] = result.confirm_message
        if result.cli_note:
            payload["cli_note"] = result.cli_note
        return {"outcome": "succeeded", "result": payload}

    @app.post("/v1/jobs/deployment.stop")
    def job_stop(request: JobRequest) -> dict:
        if not request.project_id:
            return {"outcome": "failed", "error": "missing project_id"}
        with lock:
            try:
                manifest = _load_manifest(paths, request.project_id)
                report = flow.down(request.project_id, manifest)
            except DeploymentError as exc:
                return {"outcome": "failed", "error": str(exc)}
        return {
            "outcome": "succeeded",
            "result": {
                "unit_stopped": report.unit_stopped, "pids_killed": report.pids_killed,
                "tailscale_off": report.tailscale_off, "port_free": report.port_free,
            },
        }

    @app.post("/v1/jobs/deployment.set_manifest")
    def job_set_manifest(request: JobRequest) -> dict:
        """The one write path for a project's own `manifest.deployment.toml`
        — M17 ecosystem-cutover finding: nothing exposed one before this,
        so a real project being cut over had no way to configure its own
        server manifest at all. Validated via `parse_manifest` before
        being written, so a malformed manifest is rejected outright rather
        than silently stored and only discovered at the next real deploy."""
        if not request.project_id:
            return {"outcome": "failed", "error": "missing project_id"}
        text = request.params.get("toml")
        if not text:
            return {"outcome": "failed", "error": "missing params.toml"}
        try:
            manifest = parse_manifest(text)
        except ManifestError as exc:
            return {"outcome": "failed", "error": f"invalid manifest: {exc}"}
        files = ProjectFilesClient(paths, caller_pillar="deployment")
        files.write(request.project_id, MANIFEST_FILENAME, text)
        return {"outcome": "succeeded", "result": {"name": manifest.name, "port": manifest.port}}

    @app.get("/v1/manifest/{project_id}")
    def get_manifest(project_id: str) -> dict:
        try:
            manifest = _load_manifest(paths, project_id)
        except DeploymentError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        return {
            "name": manifest.name, "command": manifest.command, "port": manifest.port, "match": manifest.match,
            "install": manifest.install, "ready_timeout_seconds": manifest.ready_timeout_seconds,
            "stop_timeout_seconds": manifest.stop_timeout_seconds, "confirm_timeout_seconds": manifest.confirm_timeout_seconds,
        }

    @app.post("/v1/jobs/deployment.seed_confirmation")
    def job_seed_confirmation(request: JobRequest) -> dict:
        if not request.project_id:
            return {"outcome": "failed", "error": "missing project_id"}
        with lock:
            try:
                flow.seed_confirmation(conn, request.project_id)
            except DeploymentError as exc:
                return {"outcome": "failed", "error": str(exc)}
        return {"outcome": "succeeded", "result": {"seeded": True}}

    @app.post("/v1/jobs/deployment.redeploy_changed_pillars")
    def job_redeploy_changed_pillars(request: JobRequest) -> dict:
        """Redeploy the pillars whose code differs between what they run and `params.commit` (default
        HEAD), in dependency order: each live pillar is compared with the commit this pillar last
        deployed it at, and one it never deployed is brought in. With `params.base`, every pillar is
        compared with that commit instead. One `deployment.redeploy_pillar` Job per pillar, each
        confirm-gated as any redeploy; nothing is redeployed when no pillar's code changed."""
        repo = workshop_repo_dir()
        commit = gitutil.run(["git", "rev-parse", request.params.get("commit") or "HEAD"], repo)
        if commit.returncode != 0:
            return {"outcome": "failed", "error": f"unknown commit: {commit.stderr.strip()}"}
        sha = commit.stdout.strip()
        base = request.params.get("base")
        compared_with = base or "the commit each pillar runs"

        def changed_since(reference: str, candidates: list[str]) -> list[str] | None:
            diff = gitutil.run(["git", "diff", "--name-only", reference, sha, "--", "src"], repo)
            return None if diff.returncode != 0 else redeploy_plan.changed_pillars(diff.stdout.splitlines(), candidates)

        live = live_pillars()
        if base:
            pillars = changed_since(base, live)
            if pillars is None:
                return {"outcome": "failed", "error": f"could not compare {base} with {sha}"}
        else:
            pillars = []
            for pillar in live:
                deployed = read(db.get_pillar_deployment, pillar)
                # Never deployed by this pillar, or at a commit git no longer knows: bring it in.
                changed = changed_since(deployed["commit_sha"], [pillar]) if deployed else None
                if changed is None or changed:
                    pillars.append(pillar)
            pillars.sort()
        if not pillars:
            return {"outcome": "succeeded", "result": {"commit": sha, "pillars": [], "compared_with": compared_with,
                                                       "note": "no pillar's code changed"}}
        plan = [[{"pillar": p} for p in batch] for batch in redeploy_plan.redeploy_batches(pillars)]
        # The first batch's redeploys run inside their creation call, so the Jobs are created beside this answer.
        threading.Thread(
            target=redeploy_plan.submit_redeploy_batches, args=(create_job, pillars, sha),
            kwargs={"created_by": f"job:{request.job_id}"}, daemon=True,
        ).start()
        return {"outcome": "succeeded", "result": {"commit": sha, "pillars": pillars, "batches": plan, "compared_with": compared_with}}

    @app.post("/v1/jobs/deployment.redeploy_pillar")
    def job_redeploy_pillar(request: JobRequest) -> dict:
        pillar = request.params.get("pillar")
        if not pillar:
            return {"outcome": "failed", "error": "missing params.pillar"}
        commit = request.params.get("commit", "HEAD")
        confirm_timeout = request.params.get("confirm_timeout_seconds")
        with lock, reporting(StepReporter(paths, request.job_id)):
            try:
                result = flow.redeploy_pillar(conn, pillar, workshop_repo_dir(), commit, confirm_timeout_seconds=confirm_timeout)
            except SelfRedeployRequiresDispatch:
                dispatched = flow.dispatch_self_redeploy(
                    pillar, commit, confirm_timeout_seconds=confirm_timeout, job_id=request.job_id
                )
                return {"outcome": "accepted", "result": {"dispatched": dispatched}}
            except (DeploymentError, RuntimeError) as exc:
                db.record_deployment(conn, pillar, commit, "?", "?", "failed", str(exc))
                return {"outcome": "failed", "error": str(exc)}
        payload = {"branch": result.branch, "version": result.version, "commit": result.commit, "how": result.how}
        if result.confirm_message:
            payload["confirm_message"] = result.confirm_message
        if result.cli_note:
            payload["cli_note"] = result.cli_note
        return {"outcome": "succeeded", "result": payload}

    @app.post("/v1/jobs/deployment.confirm")
    def job_confirm(request: JobRequest) -> dict:
        if not request.project_id:
            return {"outcome": "failed", "error": "missing project_id"}
        with lock:
            confirmed = db.confirm(conn, request.project_id)
        if not confirmed:
            return {"outcome": "failed", "error": "nothing pending to confirm"}
        return {"outcome": "succeeded", "result": {"confirmed": True}}

    @app.post("/v1/rollback-timeout/{project_id}")
    def rollback_timeout(project_id: str, request: RollbackTimeoutRequest) -> dict:
        with lock:
            state, reason = rollback_guard(conn, project_id, request.deploy_id)
            if reason is not None:
                return {"rolled_back": False, "message": reason}
            is_pillar = db.get_pillar_deployment(conn, project_id) is not None
            try:
                if is_pillar:
                    try:
                        summary = flow.rollback_pillar_if_unconfirmed(
                            conn, project_id, workshop_repo_dir(), request.deploy_id
                        )
                    except SelfRedeployRequiresDispatch:
                        dispatched = flow.dispatch_self_rollback(project_id, request.deploy_id)
                        return {"rolled_back": False, "message": f"dispatched: {dispatched}"}
                else:
                    manifest = _load_manifest(paths, project_id)
                    project_dir = resolve(paths, project_id)
                    summary = flow.rollback_if_unconfirmed(conn, project_id, manifest, project_dir, request.deploy_id)
            except (DeploymentError, ProjectResolutionError) as exc:
                raise HTTPException(status_code=500, detail=str(exc))
        return {"rolled_back": True, "message": summary}

    @app.get("/v1/status/{project_id}")
    def status(project_id: str) -> dict:
        try:
            manifest = _load_manifest(paths, project_id)
        except DeploymentError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        result = read(flow.status, project_id, manifest)
        return {
            "name": result.name, "unit": result.unit, "pids": result.pids, "listening": result.listening,
            "tailscale": result.tailscale, "branch": result.branch, "version": result.version,
            "external": result.external, "managed_unit": manifest.unit,
        }

    @app.get("/v1/pillars")
    def pillar_deployments() -> dict:
        """The commit each little-workshop pillar was last redeployed from,
        plus this checkout's own HEAD for comparison. A pillar with no row
        was never redeployed through this pillar (still on its initial install)."""
        rows = read(db.list_pillar_deployments)
        try:
            head = gitutil.run(["git", "rev-parse", "HEAD"], workshop_repo_dir()).stdout.strip() or None
            commits = gitutil.recent_commits(workshop_repo_dir(), 10)
        except Exception:
            head, commits = None, []
        return {
            "pillars": {r["pillar"]: {"commit_sha": r["commit_sha"], "updated_at": r["updated_at"]} for r in rows},
            "repo_head": head,
            "commits": commits,
        }

    @app.post("/v1/policy-scripts/locate")
    def locate_policy_script(request: LocateScriptRequest) -> dict:
        """Where in this repository, on GitHub, is the exact policy test
        script that is in use? `found: false` for a script that only lives in
        the Policy database (e.g. one drafted at authoring time)."""
        repo = workshop_repo_dir()
        try:
            hit = gitutil.locate_file_version(repo, request.script, "tools/policy_scripts")
            base = gitutil.github_base_url(repo)
        except Exception:
            hit, base = None, None
        if hit is None or base is None:
            return {"found": False}
        return {"found": True, **hit, "url": f"{base}/blob/{hit['commit']}/{hit['path']}"}

    @app.get("/v1/deployments/{project_id}")
    def deployments(project_id: str) -> list[dict]:
        return read(db.list_deployments, project_id)

    @app.get("/v1/confirmations/{project_id}")
    def confirmation(project_id: str) -> dict:
        state = read(db.get_confirmation, project_id)
        if state is None:
            raise HTTPException(status_code=404, detail="not confirm-gated (no state recorded)")
        return state

    @app.get("/v1/branches/{project_id}")
    def branches(project_id: str) -> list[str]:
        try:
            project_dir = resolve(paths, project_id)
        except ProjectResolutionError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        return list_branches(project_dir)

    # -- worktree provisioning: the shared capability --------------------

    @app.post("/v1/worktrees")
    def provision_worktree(request: ProvisionWorktreeRequest) -> dict:
        try:
            project_dir = resolve(paths, request.project_id)
        except ProjectResolutionError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        worktree_id, path = worktrees.provision(paths, project_dir, request.commitish)
        return {"worktree_id": worktree_id, "path": str(path)}

    @app.delete("/v1/worktrees/{worktree_id}")
    def release_worktree(worktree_id: str, project_id: str) -> dict:
        try:
            project_dir = resolve(paths, project_id)
        except ProjectResolutionError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        worktrees.release(paths, project_dir, worktree_id)
        return {"released": True}

    return app
