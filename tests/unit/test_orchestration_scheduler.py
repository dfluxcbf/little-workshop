"""`ScheduledTask` firing and the missed-run model — real sqlite
throughout; `socket_lookup` returns None (pillar disabled) except where
a test specifically cares about real dispatch, since dispatch.py's own
success/failure paths are already covered by test_dispatch.py."""

from datetime import datetime, timedelta

import pytest

from orchestration import db
from orchestration.models import JobStatus, JobTemplate, Schedule, ScheduledTask, ScheduleKind
from orchestration.scheduler import cancel_task, postpone_task, startup_check, tick_once, trigger_task


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "orchestration.db")
    yield connection
    connection.close()


def _disabled_lookup(pillar):
    return None


def _once_task(at: datetime) -> ScheduledTask:
    return ScheduledTask(
        name="one-shot",
        job_template=JobTemplate(type="noop", target_pillar="reference"),
        schedule=Schedule(kind=ScheduleKind.ONCE, at=at),
        next_run_at=at,
    )


def _recurring_task(next_run_at: datetime, days=(0, 1, 2, 3, 4, 5, 6)) -> ScheduledTask:
    return ScheduledTask(
        name="nightly",
        job_template=JobTemplate(type="noop", target_pillar="reference"),
        schedule=Schedule(kind=ScheduleKind.RECURRING, days_of_week=list(days), time_of_day="02:00"),
        next_run_at=next_run_at,
    )


# ---------------------------------------------------------------------------
# tick_once
# ---------------------------------------------------------------------------

def test_tick_fires_a_due_once_task_and_spends_it(conn):
    task = _once_task(datetime(2026, 1, 1, 2, 0))
    db.insert_scheduled_task(conn, task)

    fired = tick_once(conn, _disabled_lookup, now=datetime(2026, 1, 1, 2, 5))

    assert len(fired) == 1
    assert fired[0].type == "noop"
    assert fired[0].created_by == "scheduler"

    reloaded = db.get_scheduled_task(conn, task.id)
    assert reloaded.next_run_at is None  # once-kind: spent
    assert reloaded.last_job_id == fired[0].id


def test_tick_does_not_fire_a_task_not_yet_due(conn):
    task = _once_task(datetime(2026, 1, 1, 2, 0))
    db.insert_scheduled_task(conn, task)

    fired = tick_once(conn, _disabled_lookup, now=datetime(2026, 1, 1, 1, 0))

    assert fired == []


def test_tick_advances_a_recurring_task_to_its_next_occurrence(conn):
    task = _recurring_task(datetime(2026, 1, 5, 2, 0))  # a Monday
    db.insert_scheduled_task(conn, task)

    tick_once(conn, _disabled_lookup, now=datetime(2026, 1, 5, 2, 5))

    reloaded = db.get_scheduled_task(conn, task.id)
    assert reloaded.next_run_at == datetime(2026, 1, 6, 2, 0)


def test_tick_clears_a_pending_missed_run(conn):
    task = _recurring_task(datetime(2026, 1, 5, 2, 0))
    task.missed_run_at = datetime(2026, 1, 4, 2, 0)
    db.insert_scheduled_task(conn, task)

    tick_once(conn, _disabled_lookup, now=datetime(2026, 1, 5, 2, 5))

    assert db.get_scheduled_task(conn, task.id).missed_run_at is None


def test_tick_ignores_stopped_tasks(conn):
    from orchestration.models import ScheduledTaskState

    task = _once_task(datetime(2026, 1, 1, 2, 0))
    task.state = ScheduledTaskState.STOPPED
    db.insert_scheduled_task(conn, task)

    fired = tick_once(conn, _disabled_lookup, now=datetime(2026, 1, 1, 3, 0))
    assert fired == []


# ---------------------------------------------------------------------------
# startup_check — the missed-firing model
# ---------------------------------------------------------------------------

def test_startup_check_flags_a_missed_once_task_and_spends_it(conn):
    task = _once_task(datetime(2026, 1, 1, 2, 0))
    db.insert_scheduled_task(conn, task)

    updated = startup_check(conn, now=datetime(2026, 1, 3, 9, 0))

    assert len(updated) == 1
    reloaded = db.get_scheduled_task(conn, task.id)
    assert reloaded.missed_run_at == datetime(2026, 1, 1, 2, 0)
    assert reloaded.next_run_at is None
    # No Job was created -- startup_check only ever flags, never fires.


