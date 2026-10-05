"""The self-pumping queue: which queued Jobs are actually ready to
dispatch right now, and cascading a failed dependency forward so a Job
that can never succeed doesn't sit queued forever — docs/design.md's
Job concept ("`depends_on` resolution") and "the `queue_key` mutex."
"""

from __future__ import annotations

import sqlite3

from orchestration.db import get_job, get_queue_capacity, list_jobs, update_job
from orchestration.models import Job, JobStatus

_LOCKING_STATUSES = (JobStatus.RUNNING.value, JobStatus.PENDING_CONFIRMATION.value)


def is_ready(conn: sqlite3.Connection, job: Job) -> bool:
    if job.status != JobStatus.QUEUED:
        return False

    for dep_id in job.depends_on:
        dep = get_job(conn, dep_id)
        if dep is None or dep.status != JobStatus.SUCCEEDED:
            return False

    if job.queue_key:
        placeholders = ",".join("?" for _ in _LOCKING_STATUSES)
        (running_count,) = conn.execute(
            f"SELECT COUNT(*) FROM jobs WHERE queue_key = ? AND status IN ({placeholders}) AND id != ?",
            (job.queue_key, *_LOCKING_STATUSES, job.id),
        ).fetchone()
        # docs/milestones.md M9's `queue` operation: queue_key gained a
        # configurable capacity (default 1 — the original mutex) instead
        # of a strict one-at-a-time-only rule.
        if running_count >= get_queue_capacity(conn, job.queue_key):
            return False

    return True


def ready_jobs(conn: sqlite3.Connection) -> list[Job]:
    return [job for job in list_jobs(conn, status=JobStatus.QUEUED) if is_ready(conn, job)]


def cascade_dependency_failures(conn: sqlite3.Connection) -> list[Job]:
    """A queued Job whose dependency has failed, was cancelled or was stopped
    can never become ready — fail it too, and repeat until nothing new changes
    so a multi-level dependency chain fails all the way through."""
    changed: list[Job] = []
    while True:
        round_changed: list[Job] = []
        for job in list_jobs(conn, status=JobStatus.QUEUED):
            for dep_id in job.depends_on:
                dep = get_job(conn, dep_id)
                if dep is not None and dep.status in (JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.STOPPED):
                    job.status = JobStatus.FAILED
                    job.error = f"dependency {dep_id} {dep.status.value}"
                    update_job(conn, job)
                    round_changed.append(job)
                    break
        if not round_changed:
            break
        changed.extend(round_changed)
    return changed
