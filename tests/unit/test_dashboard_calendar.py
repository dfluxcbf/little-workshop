"""The Schedule page: every job and task on a month calendar, past, running and to come."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient

from common.paths import Paths
from dashboard import app as app_module
from dashboard import calendar, client
from orchestration_jobs_fake import patch_jobs


def _job(job_id, status, at, **extra):
    return {"id": job_id, "type": extra.pop("type", "build.run"), "status": status, "project_id": "little-meals",
            "created_at": at, "updated_at": at, "params": extra.pop("params", {}), "created_by": "dashboard", **extra}


def _local_utc(year, month, day, hour=12):
    """A UTC timestamp that is (year, month, day) in the host's own time zone."""
    return datetime(year, month, day, hour).astimezone().astimezone(timezone.utc).isoformat()


# --- REQ-000000375: A calendar of jobs and tasks ---


def test_a_month_grid_is_whole_weeks_from_monday():
    days = calendar.grid_days(date(2026, 10, 1))
    assert days[0] == date(2026, 9, 28) and days[-1] == date(2026, 11, 1) and len(days) % 7 == 0
    assert calendar.month_of("2026-02", date(2026, 10, 4)) == date(2026, 2, 1)
    assert calendar.month_of("nonsense", date(2026, 10, 4)) == date(2026, 10, 1)
    assert calendar.shift_month(date(2026, 12, 1), 1) == date(2027, 1, 1) and calendar.shift_month(date(2026, 1, 1), -1) == date(2025, 12, 1)


def test_jobs_and_tasks_are_on_their_day_in_their_state_and_a_cancelled_one_is_not():
    jobs = [
        _job("a", "succeeded", _local_utc(2026, 10, 2)),
        _job("b", "failed", _local_utc(2026, 10, 2)),
        _job("c", "running", _local_utc(2026, 10, 3)),
        _job("d", "stopped", _local_utc(2026, 10, 3)),
        _job("e", "queued", _local_utc(2026, 10, 3)),
        _job("f", "cancelled", _local_utc(2026, 10, 3)),
        _job("t", "succeeded", _local_utc(2026, 10, 4), type="agent.run_task", params={"title": "Export"}),
        _job("old", "succeeded", _local_utc(2026, 8, 1)),
    ]
    runs = {"t": {"id": "r1", "project_id": "little-meals", "title": "Export the plan", "status": "skipped"}}
    items = {i["key"]: i for i in calendar.job_items(jobs, runs, date(2026, 9, 28), date(2026, 11, 1), scheduled_only=False)}
    assert {k: (i["kind"], i["state"]) for k, i in items.items()} == {
        "job:a": ("job", "succeeded"), "job:b": ("job", "failed"), "job:c": ("job", "running"), "job:d": ("job", "skipped"),
        "job:e": ("job", "scheduled"), "job:t": ("task", "skipped"),
    }
    assert items["job:t"]["title"] == "Export the plan" and items["job:t"]["href"] == "/tasks/little-meals/r1"
    assert items["job:a"]["at"].date() == date(2026, 10, 2) and items["job:a"]["detail"] == "/schedule/item/job/a"


def test_by_default_only_the_runs_of_schedules_are_shown():
    jobs = [_job("mine", "succeeded", _local_utc(2026, 10, 2)),
            _job("cron", "failed", _local_utc(2026, 10, 2), created_by="scheduler")]
    shown = calendar.job_items(jobs, {}, date(2026, 9, 28), date(2026, 11, 1))
    assert [i["key"] for i in shown] == ["job:cron"]


def test_a_task_runs_state_wins_over_its_jobs():
    job = {"status": "succeeded"}
    assert calendar.item_state(job, {"status": "awaiting_finish"}) == "succeeded"
    assert calendar.item_state(job, {"status": "failed"}) == "failed"
    assert calendar.item_state(job, {"status": "running"}) == "running"
    assert calendar.item_state({"status": "pending_confirmation"}) == "scheduled"


def _task(task_id, schedule, next_run_at, **extra):
    return {"id": task_id, "name": f"task {task_id}", "state": extra.pop("state", "active"), "schedule": schedule,
            "next_run_at": next_run_at, "missed_run_at": extra.pop("missed_run_at", None), "last_job_id": None,
            "job_template": extra.pop("job_template", {"type": "agent.run_task", "project_id": "little-meals", "params": {"title": "Nightly"}})}


