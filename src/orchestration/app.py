"""Orchestration's Pillar Interface — docs/architecture.md's PI section:
versioned paths, bound to a Unix domain socket, published OpenAPI schema.
The one thing every caller (CLI, MCP, other pillars, pipeline expansion)
goes through to create or inspect Jobs and Pipelines.
"""

from __future__ import annotations

import sqlite3
import threading
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from orchestration import db
from orchestration.dispatch import SocketLookup, complete_job, dispatch_ready_jobs
from orchestration.models import Job, JobStatus, Pipeline, PipelineRun
from orchestration.pipelines import trigger_pipeline


class CreateJobRequest(BaseModel):
    type: str
    target_pillar: str
    project_id: str | None = None
    params: dict[str, Any] = {}
    queue_key: str | None = None
    depends_on: list[str] = []
    created_by: str = "unknown"


class CompleteJobRequest(BaseModel):
    succeeded: bool
    result: dict[str, Any] | None = None
    error: str | None = None


class TriggerPipelineRequest(BaseModel):
    project_id: str | None = None
    created_by: str = "unknown"
    version: int | None = None


def build_app(conn: sqlite3.Connection, socket_lookup: SocketLookup) -> FastAPI:
    app = FastAPI(title="little-workshop Orchestration")
    # FastAPI runs sync endpoint functions in a threadpool, so concurrent
    # requests could otherwise touch the one sqlite3 connection from more
    # than one thread at once — serialize every request's db work here.
    lock = threading.Lock()

    @app.get("/v1/health")
    def health() -> dict:
        with lock:
            queued = len(db.list_jobs(conn, status=JobStatus.QUEUED))
            running = len(db.list_jobs(conn, status=JobStatus.RUNNING))
        return {"ok": True, "queued_jobs": queued, "running_jobs": running}

    @app.post("/v1/jobs")
    def create_job(request: CreateJobRequest) -> dict:
        job = Job(**request.model_dump())
        with lock:
            db.insert_job(conn, job)
            dispatch_ready_jobs(conn, socket_lookup)
            return db.get_job(conn, job.id).model_dump(mode="json")

    @app.get("/v1/jobs")
    def list_jobs(status: str | None = None) -> list[dict]:
        status_enum = JobStatus(status) if status else None
        with lock:
            return [job.model_dump(mode="json") for job in db.list_jobs(conn, status=status_enum)]

    @app.get("/v1/jobs/{job_id}")
    def get_job(job_id: str) -> dict:
        with lock:
            job = db.get_job(conn, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        return job.model_dump(mode="json")

    @app.post("/v1/jobs/{job_id}/complete")
    def complete(job_id: str, request: CompleteJobRequest) -> dict:
        with lock:
            job = db.get_job(conn, job_id)
            if job is None:
                raise HTTPException(status_code=404, detail="job not found")
            completed = complete_job(
                conn, job, succeeded=request.succeeded, result=request.result, error=request.error
            )
            dispatch_ready_jobs(conn, socket_lookup)
            return completed.model_dump(mode="json")

    @app.post("/v1/pump")
    def pump() -> list[dict]:
        with lock:
            return [job.model_dump(mode="json") for job in dispatch_ready_jobs(conn, socket_lookup)]

    @app.post("/v1/pipelines")
    def save_pipeline(pipeline: Pipeline) -> dict:
        with lock:
            db.save_pipeline(conn, pipeline)
        return pipeline.model_dump(mode="json")

    @app.get("/v1/pipelines/{name}")
    def get_pipeline(name: str, version: int | None = None) -> dict:
        with lock:
            pipeline = db.get_pipeline(conn, name, version=version)
        if pipeline is None:
            raise HTTPException(status_code=404, detail="pipeline not found")
        return pipeline.model_dump(mode="json")

    @app.post("/v1/pipelines/{name}/trigger")
    def trigger(name: str, request: TriggerPipelineRequest) -> dict:
        with lock:
            pipeline = db.get_pipeline(conn, name, version=request.version)
            if pipeline is None:
                raise HTTPException(status_code=404, detail="pipeline not found")
            run: PipelineRun = trigger_pipeline(
                conn, pipeline, project_id=request.project_id, created_by=request.created_by
            )
            dispatch_ready_jobs(conn, socket_lookup)
            return run.model_dump(mode="json")

    @app.get("/v1/pipeline_runs/{run_id}")
    def get_pipeline_run(run_id: str) -> dict:
        with lock:
            run = db.get_pipeline_run(conn, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="pipeline run not found")
        return run.model_dump(mode="json")

    return app
