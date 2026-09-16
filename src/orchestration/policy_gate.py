"""Action-gating — extends the Job model with `pending_confirmation`
(`docs/pillars/policy_compliance.md`'s "Action-gating checks"). A Job
type declaring a gated topic doesn't dispatch until the policy currently
winning precedence on that topic, for that project, passes — checked
via a `policy.check_project` Job attached as an ordinary dependency,
never a synchronous blocking call.

Which Job types declare which gating topics is deliberately small here
— `docs/open_questions.md`'s `OQ-3` leaves the full mapping to each
pillar's own future implementation work; `deployment.deploy` on
`deployment-policy` is this pass's own concrete, real proof of the
mechanism, not a claim every gated Job type is enumerated yet.
"""

from __future__ import annotations

import sqlite3
from typing import Callable

import httpx

from common.shell import CommandRunner, DEFAULT_RUNNER
from common.uds_client import client_for_socket
from common import systemd
from orchestration.db import get_job, insert_job, insert_job_run, update_job
from orchestration.models import Job, JobStatus
from orchestration.queue import cascade_dependency_failures

SocketLookup = Callable[[str], object]

GATED_JOB_TOPICS: dict[str, str] = {
    "deployment.deploy": "deployment-policy",
}

# Matching little-runner's own existing confirm_timeout_seconds default
# exactly, reused rather than picked fresh (docs/pillars/policy_compliance.md).
CONFIRM_TIMEOUT_SECONDS = 600


def resolve_winning_policy_id(socket_lookup: SocketLookup, topic: str, project_id: str, *, timeout: float = 10.0) -> str | None:
    socket_path = socket_lookup("policy")
    if socket_path is None:
        return None
    try:
        with client_for_socket(socket_path, timeout=timeout) as client:
            response = client.get("/v1/policies/winning", params={"topic": topic, "project_id": project_id})
        response.raise_for_status()
    except (httpx.ConnectError, httpx.TimeoutException, OSError, httpx.HTTPStatusError):
        # Policy & Compliance unreachable right now: this pass treats
        # that the same as "no policy currently governs this topic" —
        # dispatch proceeds ungated rather than blocking indefinitely.
        # A documented simplification (docs/requirements.md's M12
        # section), not the spec's full fail-closed-on-unreachable
        # behavior.
        return None
    policy = response.json().get("policy")
    return policy["id"] if policy else None


def attach_policy_gate(conn: sqlite3.Connection, socket_lookup: SocketLookup, job: Job) -> Job:
    """Called once, at Job creation — resolves the winning policy (if
    any) for this Job type's gated topic and this Job's project, and, if
    one exists, creates the `policy.check_project` Job and wires it into
    `depends_on`, exactly the ordinary dependency mechanism every other
    Job already uses."""
    topic = GATED_JOB_TOPICS.get(job.type)
    if topic is None or not job.project_id:
        return job
    policy_id = resolve_winning_policy_id(socket_lookup, topic, job.project_id)
    if policy_id is None:
        return job
    check_job = Job(
        type="policy.check_project", target_pillar="policy", project_id=job.project_id,
        params={"policy_id": policy_id}, created_by="orchestration-gate",
    )
    insert_job(conn, check_job)
    job.depends_on = [*job.depends_on, check_job.id]
    update_job(conn, job)
    return job


def _policy_check_verdict(conn: sqlite3.Connection, job: Job) -> str | None:
    """The outcome ('pass'/'fail'/'not_applicable') of the first
    `policy.check_project` Job among this Job's dependencies, or None if
    it has none — a Job's own gate gets exactly one governing check at
    creation time, so "first" is also "only" in practice."""
    for dep_id in job.depends_on:
        dep = get_job(conn, dep_id)
        if dep is not None and dep.type == "policy.check_project" and dep.status == JobStatus.SUCCEEDED and dep.result:
            outcome = dep.result.get("outcome")
            if outcome is not None:
                return outcome
    return None


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
    if job.type not in GATED_JOB_TOPICS:
        return False
    verdict = _policy_check_verdict(conn, job)
    if verdict != "fail":
        return False
    enter_pending_confirmation(conn, job, timeout_seconds=timeout_seconds, runner=runner)
    return True


def confirm(conn: sqlite3.Connection, job: Job, socket_lookup: SocketLookup) -> dict:
    if job.status != JobStatus.PENDING_CONFIRMATION:
        return {"confirmed": False, "reason": f"job is {job.status.value}, not pending confirmation"}
    from orchestration.dispatch import dispatch_job

    dispatched = dispatch_job(conn, job, socket_lookup)
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
