"""`ScheduledTask` firing — `docs/pillars/orchestration.md`'s "Scheduled
tasks" and "Missed firings." A single in-process background thread
wakes every 10 minutes and fires anything due (`tick_once`); a separate,
one-time check at process startup (`startup_check`) is the only place a
firing missed while little-workshop itself was down gets detected — a
live tick always catches a due task within its own 10-minute window, so
a `next_run_at` still in the past *at startup* can only mean the process
was down through it.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from contextlib import nullcontext
from datetime import datetime

from common import origin
from orchestration import db
from orchestration.dispatch import SocketLookup, dispatch_ready_jobs, expire_attention_steps, expire_stuck_jobs
from orchestration.models import Job, ScheduledTask, ScheduledTaskState, ScheduleKind
from orchestration.schedule import next_occurrence_after

logger = logging.getLogger(__name__)

DEFAULT_TICK_INTERVAL_SECONDS = 600.0  # 10 minutes


def _create_job_from_template(
    conn: sqlite3.Connection, task: ScheduledTask, socket_lookup: SocketLookup, lock: threading.Lock | None = None
) -> Job:
    job = Job(
        type=task.job_template.type,
        target_pillar=task.job_template.target_pillar,
        project_id=task.job_template.project_id,
        params=task.job_template.params,
        queue_key=task.job_template.queue_key,
        depends_on=task.job_template.depends_on,
        created_by="scheduler",
        origin=origin.SCHEDULED_JOB,
    )
    guard = lock if lock is not None else nullcontext()
    with guard:
        db.insert_job(conn, job)
    dispatch_ready_jobs(conn, socket_lookup, lock=lock)
    task.last_job_id = job.id
    with guard:
        return db.get_job(conn, job.id) or job


def _is_spent(task: ScheduledTask) -> bool:
    """A `once` task whose single firing has happened (on time, or its missed
    firing triggered or dismissed): nothing left to schedule or decide."""
    return task.schedule.kind == ScheduleKind.ONCE and task.next_run_at is None and task.missed_run_at is None


def _save_or_remove(conn: sqlite3.Connection, task: ScheduledTask) -> None:
    """Persist `task`, or delete it once spent — only a task that can still
    fire, or still needs the user's missed-run decision, stays listed."""
    if _is_spent(task):
        db.delete_scheduled_task(conn, task.id)
    else:
        db.update_scheduled_task(conn, task)


def remove_spent_tasks(conn: sqlite3.Connection) -> list[ScheduledTask]:
    """Delete every spent `once` task: at startup, for any left by a version
    that kept them after firing."""
    spent = [t for t in db.list_scheduled_tasks(conn) if _is_spent(t)]
    for task in spent:
        db.delete_scheduled_task(conn, task.id)
    return spent


def tick_once(
    conn: sqlite3.Connection, socket_lookup: SocketLookup, *, now: datetime | None = None, lock: threading.Lock | None = None
) -> list[Job]:
    """Fires every active task whose `next_run_at` is due, advancing it
    to its own next occurrence (or deleting it, for a `once` task, now
    spent) and clearing any pending `missed_run_at` — a real firing on
    schedule always supersedes a stale missed-run question."""
    now = now or datetime.now()
    guard = lock if lock is not None else nullcontext()
    fired: list[Job] = []
    with guard:
        tasks = db.list_scheduled_tasks(conn, state=ScheduledTaskState.ACTIVE)
    for task in tasks:
        if task.next_run_at is None or task.next_run_at > now:
            continue
        job = _create_job_from_template(conn, task, socket_lookup, lock)
        fired.append(job)
        task.missed_run_at = None
        task.next_run_at = next_occurrence_after(task.schedule, after=task.next_run_at)
        with guard:
            _save_or_remove(conn, task)
    return fired


def startup_check(conn: sqlite3.Connection, *, now: datetime | None = None) -> list[ScheduledTask]:
    """Runs once, at process startup, before the tick loop starts. A
    `recurring` task that missed several occurrences during a longer
    outage collapses to just the most recent one — running all of them
    back to back is never useful, per the design's own reasoning."""
    now = now or datetime.now()
    updated: list[ScheduledTask] = []
    for task in db.list_scheduled_tasks(conn, state=ScheduledTaskState.ACTIVE):
        if task.next_run_at is None or task.next_run_at > now:
            continue

        last_missed = task.next_run_at
        if task.schedule.kind == ScheduleKind.RECURRING:
            cursor = task.next_run_at
            while True:
                candidate = next_occurrence_after(task.schedule, after=cursor)
                if candidate is None or candidate > now:
                    break
                last_missed = candidate
                cursor = candidate
            task.next_run_at = next_occurrence_after(task.schedule, after=cursor)
        else:
            task.next_run_at = None  # once-kind: spent, nothing left to schedule

        task.missed_run_at = last_missed
        db.update_scheduled_task(conn, task)
        updated.append(task)
    return updated


