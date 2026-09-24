"""Action-gating — extends the Job model with `pending_confirmation`
(`docs/pillars/policy_compliance.md`'s "Action-gating checks"). Refined:
gating is a per-*policy*, user-chosen set of `GatePoint` categories
(`policy/models.py`), not a fixed Job-type-to-topic table — a Job type
just declares which categories it belongs to (`JOB_TYPE_CATEGORIES`
below), and *any* currently-winning, enabled policy selected for that
project that gates one of those categories gets checked. A Job can
now be gated by more than one policy at once (one `policy.check_project`
dependency per gating policy), never just a single fixed one. A policy
whose gating is switched off for this project is still checked — for
analysis, the result is recorded — but its verdict never holds the Job.

Category strings are a shared vocabulary through the Pillar Interface's
own JSON, matching `policy.models.GatePoint`'s values exactly — never a
Python import of that pillar's own code, the same "no shared import
between two pillars' `src/` trees" rule every other cross-pillar call in
this project already follows.

Which Job types declare which categories is deliberately partial here —
`docs/open_questions.md`'s `OQ-3` leaves the full mapping to each
pillar's own future implementation work; the entries below are this
pass's own concrete, real coverage, not a claim every Job type
everywhere is categorized yet.

`project_registration` is deliberately *not* in `JOB_TYPE_CATEGORIES`
below — `OQ-15` (resolved 2026-09-17): the project a `project.bootstrap`/
`project.import` Job would register doesn't exist in the registry yet,
so there is nothing for a pre-dispatch, project-bound check to mount.
It's verified instead, in `verify_after_completion`, once the real
registration has already succeeded — a recorded outcome, never a block,
since blocking an action that already happened is meaningless.
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import nullcontext
from typing import Callable

import httpx

from common.shell import CommandRunner, DEFAULT_RUNNER
from common.uds_client import client_for_socket
from common import systemd
from orchestration.db import get_job, insert_job, insert_job_run, update_job
from orchestration.models import Job, JobStatus
from orchestration.queue import cascade_dependency_failures

SocketLookup = Callable[[str], object]

# Mirrors policy.models.GatePoint's own string values — kept as plain
# strings here rather than importing that enum, since Orchestration and
# Policy & Compliance never share a Python import.
JOB_TYPE_CATEGORIES: dict[str, set[str]] = {
    "build.run": {"build_install"},
    "build.publish_pypi": {"package_publish"},
    "build.publish_vsix": {"package_publish"},
    "deployment.deploy": {"server_launch", "deploy_redeploy"},
    "deployment.stop": {"server_stop"},
    "deployment.redeploy_pillar": {"self_redeploy"},
    "project.remove": {"project_removal"},
    "version.release_start": {"release"},
    "version.release_candidate_cut": {"release"},
    "version.release_candidate_promote": {"release"},
    "version.release_finish": {"release", "branch_finish"},
    "version.release": {"release", "branch_finish"},  # start + finish in one Job
    "version.feature_finish": {"branch_finish"},
    "version.improvement_finish": {"branch_finish"},
    "version.hotfix_finish": {"branch_finish"},
    "version.support_finish": {"branch_finish"},
}

# Matching little-runner's own existing confirm_timeout_seconds default
# exactly, reused rather than picked fresh (docs/pillars/policy_compliance.md).
CONFIRM_TIMEOUT_SECONDS = 600

# `project_registration`'s own post-completion category — checked once
# the real registration has already succeeded, never before (OQ-15).
# A separate table from JOB_TYPE_CATEGORIES on purpose: these two Job
# types are never pre-dispatch-gated, so mixing them into that table
# would make `maybe_gate` try (and fail) to gate them the normal way.
POST_COMPLETION_VERIFY_JOB_TYPES: dict[str, set[str]] = {
    "project.bootstrap": {"project_registration"},
    "project.import": {"project_registration"},
}


def resolve_gating_policies(socket_lookup: SocketLookup, project_id: str, categories: set[str], *, timeout: float = 10.0) -> list[dict]:
    """[{"id": policy id, "gating": whether a failure may hold the Job}] —
    every policy selected for this project that covers these categories."""
    socket_path = socket_lookup("policy")
    if socket_path is None:
        return []
    try:
        with client_for_socket(socket_path, timeout=timeout) as client:
            response = client.get("/v1/policies/gating-for-job", params={"project_id": project_id, "categories": ",".join(sorted(categories))})
        response.raise_for_status()
    except (httpx.ConnectError, httpx.TimeoutException, OSError, httpx.HTTPStatusError):
        # Policy & Compliance unreachable right now: this pass treats
        # that the same as "no policy currently governs this Job" —
        # dispatch proceeds ungated rather than blocking indefinitely.
        # A documented simplification (docs/requirements.md's M12
        # section), not the spec's full fail-closed-on-unreachable
        # behavior.
        return []
    return [{"id": p["id"], "gating": bool(p.get("gating", True))} for p in response.json()]


def attach_policy_gate(conn: sqlite3.Connection, socket_lookup: SocketLookup, job: Job) -> Job:
    """Called once, at Job creation — resolves every currently-winning
    policy (if any) gating one of this Job type's categories, for this
    Job's project, and creates one `policy.check_project` Job per gating
    policy, wired into `depends_on` — exactly the ordinary dependency
    mechanism every other Job already uses, just possibly more than one
    of them now."""
    categories = JOB_TYPE_CATEGORIES.get(job.type)
    if not categories or not job.project_id:
        return job
    policies = resolve_gating_policies(socket_lookup, job.project_id, categories)
    if not policies:
        return job
    for policy in policies:
        check_job = Job(
            type="policy.check_project", target_pillar="policy", project_id=job.project_id,
            params={"policy_id": policy["id"], "gating": policy["gating"]}, created_by="orchestration-gate",
        )
        insert_job(conn, check_job)
        job.depends_on = [*job.depends_on, check_job.id]
    update_job(conn, job)
    return job


def verify_after_completion(conn: sqlite3.Connection, socket_lookup: SocketLookup, job: Job) -> Job:
    """Called once a `project.bootstrap`/`project.import` Job has
    genuinely succeeded — resolves every currently-winning,
    framework-bound policy gating `project_registration` for the
    project that just got registered, and creates one
    `policy.check_project` Job per policy, fire-and-forget: no
    `depends_on` wiring back to this Job (it already succeeded; nothing
    to hold), no `pending_confirmation` on a `fail` verdict (the action
    it would have gated already happened). The result is a real,
    recorded `PolicyCheckResult` — verification, not a gate."""
    if job.status != JobStatus.SUCCEEDED or job.type not in POST_COMPLETION_VERIFY_JOB_TYPES or not job.project_id:
        return job
    categories = POST_COMPLETION_VERIFY_JOB_TYPES[job.type]
    for policy in resolve_gating_policies(socket_lookup, job.project_id, categories):
        check_job = Job(
            type="policy.check_project", target_pillar="policy", project_id=job.project_id,
            params={"policy_id": policy["id"], "gating": policy["gating"]}, created_by="orchestration-post-registration-verify",
        )
        insert_job(conn, check_job)
    return job


def _policy_check_verdicts(conn: sqlite3.Connection, job: Job) -> list[str]:
    """The outcome ('pass'/'fail'/'not_applicable') of every
    `policy.check_project` Job among this Job's dependencies that is allowed
    to gate — a check whose project has gating switched off still ran (its
    result is recorded for analysis) but never counts here."""
    verdicts = []
    for dep_id in job.depends_on:
        dep = get_job(conn, dep_id)
        if dep is not None and dep.type == "policy.check_project" and dep.status == JobStatus.SUCCEEDED and dep.result:
            if dep.params.get("gating") is False:
                continue
            outcome = dep.result.get("outcome")
            if outcome is not None:
                verdicts.append(outcome)
    return verdicts


def enter_pending_confirmation(
    conn: sqlite3.Connection, job: Job, *, timeout_seconds: int = CONFIRM_TIMEOUT_SECONDS, runner: CommandRunner = DEFAULT_RUNNER,
) -> Job:
    job.status = JobStatus.PENDING_CONFIRMATION
    update_job(conn, job)
    insert_job_run(conn, job.id, outcome="pending_confirmation", detail="policy check failed; awaiting confirmation", started_at=job.created_at, finished_at=None)
    _arm_confirm_timeout(job.id, timeout_seconds=timeout_seconds, runner=runner)
    return job


def _arm_confirm_timeout(job_id: str, *, timeout_seconds: int, runner: CommandRunner) -> None:
    """A brand-new, independent transient `systemd --user` unit, so it
    fires the timeout even if Orchestration's own unit restarts in the
    meantime — the identical pattern Deployment & Servers' own
    confirm-gate watchdog already uses (`deployment/flow.py`'s
    `arm_confirm_gate`)."""
    unit = f"policy-confirm-timeout-{job_id}"
    runner.run([
        "systemd-run", "--user", "--collect", f"--unit=little-workshop-{unit}", f"--on-active={timeout_seconds}s",
        f"--setenv=PATH={systemd.user_path_env()}",
        "lws", "job", "policy-confirm-timeout", job_id,
    ])


def maybe_gate(
    conn: sqlite3.Connection, job: Job, *, timeout_seconds: int = CONFIRM_TIMEOUT_SECONDS, runner: CommandRunner = DEFAULT_RUNNER,
) -> bool:
    """Called on a Job that `ready_jobs()` already found dispatch-ready
    (every dependency succeeded). Returns True if this call handled the
    Job itself (flipped it to `pending_confirmation`) — the caller must
    not also dispatch it normally."""
    if job.type not in JOB_TYPE_CATEGORIES:
        return False
    verdicts = _policy_check_verdicts(conn, job)
    if "fail" not in verdicts:
        return False
    enter_pending_confirmation(conn, job, timeout_seconds=timeout_seconds, runner=runner)
    return True


def confirm(conn: sqlite3.Connection, job: Job, socket_lookup: SocketLookup, *, lock: threading.Lock | None = None) -> dict:
    """`lock` (not held by the caller) guards the claim; the dispatch itself runs without it."""
    from orchestration.dispatch import dispatch_job, dispatch_timeout_for

    with lock if lock is not None else nullcontext():
        if lock is not None:
            job = get_job(conn, job.id) or job
        if job.status != JobStatus.PENDING_CONFIRMATION:
            return {"confirmed": False, "reason": f"job is {job.status.value}, not pending confirmation"}
        job.status = JobStatus.RUNNING  # claimed: a second confirm now finds it settled
        update_job(conn, job)
    dispatched = dispatch_job(conn, job, socket_lookup, timeout=dispatch_timeout_for(job.type), lock=lock)
    return {"confirmed": True, "job": dispatched.model_dump(mode="json")}


def confirm_timeout(conn: sqlite3.Connection, job: Job) -> dict:
    if job.status != JobStatus.PENDING_CONFIRMATION:
        return {"timed_out": False, "reason": f"job is {job.status.value}, already settled"}
    job.status = JobStatus.FAILED
    job.error = "policy check unconfirmed"
    update_job(conn, job)
    insert_job_run(conn, job.id, outcome="failed", detail=job.error, started_at=job.created_at, finished_at=None)
    cascade_dependency_failures(conn)
    return {"timed_out": True}
