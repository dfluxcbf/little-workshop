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

# Per-Job-type dispatch timeout overrides — `docs/open_questions.md`'s
# `OQ-14` (resolved 2026-09-17): the 20s default was never chosen with a
# real, slow Job type in mind, and a Job type whose real implementation
# genuinely calls the Claude CLI (`agent.query`, itself real-network,
# non-deterministic latency) can legitimately take longer than that. A
# table here, mirroring `orchestration/policy_gate.py`'s
# `JOB_TYPE_CATEGORIES` pattern, gives those specific types a longer
# ceiling without moving `DEFAULT_DISPATCH_TIMEOUT_SECONDS` itself and
# silently changing every other Job type's own failure-detection
# latency. `dispatch_ready_jobs` consults this before calling
# `dispatch_job`; a direct `dispatch_job` call (e.g. `policy_gate.
# confirm`) still defaults to the plain 20s unless it looks this up too.
DEFAULT_DISPATCH_TIMEOUT_SECONDS = 20.0
JOB_TYPE_TIMEOUTS: dict[str, float] = {
    "policy.author": 120.0,
    "policy.activate": 60.0,
    # M17 ecosystem-cutover finding, the same OQ-14 class of defect: a
    # real `deployment.deploy` shells out to a real `bazel run
    # //:install` from a freshly provisioned worktree — genuinely slower
    # than the 20s default on a cold cache, misreporting "target pillar
    # unreachable" for a deploy that actually succeeds moments later.
    "deployment.deploy": 300.0,
    # 2026-09-23 finding, the same OQ-14 class of defect again:
    # `policy.check_project` runs a real Docker-based sandbox check
    # synchronously inside its own request handler — that sandbox's own
    # ceiling (policy/sandbox.py's DEFAULT_CHECK_TIMEOUT_SECONDS) is
    # 1800s. With only the 20s default here, a real (non-skipped) check
    # was routinely reported "target pillar unreachable" and requeued
    # while the sandbox was still legitimately running — the Docker
    # container just kept running, orphaned from the dispatch attempt
    # that gave up on it. 1860s gives a 60s margin over the sandbox's
    # own ceiling for image-pull/startup overhead on the first run.
    "policy.check_project": 1860.0,
    # 2026-09-23 finding, reproduced live during an actual release: both
    # version.release_start and version.release_finish hit the plain 20s
    # default and were requeued mid-flight ("target pillar unreachable:
    # timed out"), then re-dispatched a moment later while the FIRST,
    # still-running-server-side invocation was still mid-way through its
    # own real git operations (branch create/merge/tag/commit/back-merge)
    # — the retry then failed on a dirty tree or wrong branch, a
    # confusing symptom of the SAME underlying timeout mismatch already
    # fixed once for policy.check_project. `DECLARED_UNSTOPPABLE_JOB_TYPES`
    # above is exactly the maintainer-curated set of "real, possibly-slow,
    # never-safely-interruptible" Job types — reused here rather than a
    # second hand-written list, since it's the same judgment call.
    **{job_type: 180.0 for job_type in DECLARED_UNSTOPPABLE_JOB_TYPES},
}


def dispatch_timeout_for(job_type: str) -> float:
    return JOB_TYPE_TIMEOUTS.get(job_type, DEFAULT_DISPATCH_TIMEOUT_SECONDS)


def dispatch_job(conn: sqlite3.Connection, job: Job, socket_lookup: SocketLookup, *, timeout: float = DEFAULT_DISPATCH_TIMEOUT_SECONDS) -> Job:
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
                from orchestration import policy_gate

                policy_gate.verify_after_completion(conn, socket_lookup, job)
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
    dispatch everything actually ready — except a gated Job type whose
    governing policy check just came back `fail`, which goes to
    `pending_confirmation` instead of dispatching (docs/milestones.md
    M12's action-gating)."""
    from orchestration import policy_gate

    cascade_dependency_failures(conn)
    dispatched: list[Job] = []
    for job in ready_jobs(conn):
        if policy_gate.maybe_gate(conn, job):
            dispatched.append(job)
            continue
        dispatched.append(dispatch_job(conn, job, socket_lookup, timeout=dispatch_timeout_for(job.type)))
    return dispatched


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
