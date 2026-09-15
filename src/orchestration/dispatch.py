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


def dispatch_job(conn: sqlite3.Connection, job: Job, socket_lookup: SocketLookup, *, timeout: float = 10.0) -> Job:
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
