"""The self-pumping queue: which queued Jobs are actually ready to
dispatch right now, and cascading a failed dependency forward so a Job
that can never succeed doesn't sit queued forever — docs/design.md's
Job concept ("`depends_on` resolution") and "the `queue_key` mutex."
"""

from __future__ import annotations

import sqlite3

from orchestration.db import get_job, get_queue_capacity, list_job_runs, list_jobs, update_job
from orchestration.models import Job, JobStatus

# Only a running Job holds its queue_key. A Job held in pending_confirmation has not touched the checkout
# yet, so the project's other Jobs go ahead (REQ-000000538);
# once confirmed it queues again for the key (policy_gate.confirm).
_LOCKING_STATUSES = (JobStatus.RUNNING.value,)

# little-workshop's own redeploys. The automatic redeploy after a finish waits until nothing else is
# going on (REQ-000000476); Orchestration's own redeploy restarts the process that dispatches Jobs,
# so it waits until no other dispatch is in flight (REQ-000000477).
REDEPLOY_CHANGED = "deployment.redeploy_changed_pillars"
REDEPLOY_PILLAR = "deployment.redeploy_pillar"
_REDEPLOYS = (REDEPLOY_CHANGED, REDEPLOY_PILLAR)


def _held_behind_confirmation(conn: sqlite3.Connection, job: Job) -> bool:
    """A queued Job waiting on a Job held for the user: it may wait indefinitely, so it is not work."""
    for dep_id in job.depends_on:
        dep = get_job(conn, dep_id)
        if dep is not None and dep.status == JobStatus.PENDING_CONFIRMATION:
            return True
    return False


def workshop_busy(conn: sqlite3.Connection, job: Job) -> bool:
    """Another Job, not a redeploy, is running or queued (and not just waiting behind a held one)."""
    for other in list_jobs(conn, status=JobStatus.RUNNING) + list_jobs(conn, status=JobStatus.QUEUED):
        if other.id == job.id or other.type in _REDEPLOYS:
            continue
        if other.status == JobStatus.QUEUED and _held_behind_confirmation(conn, other):
            continue
        return True
    return False


def dispatch_in_flight(conn: sqlite3.Connection, job: Job) -> bool:
    """Another Job is claimed and its dispatch call has not been answered yet: a `running` Job without
    the `running` run row an accepted dispatch leaves (see dispatch.settle_interrupted_dispatches)."""
    for other in list_jobs(conn, status=JobStatus.RUNNING):
        if other.id == job.id:
            continue
        runs = list_job_runs(conn, other.id)
        if not (runs and runs[-1]["outcome"] == JobStatus.RUNNING.value):
            return True
    return False


def _waits_for_quiet(conn: sqlite3.Connection, job: Job) -> bool:
    if job.type == REDEPLOY_CHANGED:
        return workshop_busy(conn, job)
    if job.type == REDEPLOY_PILLAR and job.params.get("pillar") == "orchestration":
        return dispatch_in_flight(conn, job)
    return False


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

    return not _waits_for_quiet(conn, job)


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
