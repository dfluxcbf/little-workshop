"""Dispatching a ready Job over its target pillar's Pillar Interface —
docs/design.md's "Cross-pillar job dispatch" data flow.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import httpx

from common.uds_client import client_for_socket
from orchestration.db import insert_job_run, update_job
from orchestration.models import Job, JobStatus
from orchestration.queue import cascade_dependency_failures, ready_jobs

# Given a pillar name, returns its socket path if that pillar is known
# and enabled — regardless of whether it's currently *up* — or None if
# it's disabled/unknown. "Enabled but currently down" is what produces
# the connection failure handled below; "disabled/unknown" is decided
# before ever trying to connect.
SocketLookup = Callable[[str], Path | None]

# docs/pillars/orchestration.md's "stop: declared, with a probing
# fallback" — job types the *maintainer* already knows can never safely
# be interrupted mid-flight, so a `stop` call against one is rejected
# immediately, no network round trip. Every other type (including ones
# no pillar has actually built a `stop` hook for yet) falls through to
# actually attempting the real call and trusting the target pillar's own
# answer — the probing fallback. This set only ever grows by a
# maintainer's own hand; nothing computes it.
DECLARED_UNSTOPPABLE_JOB_TYPES: frozenset[str] = frozenset({
    "project.bootstrap", "project.import", "project.remove",
    "version.init", "version.feature_start", "version.feature_finish",
    "version.improvement_start", "version.improvement_finish",
    "version.hotfix_start", "version.hotfix_finish",
    "version.release_start", "version.release_candidate_cut",
    "version.release_candidate_promote", "version.release_finish",
    "version.support_start", "version.support_finish",
    "version.config_add_hook", "version.config_remove_hook",
})


def dispatch_job(conn: sqlite3.Connection, job: Job, socket_lookup: SocketLookup, *, timeout: float = 20.0) -> Job:
    socket_path = socket_lookup(job.target_pillar)
    if socket_path is None:
        job.status = JobStatus.FAILED
        job.error = "pillar disabled"
        update_job(conn, job)
        insert_job_run(
            conn, job.id, outcome="failed", detail=job.error,
            started_at=datetime.now(timezone.utc), finished_at=datetime.now(timezone.utc),
        )
        return job

    job.status = JobStatus.RUNNING
    update_job(conn, job)
    started_at = datetime.now(timezone.utc)

    try:
        with client_for_socket(socket_path, timeout=timeout) as client:
            response = client.post(
                f"/v1/jobs/{job.type}",
                json={"job_id": job.id, "project_id": job.project_id, "params": job.params},
            )
        if response.status_code == 200:
            body = response.json()
            outcome = body.get("outcome", "succeeded")
            if outcome == "succeeded":
                job.status = JobStatus.SUCCEEDED
                job.result = body.get("result")
            elif outcome == "accepted":
                # Long-running: stays RUNNING, completed later via
                # Orchestration's own callback endpoint.
                pass
            else:
                job.status = JobStatus.FAILED
                job.error = body.get("error", "pillar reported failure")
        else:
            job.status = JobStatus.FAILED
            job.error = f"pillar returned status {response.status_code}"
    except (httpx.ConnectError, httpx.TimeoutException, OSError) as exc:
        # Pillar enabled but unreachable right now — leave the job queued
        # so it's retried once Core reports the pillar healthy again,
        # rather than failing outright.
        job.status = JobStatus.QUEUED
        job.error = f"target pillar unreachable: {exc}"

    update_job(conn, job)
    insert_job_run(
        conn, job.id, outcome=job.status.value, detail=job.error,
        started_at=started_at, finished_at=datetime.now(timezone.utc),
    )
    return job


def dispatch_ready_jobs(conn: sqlite3.Connection, socket_lookup: SocketLookup) -> list[Job]:
    """The queue's own pump: fail anything whose dependency died, then
    dispatch everything actually ready."""
    cascade_dependency_failures(conn)
    return [dispatch_job(conn, job, socket_lookup) for job in ready_jobs(conn)]


def stop_job(conn: sqlite3.Connection, job: Job, socket_lookup: SocketLookup, *, timeout: float = 10.0) -> dict:
    """`docs/milestones.md` M9's `stop`: interrupt a Job that is
    currently *running* — scheduled-originated or not. Never raises;
    every outcome (already settled, declared unstoppable, target
    unreachable, target genuinely stopped it, target says it can't) is a
    plain `{"stopped": bool, "reason": str | None}` the caller can show
    directly."""
    if job.status != JobStatus.RUNNING:
        return {"stopped": False, "reason": f"job is {job.status.value}, not running"}

    if job.type in DECLARED_UNSTOPPABLE_JOB_TYPES:
        return {"stopped": False, "reason": f"{job.type!r} is declared unstoppable"}

    socket_path = socket_lookup(job.target_pillar)
    if socket_path is None:
        return {"stopped": False, "reason": "target pillar unreachable"}

    try:
        with client_for_socket(socket_path, timeout=timeout) as client:
            response = client.post(f"/v1/jobs/{job.id}/stop")
    except (httpx.ConnectError, httpx.TimeoutException, OSError):
        return {"stopped": False, "reason": "target pillar unreachable"}

    if response.status_code == 200 and response.json().get("stopped"):
        job.status = JobStatus.STOPPED
        job.error = "stopped by user"
        update_job(conn, job)
        insert_job_run(
            conn, job.id, outcome="stopped", detail=job.error,
            started_at=datetime.now(timezone.utc), finished_at=datetime.now(timezone.utc),
        )
        return {"stopped": True, "reason": None}

    reason = "not stoppable"
    if response.status_code == 200:
        reason = response.json().get("reason", reason)
    return {"stopped": False, "reason": reason}


def complete_job(conn: sqlite3.Connection, job: Job, *, succeeded: bool, result: dict | None, error: str | None) -> Job:
    """A long-running Job's own callback into Orchestration, once the
    target pillar's work finishes."""
    job.status = JobStatus.SUCCEEDED if succeeded else JobStatus.FAILED
    job.result = result
    job.error = error
    update_job(conn, job)
    insert_job_run(
        conn, job.id, outcome=job.status.value, detail=error,
        started_at=datetime.now(timezone.utc), finished_at=datetime.now(timezone.utc),
    )
    return job