def trigger_task(
    conn: sqlite3.Connection, task: ScheduledTask, socket_lookup: SocketLookup, *, lock: threading.Lock | None = None
) -> Job:
    """Fires the template right now, out of band — `next_run_at` is
    left exactly as it was; the schedule's normal cadence keeps going
    independently. Resolves a pending missed-run question, if there was
    one, the same way an on-time firing would — and a `once` task left
    with nothing to fire is deleted."""
    job = _create_job_from_template(conn, task, socket_lookup, lock)
    task.missed_run_at = None
    with lock if lock is not None else nullcontext():
        _save_or_remove(conn, task)
    return job


def cancel_task(conn: sqlite3.Connection, task: ScheduledTask) -> dict:
    """Dismisses a pending missed-run question without firing it, if
    there is one; otherwise skips the one upcoming occurrence — for
    `recurring`, only that one, the next one after it still happens on
    schedule; for `once`, there is no next occurrence to fall back to,
    so this is the caller's cue to `remove` the task instead. Dismissing a
    `once` task's missed firing leaves nothing to fire, so it is deleted."""
    if task.missed_run_at is not None:
        task.missed_run_at = None
        _save_or_remove(conn, task)
        return {"cancelled": "missed_run"}

    if task.next_run_at is None:
        return {"cancelled": None, "reason": "nothing pending to cancel"}

    if task.schedule.kind == ScheduleKind.ONCE:
        return {"cancelled": None, "reason": "a 'once' task has no later occurrence — remove it instead"}

    task.next_run_at = next_occurrence_after(task.schedule, after=task.next_run_at)
    db.update_scheduled_task(conn, task)
    return {"cancelled": "next_occurrence"}


def postpone_task(conn: sqlite3.Connection, task: ScheduledTask, new_at: datetime) -> ScheduledTask:
    """Retargets the one pending firing (the missed one, if there is a
    pending question, otherwise the next regular occurrence) to a
    different date/time — the recurring baseline itself is untouched;
    whatever occurrence follows still lands on the original cadence."""
    task.next_run_at = new_at
    task.missed_run_at = None
    db.update_scheduled_task(conn, task)
    return task


class SchedulerThread:
    """The tick loop itself — started once, from `orchestration/__main__.py`,
    alongside `uvicorn.run`. `interval_seconds` is only ever overridden in
    tests; production always uses the real 10-minute default."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        socket_lookup: SocketLookup,
        *,
        interval_seconds: float = DEFAULT_TICK_INTERVAL_SECONDS,
        lock: threading.Lock | None = None,
    ) -> None:
        self._conn = conn
        self._lock = lock
        self._socket_lookup = socket_lookup
        self._interval_seconds = interval_seconds
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        with self._lock if self._lock is not None else nullcontext():
            remove_spent_tasks(self._conn)
            startup_check(self._conn)
        self._thread.start()

    def _run(self) -> None:
        self._expire_stuck_jobs()
        while not self._stop_event.wait(self._interval_seconds):
            tick_once(self._conn, self._socket_lookup, lock=self._lock)
            # A pump on every tick: a Job held for plan pressure, or one a trigger queued, starts
            # without waiting for some other request to pump.
            try:
                dispatch_ready_jobs(self._conn, self._socket_lookup, lock=self._lock)
            except Exception:  # never let a failed pump take the tick loop down with it
                logger.exception("pumping ready jobs failed")
            self._expire_stuck_jobs()

    def _expire_stuck_jobs(self) -> None:
        """The running-Job timeout (dispatch.RUNNING_JOB_TIMEOUT) — at start, then every tick."""
        try:
            expire_stuck_jobs(self._conn, self._socket_lookup, lock=self._lock)
            expire_attention_steps(self._conn, self._socket_lookup, lock=self._lock)
        except Exception:  # never let the sweep take the tick loop down with it
            logger.exception("expiring stuck jobs failed")

    def stop(self) -> None:
        self._stop_event.set()
        self._thread.join(timeout=5)
