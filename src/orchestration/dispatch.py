"""Dispatching a ready Job over its target pillar's Pillar Interface —
docs/design.md's "Cross-pillar job dispatch" data flow.
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import httpx

from common.uds_client import client_for_socket
from orchestration.db import get_job, insert_job_run, list_jobs, update_job
from orchestration.models import Job, JobStatus
from orchestration.queue import cascade_dependency_failures, is_ready, ready_jobs

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
    "project.bootstrap", "project.import", "project.remove", "project.connect_github",
    "version.init", "version.feature_start", "version.feature_finish",
    "version.improvement_start", "version.improvement_finish",
    "version.hotfix_start", "version.hotfix_finish",
    "version.release_start", "version.release_candidate_cut",
    "version.release_candidate_promote", "version.release_finish",
    "version.support_start", "version.support_finish",
    "version.config_add_hook", "version.config_remove_hook",
    # Answers `accepted` at once and runs on (release start/finish mid-way
    # must never be cut off); the 180s dispatch ceiling below is ample.
    "version.release",
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
    # Real network work: a repo create, two pushes and eleven label upserts.
    "project.connect_github": 180.0,
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
    # The same OQ-14 class once more, for the Dashboard's New Job catalog
    # (build a project, run its tests): each runs synchronously in its own
    # request handler with a 1800s ceiling of its own (build/runner.py,
    # requirements/testrun.py's run_suites_sandboxed); discovery only
    # collects tests (300s ceiling). 60s/30s margins for worktree setup.
    "build.run": 1860.0,
    "requirements.run_tests": 1860.0,
    "requirements.discover_tests": 330.0,
}


# A Job still `running` this long after its last change is presumed lost:
# its pillar's worker died (a restart mid-run) or never called back, and it
# would otherwise sit `running` forever, holding its queue_key. Far above
# any real Job's length (the longest dispatch ceiling above is
# policy.check_project's 1860s; an agent task runs up to an hour). A plain
# framework rule, not a Policy: `SchedulerThread` enforces it every tick.
RUNNING_JOB_TIMEOUT = timedelta(hours=6)
# Job types that legitimately run longer. An update-doc job's Claude session
# may work for hours and is then given 5 idle hours before it is wrapped up.
RUNNING_JOB_TIMEOUTS: dict[str, timedelta] = {
    "agent.update_docs": timedelta(hours=30),
}


def running_timeout_for(job_type: str, default: timedelta = RUNNING_JOB_TIMEOUT) -> timedelta:
    return RUNNING_JOB_TIMEOUTS.get(job_type, default)


def dispatch_timeout_for(job_type: str) -> float:
    return JOB_TYPE_TIMEOUTS.get(job_type, DEFAULT_DISPATCH_TIMEOUT_SECONDS)


# Job id -> set once its dispatch call has returned. A Job claimed by one
# request's pump can be the very Job another request (the one that
# created it) is waiting to report on; see `wait_for_dispatch`.
_dispatching: dict[str, threading.Event] = {}
_dispatching_lock = threading.Lock()


def wait_for_dispatch(job_id: str, timeout: float) -> None:
    """Block until `job_id`'s in-flight dispatch call (if any) returns."""
    with _dispatching_lock:
        done = _dispatching.get(job_id)
    if done is not None:
        done.wait(timeout)


def dispatch_job(
    conn: sqlite3.Connection, job: Job, socket_lookup: SocketLookup, *,
    timeout: float = DEFAULT_DISPATCH_TIMEOUT_SECONDS, lock: threading.Lock | None = None,
) -> Job:
    """`lock`, when given, guards only this function's own db work: the call
    to the target pillar runs without it. A deploy takes minutes, and every
    other request (a job list, a status read, a session start) would
    otherwise queue behind it and time out. The caller must not hold it."""
    guard = lock if lock is not None else nullcontext()
    socket_path = socket_lookup(job.target_pillar)
    if socket_path is None:
        with guard:
            job.status = JobStatus.FAILED
            job.error = "pillar disabled"
            update_job(conn, job)
            insert_job_run(
                conn, job.id, outcome="failed", detail=job.error,
                started_at=datetime.now(timezone.utc), finished_at=datetime.now(timezone.utc),
            )
        return job

    done = threading.Event()
    with _dispatching_lock:
        _dispatching[job.id] = done
    try:
        with guard:
            job.status = JobStatus.RUNNING
            update_job(conn, job)
        started_at = datetime.now(timezone.utc)

        status, result, error = JobStatus.RUNNING, None, None
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
                    status, result = JobStatus.SUCCEEDED, body.get("result")
                elif outcome == "accepted":
                    # Long-running: stays RUNNING, completed later via
                    # Orchestration's own callback endpoint.
                    pass
                else:
                    status, error = JobStatus.FAILED, body.get("error", "pillar reported failure")
            else:
                status, error = JobStatus.FAILED, f"pillar returned status {response.status_code}"
        except (httpx.ConnectError, httpx.TimeoutException, OSError) as exc:
            # Pillar enabled but unreachable right now — leave the job queued
            # so it's retried once Core reports the pillar healthy again,
            # rather than failing outright.
            status, error = JobStatus.QUEUED, f"target pillar unreachable: {exc}"

        with guard:
            if lock is not None:
                current = get_job(conn, job.id)
                if current is not None and current.status != JobStatus.RUNNING:
                    # Settled while the call was in flight (a `stop`, or an
                    # accepted Job's own completion callback) — that wins.
                    return current
            job.status, job.error = status, error
            if status == JobStatus.SUCCEEDED:
                job.result = result
                from orchestration import policy_gate

                policy_gate.verify_after_completion(conn, socket_lookup, job)
            update_job(conn, job)
            insert_job_run(
                conn, job.id, outcome=job.status.value, detail=job.error,
                started_at=started_at, finished_at=datetime.now(timezone.utc),
            )
        return job
    finally:
        done.set()
        with _dispatching_lock:
            if _dispatching.get(job.id) is done:
                del _dispatching[job.id]


