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
from common.pillar_socket import resolve_pillar_socket
from common.registry_client import ProjectResolutionError, resolve_project
from common.uds_client import client_for_socket
from agent import db
from agent.flow import AgentFlow, AgentFlowError
from agent.query import run_query


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


class JobRequest(BaseModel):
    job_id: str
    project_id: str | None = None
    params: dict[str, Any] = {}


def build_app(paths: Paths, conn: sqlite3.Connection, flow: AgentFlow | None = None) -> FastAPI:
    app = FastAPI(title="little-workshop Agentic Development")
    lock = threading.Lock()
    flow = flow or AgentFlow(paths=paths)

    @app.get("/v1/health")
    def health() -> dict:
        return {"ok": True}

    @app.post("/v1/jobs/agent.run_task")
    def job_run_task(request: JobRequest) -> dict:
        if not request.project_id:
            return {"outcome": "failed", "error": "missing project_id"}
        try:
            project = resolve_project(paths, request.project_id)
        except ProjectResolutionError as exc:
            return {"outcome": "failed", "error": str(exc)}

        params = dict(request.params)
        params.setdefault("job_id", request.job_id)
        project_dir = Path(project["path"])
        repo_url = project.get("repo_url")
        project_id = request.project_id
        job_id = request.job_id
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

    @app.get("/v1/runs/{project_id}")
    def list_runs(project_id: str, status: str | None = None) -> list[dict]:
        with lock:
            return db.list_runs(conn, project_id, status=status)

    @app.get("/v1/runs/{project_id}/{run_id}")
    def get_run(project_id: str, run_id: str) -> dict:
        with lock:
            run = db.get_run(conn, run_id)
        if run is None or run["project_id"] != project_id:
            raise HTTPException(status_code=404, detail="run not found")
        return run

    return app