def test_a_weekly_task_is_on_each_of_its_days_from_its_next_run_and_a_once_task_on_its_day():
    weekly = _task("w", {"kind": "recurring", "days_of_week": [0, 3], "time_of_day": "03:00"}, "2026-10-08T03:00:00")
    assert calendar.occurrences(weekly, date(2026, 9, 28), date(2026, 11, 1)) == [
        datetime(2026, 10, d, 3) for d in (8, 12, 15, 19, 22, 26, 29)]
    once = _task("o", {"kind": "once", "at": "2026-10-20T09:30:00"}, "2026-10-20T09:30:00",
                 job_template={"type": "build.run", "project_id": "little-meals", "params": {}})
    assert calendar.occurrences(once, date(2026, 9, 28), date(2026, 11, 1)) == [datetime(2026, 10, 20, 9, 30)]
    assert calendar.occurrences(once, date(2026, 11, 2), date(2026, 12, 6)) == []
    assert calendar.occurrences({**weekly, "state": "stopped"}, date(2026, 9, 28), date(2026, 11, 1)) == []
    items = calendar.scheduled_items([weekly, once], date(2026, 9, 28), date(2026, 11, 1))
    assert {(i["kind"], i["state"]) for i in items} == {("task", "scheduled"), ("job", "scheduled")}
    assert items[0]["title"] == "Nightly" and items[0]["href"] == "/tasks/schedule/w"


def test_a_missed_firing_shows_as_skipped_on_its_day():
    task = _task("w", {"kind": "recurring", "days_of_week": [0], "time_of_day": "03:00"}, "2026-10-12T03:00:00",
                 missed_run_at="2026-10-05T03:00:00")
    missed = [i for i in calendar.scheduled_items([task], date(2026, 9, 28), date(2026, 11, 1)) if i["state"] == "skipped"]
    assert len(missed) == 1 and missed[0]["at"] == datetime(2026, 10, 5, 3) and "(missed)" in missed[0]["title"]


@pytest.fixture
def test_client(tmp_path):
    return TestClient(app_module.build_app(Paths(config_home=tmp_path / "c", state_home=tmp_path / "s")))


def test_the_schedule_page_draws_the_month_with_its_items_and_a_details_panel(test_client, monkeypatch):
    today = date.today()
    jobs = [_job("a1", "failed", _local_utc(today.year, today.month, today.day), created_by="scheduler"),
            _job("t1", "running", _local_utc(today.year, today.month, today.day), type="agent.run_task", params={"title": "Nightly fix"},
                 created_by="scheduler"),
            _job("by-hand", "succeeded", _local_utc(today.year, today.month, today.day), type="build.run")]
    patch_jobs(monkeypatch, client, jobs)
    monkeypatch.setattr(client, "scheduled_tasks", lambda paths: [])
    monkeypatch.setattr(client, "agent_runs", lambda paths, project: [])
    page = test_client.get("/schedule").text
    assert 'href="/schedule"' in page and ">Schedule<" in page  # under Actions
    assert 'class="cal-item kind-job state-failed"' in page and 'class="cal-item kind-task state-running"' in page
    assert 'data-detail="/schedule/item/job/a1"' in page and 'id="cal-panel"' in page
    for state in calendar.STATES:
        assert f"cal-swatch state-{state}" in page
    assert 'data-live="schedule"' in page and 'id="schedule-live" data-live-region' in page
    assert "/schedule/item/job/by-hand" not in page  # started by hand: not a schedule's run
    everything = test_client.get("/schedule", params={"show": "all"}).text
    assert "/schedule/item/job/by-hand" in everything and "show=all" in everything
    other = test_client.get("/schedule", params={"month": "2026-02"}).text
    assert "February 2026" in other and "?month=2026-01" in other and "?month=2026-03" in other


def test_a_busy_day_shows_a_few_items_and_says_how_many_more(test_client, monkeypatch):
    today = date.today()
    jobs = [_job(f"j{n}", "succeeded", _local_utc(today.year, today.month, today.day), created_by="scheduler") for n in range(9)]
    patch_jobs(monkeypatch, client, jobs)
    monkeypatch.setattr(client, "scheduled_tasks", lambda paths: [])
    page = test_client.get("/schedule").text
    assert page.count('class="cal-item kind-job state-succeeded"') == 9 and page.count(" hidden>") >= 9 - calendar.PER_DAY
    assert f"+{9 - calendar.PER_DAY} more" in page


def test_the_details_panel_shows_a_job_or_a_scheduled_run(test_client, monkeypatch):
    job = {**_job("a1", "failed", "2026-10-04T10:00:00+00:00", error="boom"), "steps": []}
    monkeypatch.setattr(client, "get_job", lambda paths, job_id: job if job_id == "a1" else None)
    panel = test_client.get("/schedule/item/job/a1").text
    assert "boom" in panel and 'href="/jobs/a1"' in panel and "failed" in panel and "<html" not in panel
    assert "no longer there" in test_client.get("/schedule/item/job/zz").text

    task = _task("w", {"kind": "recurring", "days_of_week": [0], "time_of_day": "03:00"}, "2026-10-12T03:00:00")
    task["schedule"]["at"] = None
    monkeypatch.setattr(client, "get_scheduled_task", lambda paths, task_id: task)
    panel = test_client.get("/schedule/item/scheduled/w", params={"at": "2026-10-19T03:00"}).text
    assert "Nightly" in panel and "2026-10-19 03:00" in panel and 'href="/tasks/schedule/w"' in panel and "Every Mon at 03:00" in panel
