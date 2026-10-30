"""`SchedulerThread`'s own lifecycle — a short interval stands in for
the real 10-minute default so the test doesn't wait that long; the
default itself is asserted separately, once, as a real constant."""

import time
from datetime import datetime, timedelta

import pytest

from orchestration import db
from orchestration.models import JobTemplate, Schedule, ScheduledTask, ScheduleKind
from orchestration.scheduler import DEFAULT_TICK_INTERVAL_SECONDS, SchedulerThread


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "orchestration.db")
    yield connection
    connection.close()


def test_default_tick_interval_is_ten_minutes():
    assert DEFAULT_TICK_INTERVAL_SECONDS == 600.0


def test_scheduler_thread_fires_a_due_task_on_its_first_tick(conn):
    # Due shortly *after* start() -- not already overdue at start() time,
    # which startup_check would instead flag as missed (see the test
    # below). This is the routine, "process was never down" tick path.
    due_at = datetime.now() + timedelta(seconds=0.2)
    task = ScheduledTask(
        name="due-now",
        job_template=JobTemplate(type="noop", target_pillar="reference"),
        schedule=Schedule(kind=ScheduleKind.ONCE, at=due_at),
        next_run_at=due_at,
    )
    db.insert_scheduled_task(conn, task)

    thread = SchedulerThread(conn, socket_lookup=lambda pillar: None, interval_seconds=0.05)
    thread.start()
    try:
        deadline = time.time() + 5
        while time.time() < deadline:
            if db.list_jobs(conn):
                break
            time.sleep(0.05)
        jobs = db.list_jobs(conn)
        assert len(jobs) == 1
        assert jobs[0].created_by == "scheduler"
    finally:
        thread.stop()


def test_scheduler_thread_runs_startup_check_before_the_first_tick(conn):
    """A task overdue at start() time is flagged as missed, not fired —
    even though the very next tick (0.05s later) would otherwise have
    fired it. start() must run startup_check synchronously first."""
    overdue = datetime.now() - timedelta(days=1)
    task = ScheduledTask(
        name="missed-while-down",
        job_template=JobTemplate(type="noop", target_pillar="reference"),
        schedule=Schedule(kind=ScheduleKind.ONCE, at=overdue),
        next_run_at=overdue,
    )
    db.insert_scheduled_task(conn, task)

    thread = SchedulerThread(conn, socket_lookup=lambda pillar: None, interval_seconds=0.05)
    thread.start()
    try:
        time.sleep(0.3)
        reloaded = db.get_scheduled_task(conn, task.id)
        assert reloaded.missed_run_at == overdue
        assert reloaded.next_run_at is None  # once-kind: spent, nothing fired
        assert db.list_jobs(conn) == []
    finally:
        thread.stop()


def test_scheduler_thread_start_deletes_once_tasks_an_older_version_left_after_firing(conn):
    fired_long_ago = datetime.now() - timedelta(days=3)
    spent = ScheduledTask(
        name="already-ran",
        job_template=JobTemplate(type="noop", target_pillar="reference"),
        schedule=Schedule(kind=ScheduleKind.ONCE, at=fired_long_ago),
        next_run_at=None,
    )
    db.insert_scheduled_task(conn, spent)

    thread = SchedulerThread(conn, socket_lookup=lambda pillar: None, interval_seconds=60)
    thread.start()
    try:
        assert db.get_scheduled_task(conn, spent.id) is None
        assert db.list_jobs(conn) == []  # deleted, never re-fired
    finally:
        thread.stop()


def test_scheduler_thread_stop_is_clean_and_idempotent_to_join(conn):
    thread = SchedulerThread(conn, socket_lookup=lambda pillar: None, interval_seconds=60)
    thread.start()
    thread.stop()  # must not hang or raise
