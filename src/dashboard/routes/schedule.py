"""Dashboard routes: the Schedule calendar."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from dashboard import calendar, client
from dashboard.app import WEEKDAYS, _job_subjects, _try


def build_router(ctx) -> APIRouter:
    router = APIRouter()
    paths, templates, _render = ctx.paths, ctx.templates, ctx.render

    # ---- Schedule: every job and task on a month calendar (dashboard/calendar.py) ----

    @router.get("/schedule", response_class=HTMLResponse)
    def schedule_page(request: Request, month: str | None = None, show: str = "scheduled"):
        """The scheduled jobs and tasks of a month; `show=all` adds every other job and task that ran."""
        today = date.today()
        first = calendar.month_of(month, today)
        everything = show == "all"
        error = None
        days = calendar.grid_days(first)
        start = datetime.combine(days[0], datetime.min.time()).astimezone()
        end = datetime.combine(days[-1] + timedelta(days=1), datetime.min.time()).astimezone()
        try:
            jobs = client.list_jobs(paths, since=start, until=end, created_by=None if everything else calendar.SCHEDULER, summary=True)
        except client.PillarUnavailable as exc:
            jobs, error = [], str(exc)
        tasks = _try(client.scheduled_tasks, paths, default=[])
        shown = jobs if everything else [j for j in jobs if j.get("created_by") == calendar.SCHEDULER]
        weeks = calendar.month(shown, ctx.task_runs_of(shown), tasks, first, today, scheduled_only=not everything)
        return _render(
            request, "schedule.html", weeks=weeks, totals=calendar.totals(weeks), first=first, today=today, error=error, everything=everything,
            previous=calendar.shift_month(first, -1), following=calendar.shift_month(first, 1),
            weekdays=WEEKDAYS, states=calendar.STATES, nav_active="schedule", live_topics="schedule",
        )

    @router.get("/schedule/item/job/{job_id}", response_class=HTMLResponse)
    def schedule_item_job(request: Request, job_id: str):
        """The details of one job or task of a day (a fragment the day's panel loads when it is expanded)."""
        job = _try(client.get_job, paths, job_id)
        run = _try(client.agent_run_for_job, paths, job_id) if job and job.get("type") == "agent.run_task" else None
        return templates.TemplateResponse(request, "_schedule_item.html", {
            "job": job, "run": run, "task": None, "at": None,
            "subjects": _job_subjects(job, job.get("steps") or [], {}) if job else [],
        })

    @router.get("/schedule/item/scheduled/{task_id}", response_class=HTMLResponse)
    def schedule_item_scheduled(request: Request, task_id: str, at: str = ""):
        task = _try(client.get_scheduled_task, paths, task_id)
        last_job = _try(client.get_job, paths, task["last_job_id"]) if task and task.get("last_job_id") else None
        return templates.TemplateResponse(request, "_schedule_item.html", {
            "job": None, "run": None, "task": task, "at": at.replace("T", " "), "last_job": last_job, "weekdays": WEEKDAYS,
            "state": "skipped" if task and task.get("missed_run_at") and at.startswith((task.get("missed_run_at") or "")[:16]) else "scheduled",
        })

    return router