def test_startup_check_does_not_flag_a_task_that_is_not_yet_due(conn):
    task = _once_task(datetime(2026, 1, 10, 2, 0))
    db.insert_scheduled_task(conn, task)

    updated = startup_check(conn, now=datetime(2026, 1, 1, 9, 0))

    assert updated == []
    assert db.get_scheduled_task(conn, task.id).missed_run_at is None


def test_startup_check_collapses_several_missed_recurring_occurrences_to_the_last_one(conn):
    # Missed Mon 02:00 and Tue 02:00 nightly runs; discovered Wednesday
    # at 01:00 -- Wednesday's own 02:00 occurrence hasn't happened yet.
    task = _recurring_task(datetime(2026, 1, 5, 2, 0))  # Monday
    db.insert_scheduled_task(conn, task)

    startup_check(conn, now=datetime(2026, 1, 7, 1, 0))

    reloaded = db.get_scheduled_task(conn, task.id)
    assert reloaded.missed_run_at == datetime(2026, 1, 6, 2, 0)  # Tuesday: the most recent miss
    assert reloaded.next_run_at == datetime(2026, 1, 7, 2, 0)  # Wednesday: the correct next future one


def test_startup_check_ignores_stopped_tasks(conn):
    from orchestration.models import ScheduledTaskState

    task = _once_task(datetime(2026, 1, 1, 2, 0))
    task.state = ScheduledTaskState.STOPPED
    db.insert_scheduled_task(conn, task)

    updated = startup_check(conn, now=datetime(2026, 1, 3, 9, 0))
    assert updated == []


# ---------------------------------------------------------------------------
# trigger / cancel / postpone
# ---------------------------------------------------------------------------

def test_trigger_fires_now_without_touching_next_run_at(conn):
    original_next = datetime(2026, 2, 1, 2, 0)
    task = _recurring_task(original_next)
    db.insert_scheduled_task(conn, task)

    job = trigger_task(conn, task, _disabled_lookup)

    assert job.created_by == "scheduler"
    reloaded = db.get_scheduled_task(conn, task.id)
    assert reloaded.next_run_at == original_next
    assert reloaded.last_job_id == job.id


def test_trigger_clears_a_pending_missed_run(conn):
    task = _recurring_task(datetime(2026, 2, 1, 2, 0))
    task.missed_run_at = datetime(2026, 1, 31, 2, 0)
    db.insert_scheduled_task(conn, task)

    trigger_task(conn, task, _disabled_lookup)

    assert db.get_scheduled_task(conn, task.id).missed_run_at is None


def test_cancel_dismisses_a_pending_missed_run_without_firing(conn):
    task = _recurring_task(datetime(2026, 2, 1, 2, 0))
    task.missed_run_at = datetime(2026, 1, 31, 2, 0)
    db.insert_scheduled_task(conn, task)

    result = cancel_task(conn, task)

    assert result == {"cancelled": "missed_run"}
    assert db.get_scheduled_task(conn, task.id).missed_run_at is None
    assert db.list_jobs(conn) == []  # nothing was fired


def test_cancel_skips_only_the_next_recurring_occurrence(conn):
    task = _recurring_task(datetime(2026, 1, 5, 2, 0))
    db.insert_scheduled_task(conn, task)

    result = cancel_task(conn, task)

    assert result == {"cancelled": "next_occurrence"}
    assert db.get_scheduled_task(conn, task.id).next_run_at == datetime(2026, 1, 6, 2, 0)


def test_cancel_on_a_once_task_refuses_and_says_so(conn):
    task = _once_task(datetime(2026, 2, 1, 2, 0))
    db.insert_scheduled_task(conn, task)

    result = cancel_task(conn, task)

    assert result["cancelled"] is None
    assert "remove" in result["reason"]
    # Nothing changed -- the task is still there, pending, for `remove` to handle.
    assert db.get_scheduled_task(conn, task.id).next_run_at == datetime(2026, 2, 1, 2, 0)


def test_postpone_retargets_next_run_at_and_clears_any_missed_run(conn):
    task = _recurring_task(datetime(2026, 2, 1, 2, 0))
    task.missed_run_at = datetime(2026, 1, 31, 2, 0)
    db.insert_scheduled_task(conn, task)

    new_at = datetime(2026, 2, 3, 5, 0)
    updated = postpone_task(conn, task, new_at)

    assert updated.next_run_at == new_at
    assert updated.missed_run_at is None
    assert db.get_scheduled_task(conn, task.id).next_run_at == new_at
