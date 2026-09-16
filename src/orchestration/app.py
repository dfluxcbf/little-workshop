"""Orchestration's Pillar Interface — docs/architecture.md's PI section:
versioned paths, bound to a Unix domain socket, published OpenAPI schema.
The one thing every caller (CLI, MCP, other pillars, pipeline expansion)
goes through to create or inspect Jobs and Pipelines.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from orchestration import db, policy_gate
from orchestration.dispatch import SocketLookup, complete_job, dispatch_ready_jobs, stop_job
from orchestration.models import (
    Job,
    JobStatus,
    JobTemplate,
    Pipeline,
    PipelineRun,
    Schedule,
    ScheduledTask,
)
from orchestration.pipelines import trigger_pipeline
from orchestration.schedule import first_occurrence
from orchestration.scheduler import cancel_task, postpone_task, trigger_task


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


class CreateScheduledTaskRequest(BaseModel):
    name: str
    job_template: JobTemplate
    schedule: Schedule


class EditScheduledTaskRequest(BaseModel):
    name: str | None = None
    job_template: JobTemplate | None = None
    schedule: Schedule | None = None


class PostponeRequest(BaseModel):
    at: datetime


class SetQueueCapacityRequest(BaseModel):
    capacity: int


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
            policy_gate.attach_policy_gate(conn, socket_lookup, job)
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

    @app.post("/v1/jobs/{job_id}/stop")
    def stop(job_id: str) -> dict:
        with lock:
            job = db.get_job(conn, job_id)
            if job is None:
                raise HTTPException(status_code=404, detail="job not found")
            return stop_job(conn, job, socket_lookup)

    @app.post("/v1/jobs/{job_id}/policy_confirm")
    def policy_confirm(job_id: str) -> dict:
        with lock:
            job = db.get_job(conn, job_id)
            if job is None:
                raise HTTPException(status_code=404, detail="job not found")
            result = policy_gate.confirm(conn, job, socket_lookup)
            dispatch_ready_jobs(conn, socket_lookup)
            return result

    @app.post("/v1/jobs/{job_id}/policy_confirm_timeout")
    def policy_confirm_timeout(job_id: str) -> dict:
        with lock:
            job = db.get_job(conn, job_id)
            if job is None:
                raise HTTPException(status_code=404, detail="job not found")
            return policy_gate.confirm_timeout(conn, job)

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

    # -- Scheduled tasks (docs/milestones.md M9) --------------------------

    @app.post("/v1/scheduled_tasks")
    def create_scheduled_task(request: CreateScheduledTaskRequest) -> dict:
        task = ScheduledTask(
            name=request.name, job_template=request.job_template, schedule=request.schedule
        )
        task.next_run_at = first_occurrence(task.schedule, now=datetime.now())
        with lock:
            db.insert_scheduled_task(conn, task)
        return task.model_dump(mode="json")

    @app.get("/v1/scheduled_tasks")
    def list_scheduled_tasks() -> list[dict]:
        with lock:
            return [t.model_dump(mode="json") for t in db.list_scheduled_tasks(conn)]

    @app.get("/v1/scheduled_tasks/{task_id}")
    def get_scheduled_task(task_id: str) -> dict:
        with lock:
            task = db.get_scheduled_task(conn, task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="scheduled task not found")
        return task.model_dump(mode="json")

    @app.patch("/v1/scheduled_tasks/{task_id}")
    def edit_scheduled_task(task_id: str, request: EditScheduledTaskRequest) -> dict:
        with lock:
            task = db.get_scheduled_task(conn, task_id)
            if task is None:
                raise HTTPException(status_code=404, detail="scheduled task not found")
            if request.name is not None:
                task.name = request.name
            if request.job_template is not None:
                task.job_template = request.job_template
            if request.schedule is not None:
                task.schedule = request.schedule
                # A changed schedule invalidates whatever was pending —
                # recompute from now, the same arithmetic `add` uses.
                task.next_run_at = first_occurrence(task.schedule, now=datetime.now())
                task.missed_run_at = None
            db.update_scheduled_task(conn, task)
            return task.model_dump(mode="json")

    @app.delete("/v1/scheduled_tasks/{task_id}")
    def remove_scheduled_task(task_id: str) -> dict:
        with lock:
            task = db.get_scheduled_task(conn, task_id)
            if task is None:
                raise HTTPException(status_code=404, detail="scheduled task not found")
            if task.last_job_id:
                last_job = db.get_job(conn, task.last_job_id)
                if last_job is not None and last_job.status == JobStatus.RUNNING:
                    stop_job(conn, last_job, socket_lookup)
            db.delete_scheduled_task(conn, task_id)
            return {"id": task_id, "removed": True}

    @app.post("/v1/scheduled_tasks/{task_id}/trigger")
    def trigger(task_id: str) -> dict:
        with lock:
            task = db.get_scheduled_task(conn, task_id)
            if task is None:
                raise HTTPException(status_code=404, detail="scheduled task not found")
            job = trigger_task(conn, task, socket_lookup)
            return job.model_dump(mode="json")

    @app.post("/v1/scheduled_tasks/{task_id}/cancel")
    def cancel(task_id: str) -> dict:
        with lock:
            task = db.get_scheduled_task(conn, task_id)
            if task is None:
                raise HTTPException(status_code=404, detail="scheduled task not found")
            return cancel_task(conn, task)

    @app.post("/v1/scheduled_tasks/{task_id}/postpone")
    def postpone(task_id: str, request: PostponeRequest) -> dict:
        with lock:
            task = db.get_scheduled_task(conn, task_id)
            if task is None:
                raise HTTPException(status_code=404, detail="scheduled task not found")
            updated = postpone_task(conn, task, request.at)
            return updated.model_dump(mode="json")

    # -- Queue capacity (the `queue` operation) ---------------------------

    @app.get("/v1/queue_keys/{queue_key}/capacity")
    def get_queue_capacity(queue_key: str) -> dict:
        with lock:
            return {"queue_key": queue_key, "capacity": db.get_queue_capacity(conn, queue_key)}

    @app.post("/v1/queue_keys/{queue_key}/capacity")
    def set_queue_capacity(queue_key: str, request: SetQueueCapacityRequest) -> dict:
        if request.capacity < 1:
            raise HTTPException(status_code=422, detail="capacity must be at least 1")
        with lock:
            db.set_queue_capacity(conn, queue_key, request.capacity)
            dispatch_ready_jobs(conn, socket_lookup)
        return {"queue_key": queue_key, "capacity": request.capacity}

    return app
