"""Orchestration's Pillar Interface — docs/architecture.md's PI section:
versioned paths, bound to a Unix domain socket, published OpenAPI schema.
The one thing every caller (CLI, MCP, other pillars, pipeline expansion)
goes through to create or inspect Jobs and Pipelines.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from common.incident import install_incident_reporting
from common.paths import Paths
from orchestration import changes, db, policy_gate, steps, triggers
from orchestration.dispatch import SocketLookup, complete_job, dispatch_ready_jobs, dispatch_timeout_for, stop_job, wait_for_dispatch
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
from orchestration.queue import is_ready
from orchestration.schedule import first_occurrence
from orchestration.scheduler import cancel_task, postpone_task, trigger_task


MAX_CLEANUP_HOURS = 3650 * 24


# `GET /v1/jobs/<id>/wait`: how long it waits when the caller names no timeout, the most it ever
# waits (under the proxies' own request timeouts), and how often it looks.
WAIT_DEFAULT_SECONDS = 120.0
WAIT_MAX_SECONDS = 300.0
WAIT_POLL_SECONDS = 0.5
WAIT_FOR_STATUSES = (JobStatus.QUEUED, JobStatus.RUNNING)


class JobCleanupRequest(BaseModel):
    older_than_hours: int = Field(ge=1, le=MAX_CLEANUP_HOURS)


class CreateJobRequest(BaseModel):
    # A caller may choose the id, to follow the Job's steps while its creation call is still running.
    id: str | None = None
    type: str
    target_pillar: str
    project_id: str | None = None
    params: dict[str, Any] = {}
    queue_key: str | None = None
    depends_on: list[str] = []
    created_by: str = "unknown"
    origin: str = "unknown"


class CompleteJobRequest(BaseModel):
    succeeded: bool
    result: dict[str, Any] | None = None
    error: str | None = None


class StopJobRequest(BaseModel):
    # The user's own decision, taken on the Dashboard after a warning: settle the Job whatever its pillar says.
    force: bool = False


# A Job in one of these states has settled: a completion reported for it afterwards changes nothing.
SETTLED_STATUSES = (JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.STOPPED)


class StepReportRequest(BaseModel):
    state: str
    detail: str | None = None
    prompt: dict[str, Any] | None = None


class StepAnswerRequest(BaseModel):
    choice: str


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


class CreateTriggerRequest(BaseModel):
    name: str
    on: triggers.TriggerOn
    job_template: JobTemplate
    enabled: bool = True


class EditTriggerRequest(BaseModel):
    enabled: bool


class EventRequest(BaseModel):
    name: str
    project_id: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class SetQueueCapacityRequest(BaseModel):
    capacity: int


def build_app(
    conn: sqlite3.Connection, socket_lookup: SocketLookup, paths: Paths | None = None, lock: threading.Lock | None = None,
) -> FastAPI:
    app = FastAPI(title="little-workshop Orchestration")
    if paths is not None:
        install_incident_reporting(app, paths, pillar="orchestration")
    # FastAPI runs sync endpoint functions in a threadpool, so concurrent
    # requests could otherwise touch the one sqlite3 connection from more
    # than one thread at once — serialize every request's db work here.
    # Never held across a call to another pillar (see dispatch.py), so a
    # minutes-long Job never stalls reads. Shared with the scheduler thread.
    lock = lock or threading.Lock()

    def payload(job: Job) -> dict:
        """The Job with its checklist; call with `lock` held."""
        return {**job.model_dump(mode="json"), "steps": steps.list_steps(conn, job)}

    @app.get("/v1/health")
    def health() -> dict:
        with lock:
            queued = len(db.list_jobs(conn, status=JobStatus.QUEUED))
            running = len(db.list_jobs(conn, status=JobStatus.RUNNING))
        return {"ok": True, "queued_jobs": queued, "running_jobs": running}

    @app.post("/v1/jobs")
    def create_job(request: CreateJobRequest) -> dict:
        fields = request.model_dump()
        if fields["id"] is None:
            del fields["id"]
        job = Job(**fields)
        with lock:
            db.insert_job(conn, job)
            policy_gate.attach_policy_gate(conn, socket_lookup, job)
            waits = not is_ready(conn, db.get_job(conn, job.id))
        if waits:
            # Behind its policy checks (minutes, for a test-suite one) or a
            # busy queue_key: answer now with the queued Job and its id, and
            # pump in the background. Waiting here outlasted every caller's
            # timeout, which each reported as "orchestration daemon
            # unreachable" while the Job went on to run (2026-09-24).
            threading.Thread(target=dispatch_ready_jobs, args=(conn, socket_lookup), kwargs={"lock": lock}, daemon=True).start()
            with lock:
                return payload(db.get_job(conn, job.id))
        dispatch_ready_jobs(conn, socket_lookup, lock=lock)
        # A concurrent request's pump may have claimed this Job; report its outcome, not "running".
        wait_for_dispatch(job.id, dispatch_timeout_for(job.type) + 5)
        with lock:
            return payload(db.get_job(conn, job.id))

    @app.get("/v1/jobs/steps")
    def steps_of_jobs(ids: str) -> dict:
        """The checklists of several Jobs at once ({job id: steps}), for a page listing many Jobs."""
        wanted = [i for i in ids.split(",") if i][:200]
        with lock:
            found = {job_id: db.get_job(conn, job_id) for job_id in wanted}
            return {job_id: steps.list_steps(conn, job) for job_id, job in found.items() if job is not None}

    @app.get("/v1/changes")
    def changes_since(after: int | None = None, boot: str | None = None, timeout: float = 0.0) -> dict:
        """The changes of Jobs, their steps and scheduled tasks numbered after `after`, waiting up to
        `timeout` seconds (at most 30) for the first: the Dashboard's live pages follow this.
        {boot, seq, changes: [{seq, kind, id, type, project_id, status}], reset}."""
        return changes.FEED.since(after, boot=boot, timeout=timeout)

    def _several(value: str | None) -> list[str] | None:
        return [v for v in value.split(",") if v] if value else None

    def _job_filters(status, type, project_id, created_by, since, until, updated_since, ids, q) -> dict:
        statuses = _several(status)
        try:
            for value in statuses or ():
                JobStatus(value)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        return {"statuses": statuses, "types": _several(type), "project_id": project_id, "created_by": created_by,
                "since": since, "until": until, "updated_since": updated_since, "ids": _several(ids), "q": q}

    @app.get("/v1/jobs")
    def list_jobs(
        status: str | None = None, type: str | None = None, project_id: str | None = None, created_by: str | None = None,
        since: datetime | None = None, until: datetime | None = None, updated_since: datetime | None = None,
        ids: str | None = None, q: str | None = None, order: str = "oldest", limit: int | None = Query(None, ge=1),
        view: str = "full",
    ) -> list[dict]:
        """Jobs, oldest first (`order=newest` turns it), at most `limit`. `status`, `type` and `ids` take several,
        comma-separated; `q` is looked for in the type, project, pillar, creator and id; `view=summary` gives
        each Job's brief in place of its full params and result (orchestration/brief.py): what a list shows."""
        filters = _job_filters(status, type, project_id, created_by, since, until, updated_since, ids, q)
        with lock:
            jobs = db.find_jobs(conn, **filters, newest_first=order == "newest", limit=limit, summary=view == "summary")
            return [{**job, "progress": steps.progress(conn, job["id"])} for job in jobs]

    @app.get("/v1/jobs/counts")
    def count_jobs(
        status: str | None = None, type: str | None = None, project_id: str | None = None, created_by: str | None = None,
        since: datetime | None = None, until: datetime | None = None, updated_since: datetime | None = None,
        ids: str | None = None, q: str | None = None,
    ) -> dict[str, int]:
        """{status: how many Jobs} among those the same filters as GET /v1/jobs select."""
        filters = _job_filters(status, type, project_id, created_by, since, until, updated_since, ids, q)
        with lock:
            return db.count_jobs(conn, **filters)

    # Job history cleanup. Paths avoid `/v1/jobs/...` so they are never read as a job id.

    @app.get("/v1/job-cleanup")
    def job_cleanup_preview(hours: str = "12,48,168,672,1344,4032") -> dict:
        """How many finished jobs each age would delete: {"prunable": {"168": 312, ...}} (keys in hours)."""
        try:
            ages = [int(h) for h in hours.split(",") if h.strip()]
        except ValueError:
            raise HTTPException(status_code=400, detail="hours must be comma-separated integers")
        if not ages or any(a < 1 or a > MAX_CLEANUP_HOURS for a in ages):
            raise HTTPException(status_code=400, detail=f"each age must be 1..{MAX_CLEANUP_HOURS} hours")
        now = datetime.now(timezone.utc)
        with lock:
            counts = db.count_prunable_jobs_by_age(conn, [now - timedelta(hours=a) for a in ages])
        return {"prunable": {str(a): n for a, n in zip(ages, counts)}}

    @app.post("/v1/job-cleanup")
    def job_cleanup(request: JobCleanupRequest) -> dict:
        """Delete finished jobs (and their run records) last updated more than
        `older_than_hours` ago. Running, queued and awaiting-confirmation jobs are never
        touched, nor anything they still depend on."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=request.older_than_hours)
        with lock:
            return db.prune_finished_jobs(conn, cutoff)

    @app.get("/v1/jobs/{job_id}")
    def get_job(job_id: str) -> dict:
        with lock:
            job = db.get_job(conn, job_id)
            if job is None:
                raise HTTPException(status_code=404, detail="job not found")
            return payload(job)

    @app.get("/v1/jobs/{job_id}/wait")
    def wait_job(job_id: str, timeout: float = WAIT_DEFAULT_SECONDS) -> dict:
        """The Job as soon as there is something to act on: it settled, it is held for a confirmation,
        or a step asks the user a question. Otherwise the Job as it is once `timeout` seconds passed.
        One call instead of a caller's status polls, each of which costs a Claude session a turn."""
        deadline = time.monotonic() + max(0.0, min(timeout, WAIT_MAX_SECONDS))
        while True:
            with lock:
                job = db.get_job(conn, job_id)
                if job is None:
                    raise HTTPException(status_code=404, detail="job not found")
                body = payload(job)
            asks = any(s.get("state") == "attention" for s in body["steps"])
            if job.status not in WAIT_FOR_STATUSES or asks or time.monotonic() >= deadline:
                return body
            time.sleep(WAIT_POLL_SECONDS)

    @app.post("/v1/jobs/{job_id}/steps/{key}")
    def report_step(job_id: str, key: str, request: StepReportRequest) -> dict:
        """A pillar reports one step of its running Job: running, succeeded, failed, or
        attention (with a `prompt` {question, options: [{id, label}]} for the user)."""
        with lock:
            job = db.get_job(conn, job_id)
            if job is None:
                raise HTTPException(status_code=404, detail="job not found")
            try:
                return steps.report(conn, job, key, request.state, detail=request.detail, prompt=request.prompt)
            except steps.StepError as exc:
                raise HTTPException(status_code=exc.status_code, detail=str(exc))

    @app.post("/v1/jobs/{job_id}/steps/{key}/answer")
    def answer_step(job_id: str, key: str, request: StepAnswerRequest) -> dict:
        """The user's choice for a step awaiting attention; the pillar waiting on it carries on."""
        with lock:
            job = db.get_job(conn, job_id)
            if job is None:
                raise HTTPException(status_code=404, detail="job not found")
            try:
                return steps.answer(conn, job, key, request.choice)
            except steps.StepError as exc:
                raise HTTPException(status_code=exc.status_code, detail=str(exc))

    @app.post("/v1/jobs/{job_id}/complete")
    def complete(job_id: str, request: CompleteJobRequest) -> dict:
        with lock:
            job = db.get_job(conn, job_id)
            if job is None:
                raise HTTPException(status_code=404, detail="job not found")
            if job.status in SETTLED_STATUSES:
                # Stopped (perhaps forced), timed out or cancelled meanwhile: that outcome stands, and what
                # waited on the Job has already been failed accordingly.
                return {**job.model_dump(mode="json"), "ignored": True}
            completed = complete_job(
                conn, job, succeeded=request.succeeded, result=request.result, error=request.error
            )
        dispatch_ready_jobs(conn, socket_lookup, lock=lock)
        return completed.model_dump(mode="json")

    @app.post("/v1/jobs/{job_id}/stop")
    def stop(job_id: str, request: StopJobRequest | None = None) -> dict:
        with lock:
            job = db.get_job(conn, job_id)
            if job is None:
                raise HTTPException(status_code=404, detail="job not found")
        result = stop_job(conn, job, socket_lookup, lock=lock, force=bool(request and request.force))
        dispatch_ready_jobs(conn, socket_lookup, lock=lock)  # a stopped Job frees its queue_key
        return result

    @app.post("/v1/jobs/{job_id}/policy_confirm")
    def policy_confirm(job_id: str) -> dict:
        with lock:
            job = db.get_job(conn, job_id)
            if job is None:
                raise HTTPException(status_code=404, detail="job not found")
        result = policy_gate.confirm(conn, job, socket_lookup, lock=lock)
        dispatch_ready_jobs(conn, socket_lookup, lock=lock)
        return result

    @app.post("/v1/jobs/{job_id}/policy_confirm_timeout")
    def policy_confirm_timeout(job_id: str) -> dict:
        with lock:
            job = db.get_job(conn, job_id)
            if job is None:
                raise HTTPException(status_code=404, detail="job not found")
            result = policy_gate.confirm_timeout(conn, job)
        # A pending_confirmation Job holds its queue_key; once it settles,
        # whatever queued behind it must be dispatched now, not whenever the
        # next unrelated Job happens to arrive (2026-09-24: a finish sat
        # queued with every dependency green until a manual pump).
        dispatch_ready_jobs(conn, socket_lookup, lock=lock)
        return result

    @app.post("/v1/pump")
    def pump() -> list[dict]:
        return [job.model_dump(mode="json") for job in dispatch_ready_jobs(conn, socket_lookup, lock=lock)]

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
        dispatch_ready_jobs(conn, socket_lookup, lock=lock)
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
        return trigger_task(conn, task, socket_lookup, lock=lock).model_dump(mode="json")

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

    # -- triggers: an event starts a Job (docs/pillars/orchestration.md, "Triggers") ----

    @app.post("/v1/triggers")
    def create_trigger(request: CreateTriggerRequest) -> dict:
        trigger = triggers.Trigger(name=request.name, on=request.on, job_template=request.job_template, enabled=request.enabled)
        with lock:
            try:
                triggers.save(conn, trigger)
            except triggers.TriggerError as exc:
                raise HTTPException(status_code=422, detail=str(exc))
        return trigger.model_dump(mode="json")

    @app.get("/v1/triggers")
    def list_triggers() -> list[dict]:
        with lock:
            return [t.model_dump(mode="json") for t in triggers.list_all(conn)]

    @app.post("/v1/triggers/install-defaults")
    def install_default_triggers() -> list[dict]:
        with lock:
            return [t.model_dump(mode="json") for t in triggers.install_defaults(conn)]

    @app.patch("/v1/triggers/{trigger_id}")
    def edit_trigger(trigger_id: str, request: EditTriggerRequest) -> dict:
        with lock:
            trigger = triggers.get(conn, trigger_id)
            if trigger is None:
                raise HTTPException(status_code=404, detail="trigger not found")
            trigger.enabled = request.enabled
            triggers.save(conn, trigger)
            return trigger.model_dump(mode="json")

    @app.delete("/v1/triggers/{trigger_id}")
    def remove_trigger(trigger_id: str) -> dict:
        with lock:
            if not triggers.delete(conn, trigger_id):
                raise HTTPException(status_code=404, detail="trigger not found")
        return {"removed": trigger_id}

    @app.post("/v1/events")
    def post_event(request: EventRequest) -> dict:
        """An event another pillar reports (`claude.session_ended`, `usage.finding_opened`,
        `registry.project_registered`). The Jobs its triggers create are returned."""
        if request.name.startswith("job.") or request.name.startswith("version."):
            raise HTTPException(status_code=422, detail="that event is Orchestration's own: it comes from a Job settling")
        with lock:
            triggers.queue_event(conn, request.name, request.project_id, {**request.data, "project_id": request.project_id})
        fired = [job for job in dispatch_ready_jobs(conn, socket_lookup, lock=lock) if job.created_by.startswith("trigger:")]
        return {"jobs": [job.id for job in fired]}

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
        dispatch_ready_jobs(conn, socket_lookup, lock=lock)
        return {"queue_key": queue_key, "capacity": request.capacity}

    return app