def dispatch_ready_jobs(conn: sqlite3.Connection, socket_lookup: SocketLookup, *, lock: threading.Lock | None = None) -> list[Job]:
    """The queue's own pump: fail anything whose dependency died, then
    dispatch everything actually ready — except a gated Job type whose
    governing policy check just came back `fail`, which goes to
    `pending_confirmation` instead of dispatching (docs/milestones.md
    M12's action-gating).

    With `lock` (which the caller must not hold), ready Jobs are claimed
    (set `running`) under it, so two concurrent pumps never dispatch the
    same Job, and then dispatched without it, side by side."""
    from orchestration import policy_gate

    if lock is None:
        cascade_dependency_failures(conn)
        dispatched: list[Job] = []
        for job in ready_jobs(conn):
            if policy_gate.maybe_gate(conn, job):
                dispatched.append(job)
                continue
            dispatched.append(dispatch_job(conn, job, socket_lookup, timeout=dispatch_timeout_for(job.type)))
        return dispatched

    def run(job: Job) -> Job:
        return dispatch_job(conn, job, socket_lookup, timeout=dispatch_timeout_for(job.type), lock=lock)

    handled: list[Job] = []
    tried: set[str] = set()
    while True:
        claimed: list[Job] = []
        with lock:
            cascade_dependency_failures(conn)
            for job in ready_jobs(conn):
                # Re-checked per Job: claiming an earlier one may have filled its queue_key.
                if job.id in tried or not is_ready(conn, job):
                    continue
                tried.add(job.id)
                if policy_gate.maybe_gate(conn, job):
                    handled.append(job)
                    continue
                job.status = JobStatus.RUNNING
                update_job(conn, job)
                claimed.append(job)
        if not claimed:
            return handled
        if len(claimed) == 1:
            handled.append(run(claimed[0]))
            continue
        results: dict[str, Job] = {}
        threads = [threading.Thread(target=lambda j=job: results.__setitem__(j.id, run(j)), daemon=True) for job in claimed]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        handled.extend(results.get(job.id, job) for job in claimed)
        # Another round: a Job whose queue_key or dependency just freed up is ready now.


def stop_job(
    conn: sqlite3.Connection, job: Job, socket_lookup: SocketLookup, *, timeout: float = 10.0, lock: threading.Lock | None = None,
) -> dict:
    """`docs/milestones.md` M9's `stop`: interrupt a Job that is
    currently *running* — scheduled-originated or not. Never raises;
    every outcome (already settled, declared unstoppable, target
    unreachable, target genuinely stopped it, target says it can't) is a
    plain `{"stopped": bool, "reason": str | None}` the caller can show
    directly."""
    if job.status == JobStatus.QUEUED:
        # Not dispatched yet: nothing to interrupt, so stopping it cancels it.
        with lock if lock is not None else nullcontext():
            job.status = JobStatus.CANCELLED
            job.error = "cancelled by user before it started"
            update_job(conn, job)
            cascade_dependency_failures(conn)
        return {"stopped": True, "reason": None}

    if job.status == JobStatus.PENDING_CONFIRMATION:
        # Never dispatched: nothing to interrupt, but it holds its
        # queue_key until confirmed or timed out -- stopping it declines it.
        with lock if lock is not None else nullcontext():
            job.status = JobStatus.CANCELLED
            job.error = "stopped by user while awaiting policy confirmation"
            update_job(conn, job)
            cascade_dependency_failures(conn)
        return {"stopped": True, "reason": None}

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
        with lock if lock is not None else nullcontext():  # `lock`: not held by the caller, db work only
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


def expire_stuck_jobs(
    conn: sqlite3.Connection, socket_lookup: SocketLookup, *,
    now: datetime | None = None, max_age: timedelta = RUNNING_JOB_TIMEOUT, lock: threading.Lock | None = None,
) -> list[Job]:
    """Fail every Job `running` for longer than `max_age` (see
    `RUNNING_JOB_TIMEOUT`), after asking its pillar to stop it where that
    pillar can; then fail whatever depended on it and pump the queue its
    queue_key was holding. `lock`, when given, must not be held by the caller."""
    guard = lock if lock is not None else nullcontext()
    now = now or datetime.now(timezone.utc)
    with guard:
        stale = [
            job for job in list_jobs(conn, status=JobStatus.RUNNING)
            if now - (job.updated_at if job.updated_at.tzinfo else job.updated_at.replace(tzinfo=timezone.utc)) >= running_timeout_for(job.type, max_age)
        ]
    expired: list[Job] = []
    for job in stale:
        stop_job(conn, job, socket_lookup, lock=lock)  # best effort: most pillars say "not stoppable"
        with guard:
            current = get_job(conn, job.id)
            if current is None:
                continue
            if current.status == JobStatus.RUNNING:
                current.status = JobStatus.FAILED
                current.error = f"timed out: still running after {running_timeout_for(job.type, max_age).total_seconds() / 3600:g} hours"
                update_job(conn, current)
                insert_job_run(conn, current.id, outcome="failed", detail=current.error, started_at=job.updated_at, finished_at=now)
            expired.append(current)
    if expired:
        with guard:
            cascade_dependency_failures(conn)
        dispatch_ready_jobs(conn, socket_lookup, lock=lock)
    return expired


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
