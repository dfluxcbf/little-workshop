"""The Schedule page's month: the scheduled jobs and tasks on each day they ran, run or will run.

What ran or runs comes from Orchestration's Job history — by default only the runs of schedules (Jobs
the scheduler created), or every Job when asked — so a Job deleted from the history is gone from the
calendar too, and one cancelled before it ran never appears; what will run comes from the scheduled
tasks, a weekly one on each of its days from its next run on. Each item is a task (`agent.run_task`)
or a job, in one of five states: succeeded, failed, running, skipped (stopped, or a run the user
skipped) and scheduled (queued, or still to come). A day shows one symbol per kind and state, with
how many of its items it stands for. Times are the host's local time, as schedules are."""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, time, timedelta

STATES = ("succeeded", "failed", "running", "skipped", "scheduled")
KINDS = ("task", "job")
TASK_TYPE = "agent.run_task"
SCHEDULER = "scheduler"  # a Job's `created_by` when a scheduled task created it

_RUN_STATE = {"running": "running", "awaiting_finish": "succeeded", "finished": "succeeded", "failed": "failed",
              "skipped": "skipped", "stopped": "skipped", "cancelled": "skipped"}
_JOB_STATE = {"running": "running", "queued": "scheduled", "pending_confirmation": "scheduled", "succeeded": "succeeded",
              "failed": "failed", "stopped": "skipped"}


def month_of(text: str | None, today: date) -> date:
    """The first day of the month `text` ("2026-10") names, or of today's month."""
    try:
        year, month = (int(part) for part in (text or "").split("-"))
        return date(year, month, 1)
    except ValueError:
        return today.replace(day=1)


def shift_month(first: date, months: int) -> date:
    index = first.year * 12 + first.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


def grid_days(first: date) -> list[date]:
    """The days a month grid shows: whole weeks, Monday to Sunday, around the month."""
    start = first - timedelta(days=first.weekday())
    last = shift_month(first, 1) - timedelta(days=1)
    end = last + timedelta(days=6 - last.weekday())
    return [start + timedelta(days=n) for n in range((end - start).days + 1)]


def _local(value: str | None) -> datetime | None:
    """A pillar's UTC timestamp as the host's local time, without its zone (schedules are local)."""
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    return moment.astimezone().replace(tzinfo=None) if moment.tzinfo else moment


