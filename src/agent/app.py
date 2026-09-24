"""Agentic Development's Pillar Interface. `agent.run_task`/
`agent.finish_task`/`agent.skip_task` follow the Job-dispatch
convention; run history is a direct read, matching every other
pillar's own split between mutations (Jobs) and reads (plain GETs).
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from common.paths import Paths
from common.incident import install_incident_reporting
from common.pillar_socket import resolve_pillar_socket
from common.registry_client import ProjectResolutionError, resolve_project
from common.uds_client import client_for_socket
from common.workspace import WORKSPACE_ID, workspace_dir
from agent import db
from agent.flow import AgentFlow, AgentFlowError
from agent.query import run_query
from agent.session_flow import SessionError, SessionFlow, is_trusted, session_name as session_name_for


def _complete_orchestration_job(paths: Paths, job_id: str, *, succeeded: bool, result: dict | None, error: str | None) -> None:
    """Best-effort: `agent.run_task` runs for up to an hour, so its Job
    handler dispatches the real work to a background thread and returns
    `accepted` immediately — this is what reports the eventual outcome
    back, the same async-Job completion path M5's dispatched self-
    redeploy worker uses for the identical reason (a long-running
    operation that must not block the request that started it)."""
    socket_path = resolve_pillar_socket(paths, "orchestration", timeout=5.0)
    if socket_path is None:
        return
    try:
        with client_for_socket(socket_path, timeout=10.0) as client:
            client.post(f"/v1/jobs/{job_id}/complete", json={"succeeded": succeeded, "result": result, "error": error})
    except (httpx.ConnectError, httpx.TimeoutException, OSError):
        pass


INTERRUPTED_RUN_REASON = "interrupted: the agent pillar restarted while this run was in progress"


def recover_interrupted_runs(paths: Paths, conn: sqlite3.Connection) -> list[dict]:
    """Startup: a run's worker thread dies with the process that started it,
    leaving the run `running` forever and its Job holding its queue_key until
    Orchestration's own timeout. Fail each one and report that back, the same
    way a worker that failed on its own would."""
    runs = db.fail_interrupted_runs(conn, INTERRUPTED_RUN_REASON)
    for run in runs:
        if run["job_id"]:
            _complete_orchestration_job(paths, run["job_id"], succeeded=False, result=None, error=INTERRUPTED_RUN_REASON)
    return runs


class JobRequest(BaseModel):
    job_id: str
    project_id: str | None = None
    params: dict[str, Any] = {}


def build_app(
    paths: Paths, conn: sqlite3.Connection, flow: AgentFlow | None = None, session_flow: SessionFlow | None = None,
) -> FastAPI:
    app = FastAPI(title="little-workshop Agentic Development")
    install_incident_reporting(app, paths, pillar="agent")
    lock = threading.Lock()
    flow = flow or AgentFlow(paths=paths)
    session_flow = session_flow or SessionFlow(paths=paths)

    def read(fn, *args, **kwargs):
        """Reads must never queue behind `lock`: `agent.run_task`'s worker
        holds it for the whole of a run (up to an hour), and the Dashboard's
        run/CI panels would otherwise time out and show this whole pillar as
        unreachable for exactly as long as it is busiest. A short-lived
        read-only connection (WAL) sidesteps it; an in-memory database
        (tests) has no file to reopen, so it falls back to the shared one."""
        row = conn.execute("PRAGMA database_list").fetchone()
        db_file = row[2] if row else ""
        if not db_file:
            with lock:
                return fn(conn, *args, **kwargs)
        reader = sqlite3.connect(f"file:{db_file}?mode=ro", uri=True)
        reader.row_factory = sqlite3.Row
        try:
            return fn(reader, *args, **kwargs)
        finally:
            reader.close()

    @app.get("/v1/health")
    def health() -> dict:
        return {"ok": True}

    def _dispatch_run_task(project_id: str, job_id: str, params: dict) -> dict:
        """Shared by `agent.run_task` and `agent.confirm_incident` — an incident's
        confirmed fix is an ordinary task, started the same way, just with its
        title/description/intent already filled in from the incident."""
        try:
            project = resolve_project(paths, project_id)
        except ProjectResolutionError as exc:
            return {"outcome": "failed", "error": str(exc)}

        params = dict(params)
        params.setdefault("job_id", job_id)
        project_dir = Path(project["path"])
        repo_url = project.get("repo_url")
        run_id = db.new_run_id()

        def worker() -> None:
            with lock:
                result = flow.run_task(conn, project_id, project_dir, repo_url, params, run_id=run_id)
            if result.status == db.FAILED:
                _complete_orchestration_job(paths, job_id, succeeded=False, result=None, error=result.error)
            else:
                _complete_orchestration_job(
                    paths, job_id, succeeded=True, error=None,
                    result={
                        "run_id": result.run_id, "status": result.status, "branch": result.branch,
                        "completion_status": result.completion_status, "pr_url": result.pr_url,
                    },
                )

        threading.Thread(target=worker, daemon=True).start()
        return {"outcome": "accepted", "result": {"dispatched": True, "run_id": run_id}}

    @app.post("/v1/jobs/agent.run_task")
    def job_run_task(request: JobRequest) -> dict:
        if not request.project_id:
            return {"outcome": "failed", "error": "missing project_id"}
        return _dispatch_run_task(request.project_id, request.job_id, request.params)

    @app.post("/v1/jobs/agent.query")
    def job_query(request: JobRequest) -> dict:
        prompt = request.params.get("prompt")
        if not prompt:
            return {"outcome": "failed", "error": "missing params.prompt"}
        timeout = request.params.get("timeout_seconds")
        kwargs = {"runner": flow.runner}
        if timeout:
            kwargs["timeout"] = float(timeout)
        result = run_query(prompt, **kwargs)
        if result.is_error:
            return {"outcome": "failed", "error": result.text or "claude reported an error"}
        return {"outcome": "succeeded", "result": {"text": result.text}}

    @app.post("/v1/jobs/agent.finish_task")
    def job_finish_task(request: JobRequest) -> dict:
        run_id = request.params.get("run_id")
        if not run_id:
            return {"outcome": "failed", "error": "missing params.run_id"}
        run = db.get_run(conn, run_id)
        if run is None:
            return {"outcome": "failed", "error": f"no such run {run_id!r}"}
        try:
            project_dir = Path(resolve_project(paths, run["project_id"])["path"])
        except ProjectResolutionError as exc:
            return {"outcome": "failed", "error": str(exc)}
        with lock:
            try:
                summary = flow.finish_task(conn, run_id, project_dir=project_dir)
            except AgentFlowError as exc:
                return {"outcome": "failed", "error": str(exc)}
        return {"outcome": "succeeded", "result": {"summary": summary}}

    @app.post("/v1/jobs/agent.skip_task")
    def job_skip_task(request: JobRequest) -> dict:
        run_id = request.params.get("run_id")
        if not run_id:
            return {"outcome": "failed", "error": "missing params.run_id"}
        with lock:
            try:
                summary = flow.skip_task(conn, run_id)
            except AgentFlowError as exc:
                return {"outcome": "failed", "error": str(exc)}
        return {"outcome": "succeeded", "result": {"summary": summary}}

    @app.post("/v1/jobs/agent.ci_check")
    def job_ci_check(request: JobRequest) -> dict:
        if not request.project_id:
            return {"outcome": "failed", "error": "missing project_id"}
        try:
            project = resolve_project(paths, request.project_id)
        except ProjectResolutionError as exc:
            return {"outcome": "failed", "error": str(exc)}

        params = dict(request.params)
        repo_url = project.get("repo_url")
        project_id = request.project_id
        job_id = request.job_id

        def worker() -> None:
            with lock:
                try:
                    result = flow.run_ci_check(conn, project_id, repo_url, params)
                except AgentFlowError as exc:
                    _complete_orchestration_job(paths, job_id, succeeded=False, result=None, error=str(exc))
                    return
            _complete_orchestration_job(paths, job_id, succeeded=True, result=result, error=None)

        threading.Thread(target=worker, daemon=True).start()
        return {"outcome": "accepted", "result": {"dispatched": True}}

    @app.get("/v1/runs/{project_id}")
    def list_runs(project_id: str, status: str | None = None) -> list[dict]:
        return read(db.list_runs, project_id, status=status)

    @app.get("/v1/runs/{project_id}/{run_id}")
    def get_run(project_id: str, run_id: str) -> dict:
        run = read(db.get_run, run_id)
        if run is None or run["project_id"] != project_id:
            raise HTTPException(status_code=404, detail="run not found")
        return run

    @app.get("/v1/ci_checks/{project_id}")
    def list_ci_checks(project_id: str) -> list[dict]:
        return read(db.list_ci_checks, project_id)

    @app.get("/v1/ci_checks/{project_id}/latest")
    def latest_ci_check(project_id: str, branch: str | None = None) -> dict:
        check = read(db.latest_ci_check, project_id, branch=branch)
        if check is None:
            raise HTTPException(status_code=404, detail="no CI check recorded yet")
        return check

    @app.post("/v1/jobs/agent.session_start")
    def job_session_start(request: JobRequest) -> dict:
        if not request.project_id:
            return {"outcome": "failed", "error": "missing project_id"}
        try:
            project_dir = workspace_dir() if request.project_id == WORKSPACE_ID else Path(resolve_project(paths, request.project_id)["path"])
        except ProjectResolutionError as exc:
            return {"outcome": "failed", "error": str(exc)}
        try:
            name = session_flow.start(request.project_id, project_dir)
        except SessionError as exc:
            return {"outcome": "failed", "error": str(exc)}
        return {"outcome": "succeeded", "result": {"session_name": name}}

    @app.post("/v1/jobs/agent.session_stop")
    def job_session_stop(request: JobRequest) -> dict:
        if not request.project_id:
            return {"outcome": "failed", "error": "missing project_id"}
        stopped = session_flow.stop(request.project_id)
        return {"outcome": "succeeded", "result": {"stopped": stopped}}

    @app.get("/v1/sessions/{project_id}")
    def session_status(project_id: str) -> dict:
        running = session_flow.is_running(project_id)
        trusted = None  # unknown when the project can't be resolved
        try:
            trusted = is_trusted(workspace_dir() if project_id == WORKSPACE_ID else Path(resolve_project(paths, project_id)["path"]))
        except ProjectResolutionError:
            pass
        return {
            "project_id": project_id, "running": running, "trusted": trusted,
            "session_name": session_name_for(project_id) if running else None,
        }

    # -- incidents: an unhandled exception, awaiting the user's confirmation ----

    @app.post("/v1/incidents")
    def report_incident(body: dict) -> dict:
        """Called directly by `common/incident.py`'s reporter — never a Job (see
        that module's own docstring for why): recording a crash must survive
        Orchestration itself being unreachable."""
        missing = [k for k in ("project_id", "pillar", "exception_type") if not body.get(k)]
        if missing:
            raise HTTPException(status_code=422, detail=f"missing: {', '.join(missing)}")
        incident_id = db.new_incident_id()
        with lock:
            db.create_incident(
                conn, incident_id, project_id=body["project_id"], pillar=body["pillar"],
                context=body.get("context", ""), exception_type=body["exception_type"],
                message=body.get("message", ""), traceback=body.get("traceback", ""),
            )
        return {"id": incident_id}

    @app.get("/v1/incidents")
    def list_incidents(status: str | None = None) -> list[dict]:
        return read(db.list_incidents, status=status)

    @app.get("/v1/incidents/{incident_id}")
    def get_incident(incident_id: str) -> dict:
        incident = read(db.get_incident, incident_id)
        if incident is None:
            raise HTTPException(status_code=404, detail="incident not found")
        return incident

    @app.post("/v1/jobs/agent.confirm_incident")
    def job_confirm_incident(request: JobRequest) -> dict:
        incident_id = request.params.get("incident_id")
        if not incident_id:
            return {"outcome": "failed", "error": "missing params.incident_id"}
        with lock:
            incident = db.get_incident(conn, incident_id)
        if incident is None:
            return {"outcome": "failed", "error": f"no such incident {incident_id!r}"}
        if incident["status"] != "open":
            return {"outcome": "failed", "error": f"incident already {incident['status']}"}
        review_mode = request.params.get("review_mode", "auto_approve")
        title = f"Fix: {incident['exception_type']} in {incident['pillar']}"
        description = (
            f"An unhandled {incident['exception_type']} was raised in the {incident['pillar']} pillar "
            f"({incident['context']}): {incident['message']}\n\nTraceback:\n{incident['traceback']}"
        )
        result = _dispatch_run_task(incident["project_id"], request.job_id, {
            "title": title, "description": description, "intent": "bug", "review_mode": review_mode,
        })
        if result["outcome"] == "accepted":
            with lock:
                db.decide_incident(conn, incident_id, status="confirmed", run_id=result["result"]["run_id"])
        return result

    @app.post("/v1/jobs/agent.dismiss_incident")
    def job_dismiss_incident(request: JobRequest) -> dict:
        incident_id = request.params.get("incident_id")
        if not incident_id:
            return {"outcome": "failed", "error": "missing params.incident_id"}
        with lock:
            incident = db.get_incident(conn, incident_id)
            if incident is None:
                return {"outcome": "failed", "error": f"no such incident {incident_id!r}"}
            if incident["status"] != "open":
                return {"outcome": "failed", "error": f"incident already {incident['status']}"}
            db.decide_incident(conn, incident_id, status="dismissed")
        return {"outcome": "succeeded", "result": {"incident_id": incident_id}}

    return app
