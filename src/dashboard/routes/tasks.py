"""Dashboard routes: Claude tasks."""

from __future__ import annotations

import re
from datetime import datetime

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from common.task_queue import run_task_queue_key
from dashboard import client
from dashboard.app import (
    TASK_GROUPS, TASK_JOB_STATUSES_WITHOUT_RUN, WEEKDAYS, _DISMISSABLE_STATUSES, _back, _task_group,
    _task_rows_without_a_run, _try,
)


def build_router(ctx) -> APIRouter:
    router = APIRouter()
    paths, _render = ctx.paths, ctx.render

    # ---- Tasks: Claude on a real branch, under review -------------------

    def _parse_schedule(kind: str, at: str, time_of_day: str, day: list[str], back: str):
        """Shared by New Task's and New Job's own "When" fields."""
        if kind == "once":
            try:
                when = datetime.fromisoformat(at)
            except ValueError:
                return None, _back(back, "Choose the date and time to start it.")
            if when <= datetime.now():
                return None, _back(back, "That time has already passed — choose a time in the future.")
            return {"kind": "once", "at": when.isoformat()}, None
        if kind == "recurring":
            days = sorted({int(d) for d in day if d.isdigit() and 0 <= int(d) <= 6})
            if not days or not re.match(r"^([01]\d|2[0-3]):[0-5]\d$", time_of_day):
                return None, _back(back, "Choose at least one weekday and a time of day.")
            return {"kind": "recurring", "days_of_week": days, "time_of_day": time_of_day}, None
        return None, _back(back, "Choose when it should run.")

    ctx.parse_schedule = _parse_schedule

    def _is_recurring_task(project: str, title: str, job: dict | None) -> bool:
        """A run launched by a weekly schedule comes back by itself; retrying it by hand makes no sense."""
        if not job or job.get("created_by") != "scheduler":
            return False
        return any(
            (t.get("schedule") or {}).get("kind") == "recurring" and (t.get("job_template") or {}).get("project_id") == project
            and ((t.get("job_template") or {}).get("params") or {}).get("title") == title
            for t in _try(client.scheduled_tasks, paths, default=[])
        )

    @router.get("/tasks", response_class=HTMLResponse)
    def tasks(request: Request):
        error = None
        rows: list[dict] = []
        try:
            names = ctx.project_names()
        except client.PillarUnavailable as exc:
            names, error = [], str(exc)
        for name in names:
            rows += _try(client.agent_runs, paths, name, default=[])
        task_jobs = _try(lambda: client.list_jobs(paths, types=["agent.run_task"], status=TASK_JOB_STATUSES_WITHOUT_RUN, summary=True), default=[])
        rows += _task_rows_without_a_run(task_jobs, rows)
        rows.sort(key=lambda r: r.get("created_at") or "", reverse=True)
        task_sections = {key: [r for r in rows if _task_group(r) == key] for key, _ in TASK_GROUPS}
        return _render(request, "tasks.html", runs=rows, task_sections=task_sections, task_groups=TASK_GROUPS, error=error, nav_active="tasks",
                       live_topics="tasks")

    def _task_prefill(backlog: str | None, candidate: str | None) -> tuple[dict, str | None]:
        """What New task opens with: a backlog entry's task prompt (Launch task) or an automation
        candidate's task (Make it a task). ({} and a message, when neither can be read.)"""
        if backlog:
            entry = _try(client.backlog_get, paths, backlog)
            if entry is None:
                return {}, "That backlog entry is no longer in the backlog."
            # The task prompt is written for the task; an entry without one yet gives what it has, and says so.
            if entry["task_prompt"]:
                description, note = entry["task_prompt"], ""
            elif entry["refined_entry"]:
                description, note = entry["refined_entry"], " (no task prompt yet: its refined entry)"
            else:
                description, note = entry["primary_entry"], " (not refined yet: its primary entry)"
            return {"project": entry["project_name"], "title": entry["title"], "description": description, "backlog_id": entry["id"],
                    "source": f"the backlog entry “{entry['title']}”{note}",
                    "source_href": f"/backlog/{entry['project_name']}/{entry['id']}"}, None
        if candidate:
            task = _try(client.usage_candidate_task, paths, candidate)
            if task is None:
                return {}, "That automation candidate is no longer open."
            return {"project": task["project_id"], "title": task["title"], "description": task["description"],
                    "intent": task.get("intent"), "review_mode": task.get("review_mode"), "candidate_id": candidate,
                    "source": "an automation candidate", "source_href": "/usage#candidates"}, None
        return {}, None

    @router.get("/tasks/new", response_class=HTMLResponse)
    def task_new(request: Request, project: str | None = None, backlog: str | None = None, candidate: str | None = None):
        error = None
        try:
            names = ctx.project_names()
        except client.PillarUnavailable as exc:
            names, error = [], str(exc)
        prefill, missing = _task_prefill(backlog, candidate)
        scheduled = [t for t in _try(lambda: client.scheduled_tasks(paths), default=[]) if (t.get("job_template") or {}).get("type") == "agent.run_task"]
        return _render(
            request, "task_new.html", projects=names, selected=prefill.get("project") or project, scheduled=scheduled, weekdays=WEEKDAYS,
            prefill=prefill, error=error, message=missing or request.query_params.get("message"), nav_active="new-task",
        )

    @router.post("/tasks/new")
    def task_create(
        project: str = Form(...), title: str = Form(...), description: str = Form(""),
        intent: str = Form("task"), review_mode: str = Form("manual"), when: str = Form("immediate"),
        kind: str = Form("once"), at: str = Form(""), time_of_day: str = Form(""), day: list[str] = Form(default=[]),
        include_standards: list[str] = Form(default=["1"]), candidate_id: str = Form(""), backlog_id: str = Form(""),
    ):
        params = {
            "title": title, "description": description, "intent": intent, "review_mode": review_mode,
            "include_standards": "1" in include_standards,
        }
        if when == "immediate":
            result = _try(lambda: client.create_job(
                paths, type="agent.run_task", target_pillar="agent", project_id=project,
                params=params, queue_key=run_task_queue_key(project, params), depends_on=[], created_by="dashboard",
            ))
            if result is None or result.get("status") not in ("running", "succeeded", "queued"):
                return _back("/tasks/new", f"Could not start the task: {(result or {}).get('error', 'orchestration unreachable')}")
            if candidate_id:  # made from an automation candidate: its task has started
                _try(client.usage_candidate_task_started, paths, candidate_id, result.get("id"))
            if backlog_id and result.get("id"):  # launched from a backlog entry: the entry remembers it, and stays open
                _try(client.backlog_task_launched, paths, backlog_id, result["id"])
            return _back("/tasks", f"Task started on {project}.")
        schedule, refusal = _parse_schedule(kind, at, time_of_day, day, "/tasks/new")
        if refusal is not None:
            return refusal
        template = {
            "type": "agent.run_task", "target_pillar": "agent", "project_id": project,
            "params": params, "queue_key": run_task_queue_key(project, params), "depends_on": [],
        }
        try:
            client.create_scheduled_task(paths, name=f"{project}: {title}", job_template=template, schedule=schedule)
        except client.PillarUnavailable as exc:
            return _back("/tasks/new", f"Could not schedule the task: {exc}")
        return _back("/tasks/new", f"Task scheduled on {project}.")

    def _schedule_detail(request: Request, task_id: str, *, is_task: bool):
        base, back, back_label, nav = (
            ("/tasks/schedule", "/tasks/new", "New task", "new-task") if is_task
            else ("/jobs/schedule", "/jobs/new", "New job", "new-job")
        )
        try:
            task, error = client.get_scheduled_task(paths, task_id), None
        except client.PillarUnavailable as exc:
            task, error = None, str(exc)
        if task is not None and ((task.get("job_template") or {}).get("type") == "agent.run_task") != is_task:
            task = None  # a job opened under /tasks/schedule, or the reverse: not this page's to show
        last_job = _try(client.get_job, paths, task["last_job_id"]) if task and task.get("last_job_id") else None
        return _render(
            request, "schedule_detail.html", t=task, is_task=is_task, last_job=last_job, error=error,
            detail_base=base, back=back, back_label=back_label, weekdays=WEEKDAYS, nav_active=nav,
        )

    ctx.schedule_detail = _schedule_detail

    @router.get("/tasks/schedule/{task_id}", response_class=HTMLResponse)
    def task_schedule_detail(request: Request, task_id: str):
        return _schedule_detail(request, task_id, is_task=True)

    @router.post("/tasks/schedule/{task_id}/remove")
    def task_schedule_remove(task_id: str):
        try:
            client.remove_scheduled_task(paths, task_id)
        except client.PillarUnavailable as exc:
            return _back("/tasks/new", f"Could not remove it: {exc}")
        return _back("/tasks/new", "Scheduled task removed.")

    @router.get("/tasks/{project}/{run_id}", response_class=HTMLResponse)
    def task_detail(request: Request, project: str, run_id: str):
        run = _try(client.agent_run, paths, project, run_id)
        if run is None:
            return _render(request, "tasks.html", runs=[], error=f"no such task {run_id!r} on {project}", nav_active="tasks")
        job = _try(client.get_job, paths, run["job_id"]) if run.get("job_id") else None
        description = run.get("description") or str(((job or {}).get("params") or {}).get("description") or "")  # older runs: the Job has it
        can_retry = run.get("status") == "failed" and not _is_recurring_task(project, run.get("title"), job)
        can_dismiss = _task_group(run) == "attention" and run.get("status") in _DISMISSABLE_STATUSES
        claude_peek = ctx.peek_of(run["job_id"]) if run.get("job_id") else None
        remote_control = ctx.remote_control_of(run["job_id"], project) if run.get("job_id") else None
        topics = ["tasks", *([f"job:{run['job_id']}"] if run.get("job_id") else []),
                  *([f"peek:{run['job_id']}"] if claude_peek and claude_peek.get("running") else [])]
        return _render(request, "task_detail.html", run=run, job=job, description=description, can_retry=can_retry, can_dismiss=can_dismiss,
                       peek=claude_peek, remote_control=remote_control, stop_refused=request.query_params.get("refused") == "1", nav_active="tasks", live_topics=",".join(topics))

    def _retryable_run(project: str, run_id: str):
        run = _try(client.agent_run, paths, project, run_id)
        if not run or run.get("status") != "failed":
            return None, None
        job = _try(client.get_job, paths, run["job_id"]) if run.get("job_id") else None
        if _is_recurring_task(project, run.get("title"), job):
            return None, None
        params = dict((job or {}).get("params") or {})
        params.update({k: run.get(k) for k in ("title", "intent", "review_mode") if run.get(k)})
        params["description"] = run.get("description") or params.get("description") or ""
        kept = {k: params.get(k, "") for k in ("title", "description", "intent", "review_mode")}
        kept["include_standards"] = params.get("include_standards", True)
        return run, kept

    @router.get("/tasks/{project}/{run_id}/retry", response_class=HTMLResponse)
    def task_retry(request: Request, project: str, run_id: str):
        run, params = _retryable_run(project, run_id)
        if run is None:
            return _back("/tasks", "Only a failed one-off task can be retried.")
        return _render(request, "task_retry.html", run=run, params=params, nav_active="tasks")

    @router.post("/tasks/{project}/{run_id}/retry")
    def task_retry_start(project: str, run_id: str, when: str = Form("immediate"), at: str = Form("")):
        run, params = _retryable_run(project, run_id)
        back = f"/tasks/{project}/{run_id}/retry"
        if run is None:
            return _back("/tasks", "Only a failed one-off task can be retried.")
        if when == "once":
            schedule, refusal = _parse_schedule("once", at, "", [], back)
            if refusal is not None:
                return refusal
            template = {
                "type": "agent.run_task", "target_pillar": "agent", "project_id": project,
                "params": params, "queue_key": run_task_queue_key(project, params), "depends_on": [],
            }
            try:
                client.create_scheduled_task(paths, name=f"{project}: {params['title']}", job_template=template, schedule=schedule)
            except client.PillarUnavailable as exc:
                return _back(back, f"Could not schedule the task: {exc}")
            return _back("/tasks", f"Retry scheduled on {project}.")
        result = _try(lambda: client.create_job(
            paths, type="agent.run_task", target_pillar="agent", project_id=project,
            params=params, queue_key=run_task_queue_key(project, params), depends_on=[], created_by="dashboard",
        ))
        if result is None or result.get("status") not in ("running", "succeeded"):
            return _back(back, f"Could not start the task: {(result or {}).get('error', 'orchestration unreachable')}")
        return _back("/tasks", f"Task started again on {project}.")

    @router.post("/tasks/{project}/{run_id}/{action}")
    def task_settle(project: str, run_id: str, action: str):
        if action not in ("finish", "skip", "dismiss"):
            return _back("/tasks", "Unknown action.")
        result = _try(lambda: client.create_job(
            paths, type=f"agent.{action}_task", target_pillar="agent", project_id=project, params={"run_id": run_id},
            queue_key=project, depends_on=[], created_by="dashboard",
        ))
        ok = result is not None and result.get("status") == "succeeded"
        if action == "dismiss":
            return _back("/tasks", "Moved to the done list." if ok else f"Could not move it: {(result or {}).get('error', 'orchestration unreachable')}")
        return _back(f"/tasks/{project}/{run_id}", f"Task {action}ed." if ok else f"Could not {action}: {(result or {}).get('error', 'orchestration unreachable')}")

    return router