def _naive(value: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(value) if value else None
    except ValueError:
        return None


def item_state(job: dict, run: dict | None = None) -> str:
    """A task's run says more than its Job (the Job succeeds once the run awaits review)."""
    if run is not None and run.get("status") in _RUN_STATE:
        return _RUN_STATE[run["status"]]
    return _JOB_STATE.get(job.get("status") or "", "scheduled")


def job_items(jobs: list[dict], runs_by_job: dict[str, dict], start: date, end: date, *, scheduled_only: bool = True) -> list[dict]:
    """The Jobs created between `start` and `end`: the runs of schedules, or with `scheduled_only` off every Job."""
    items = []
    for job in jobs:
        if job.get("status") == "cancelled":
            continue  # cancelled before it ran: nothing happened
        if scheduled_only and job.get("created_by") != SCHEDULER:
            continue
        at = _local(job.get("created_at"))
        if at is None or not start <= at.date() <= end:
            continue
        run = runs_by_job.get(job["id"])
        is_task = job.get("type") == TASK_TYPE
        title = (run or {}).get("title") or (job.get("params") or {}).get("title") or job.get("type") or "job"
        items.append({
            "key": f"job:{job['id']}", "kind": "task" if is_task else "job", "state": item_state(job, run), "at": at,
            "title": title, "type": job.get("type"), "project": job.get("project_id") or "",
            "detail": f"/schedule/item/job/{job['id']}",
        })
    return items


def occurrences(task: dict, start: date, end: date) -> list[datetime]:
    """When a scheduled task will run between `start` and `end`: its next run, and for a weekly one
    each of its days after that at its time of day. A stopped task runs no more."""
    if task.get("state", "active") != "active":
        return []
    following = _naive(task.get("next_run_at"))
    if following is None:
        return []
    schedule = task.get("schedule") or {}
    if schedule.get("kind") != "recurring":
        return [following] if start <= following.date() <= end else []
    try:
        hour, minute = (int(part) for part in (schedule.get("time_of_day") or "").split(":"))
    except ValueError:
        return [following] if start <= following.date() <= end else []
    days = set(schedule.get("days_of_week") or [])
    found, day = [], max(start, following.date())
    while day <= end:
        moment = datetime.combine(day, time(hour, minute))
        if day.weekday() in days and moment >= following:
            found.append(moment)
        day += timedelta(days=1)
    return found


def scheduled_items(tasks: list[dict], start: date, end: date) -> list[dict]:
    items = []
    for task in tasks:
        template = task.get("job_template") or {}
        is_task = template.get("type") == TASK_TYPE
        title = (template.get("params") or {}).get("title") if is_task else None
        for at in occurrences(task, start, end):
            items.append({
                "key": f"scheduled:{task['id']}:{at.isoformat(timespec='minutes')}", "kind": "task" if is_task else "job",
                "state": "scheduled", "at": at, "title": title or task.get("name") or template.get("type") or "scheduled",
                "type": template.get("type"), "project": template.get("project_id") or "",
                "detail": f"/schedule/item/scheduled/{task['id']}?at={at.isoformat(timespec='minutes')}",
            })
        missed = _naive(task.get("missed_run_at"))
        if missed is not None and start <= missed.date() <= end:
            items.append({
                "key": f"missed:{task['id']}", "kind": "task" if is_task else "job", "state": "skipped", "at": missed,
                "title": f"{title or task.get('name') or 'scheduled'} (missed)", "type": template.get("type"),
                "project": template.get("project_id") or "",
                "detail": f"/schedule/item/scheduled/{task['id']}?at={missed.isoformat(timespec='minutes')}",
            })
    return items


def symbols(entries: list[dict]) -> list[dict]:
    """A day's symbols: one per kind and state among its items, with how many items it stands for;
    tasks first, then jobs, each in the order of STATES."""
    counts = Counter((entry["kind"], entry["state"]) for entry in entries)
    return [{"kind": kind, "state": state, "count": counts[kind, state]}
            for kind in KINDS for state in STATES if counts[kind, state]]


def totals(weeks: list[list[dict]]) -> dict[str, dict[str, int]]:
    """How many of the month's own items (not the days around it) are in each state: {state: {all, task, job}}."""
    counts = {state: {"all": 0, **dict.fromkeys(KINDS, 0)} for state in STATES}
    for cell in (c for week in weeks for c in week if c["in_month"]):
        for symbol in cell["symbols"]:
            counts[symbol["state"]][symbol["kind"]] += symbol["count"]
            counts[symbol["state"]]["all"] += symbol["count"]
    return counts


def month(jobs: list[dict], runs_by_job: dict[str, dict], tasks: list[dict], first: date, today: date,
          *, scheduled_only: bool = True) -> list[list[dict]]:
    """The month's grid, week by week: [{date, in_month, today, entries (its items, sorted by time), symbols}]."""
    days = grid_days(first)
    items = job_items(jobs, runs_by_job, days[0], days[-1], scheduled_only=scheduled_only) + scheduled_items(tasks, days[0], days[-1])
    by_day: dict[date, list[dict]] = {}
    for item in sorted(items, key=lambda i: i["at"]):
        by_day.setdefault(item["at"].date(), []).append(item)
    cells = [{"date": d, "in_month": d.month == first.month, "today": d == today, "entries": by_day.get(d, []),
              "symbols": symbols(by_day.get(d, []))} for d in days]
    return [cells[n:n + 7] for n in range(0, len(cells), 7)]
