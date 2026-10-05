"""Orchestration's change feed: every Job, step and scheduled-task change is numbered, and a caller
holding a number is told of the next change as soon as it happens."""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from orchestration import changes, db, steps
from orchestration.app import build_app
from orchestration.models import Job, JobStatus, JobTemplate, Schedule, ScheduleKind, ScheduledTask


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "orchestration.db")
    yield connection
    connection.close()


# --- REQ-000000378: Orchestration publishes its changes ---


def test_job_step_and_schedule_writes_are_numbered_with_what_changed(conn):
    start = changes.FEED.seq
    job = Job(type="agent.run_task", target_pillar="agent", project_id="little-meals")
    db.insert_job(conn, job)
    job.status = JobStatus.RUNNING
    db.update_job(conn, job)
    steps.report(conn, job, "check_checkout", "running")
    task = ScheduledTask(name="weekly", job_template=JobTemplate(type="build.run", target_pillar="build"),
                         schedule=Schedule(kind=ScheduleKind.ONCE, at=datetime.now() + timedelta(days=1)))
    db.insert_scheduled_task(conn, task)
    db.delete_scheduled_task(conn, task.id)

    answer = changes.FEED.since(start)
    # The feed is the process's own: keep to this test's changes (another test's thread may write too).
    mine = [c for c in answer["changes"] if c["id"] in (job.id, task.id)]
    assert [(c["kind"], c["id"]) for c in mine] == [
        ("job", job.id), ("job", job.id), ("step", job.id), ("scheduled_task", task.id), ("scheduled_task", task.id)]
    assert mine[1]["status"] == "running" and mine[0]["type"] == "agent.run_task" and mine[0]["project_id"] == "little-meals"
    assert answer["seq"] >= start + 5 and answer["reset"] is False
    assert all(earlier["seq"] < later["seq"] for earlier, later in zip(mine, mine[1:]))


def test_a_caller_waiting_is_answered_as_soon_as_something_changes():
    feed = changes.ChangeFeed()
    threading.Timer(0.2, lambda: feed.note("job", "j1", type="x", status="running")).start()
    started = time.monotonic()
    answer = feed.since(0, timeout=10)
    assert time.monotonic() - started < 5
    assert [c["id"] for c in answer["changes"]] == ["j1"]


def test_with_nothing_new_the_answer_comes_after_the_time_allowed_and_empty():
    feed = changes.ChangeFeed()
    started = time.monotonic()
    answer = feed.since(0, timeout=0.2)
    assert 0.15 <= time.monotonic() - started < 3 and answer["changes"] == [] and answer["seq"] == 0


def test_a_caller_from_another_boot_or_too_far_behind_is_told_to_reset():
    feed = changes.ChangeFeed(keep=3)
    assert feed.since(None) == {"boot": feed.boot, "seq": 0, "changes": [], "reset": False}
    for n in range(5):
        feed.note("job", f"j{n}")
    behind = feed.since(1)
    assert behind["reset"] is True and [c["id"] for c in behind["changes"]] == ["j2", "j3", "j4"]
    assert feed.since(2)["reset"] is False
    assert feed.since(4, boot="an-older-boot", timeout=5)["reset"] is True  # at once, not after waiting
    assert feed.since(99)["reset"] is True


def test_the_feed_is_served_over_the_pillar_interface(conn):
    client = TestClient(build_app(conn, socket_lookup=lambda pillar: None))
    first = client.get("/v1/changes").json()
    db.insert_job(conn, Job(type="build.run", target_pillar="build"))
    answer = client.get("/v1/changes", params={"after": first["seq"], "boot": first["boot"], "timeout": 5}).json()
    assert [c["type"] for c in answer["changes"]] == ["build.run"] and answer["boot"] == first["boot"]
