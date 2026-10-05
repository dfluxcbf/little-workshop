"""What the MCP Server talks to: every pillar's Pillar Interface over its
Unix socket. A deliberate duplicate of `cli.client`'s HTTP glue rather than
a shared import — docs/architecture.md's PI section enforces no shared
Python import path between two `src/` trees, a rule this project has kept
between every pair of pillars so far and extends here to its two thin
proxies (CLI and MCP Server).

Unlike `cli.client`, nothing here falls back to a direct database/file read
when a daemon is unreachable: the MCP Server holds no domain data of its
own (docs/architecture.md's Components table), so a caller reaching it
through a live MCP session is already talking to a live process — if the
target pillar's daemon is down, that is simply the answer to report back,
not something to paper over with a local read.
"""

from __future__ import annotations

import time

import httpx

from common.paths import Paths
from common.pillar_socket import resolve_pillar_socket
from common.uds_client import client_for_socket


class PillarUnavailable(Exception):
    """Raised whenever a pillar's Pillar Interface can't be reached."""


def _failure(pillar: str, exc: Exception, timeout: float) -> str:
    """Say what actually went wrong. A pillar that took the request and has
    not answered yet is not "unreachable": a caller told it was would retry
    something that is in fact running (2026-09-24)."""
    if isinstance(exc, httpx.ReadTimeout):
        return (
            f"{pillar} daemon took the request but has not answered within {timeout:g}s; "
            "it may still be working on it -- check workshop_job_list before retrying"
        )
    if isinstance(exc, httpx.HTTPStatusError):
        return f"{pillar} daemon answered HTTP {exc.response.status_code}: {exc.response.text[:500]}"
    return f"{pillar} daemon unreachable"


def _get(paths: Paths, pillar: str, path: str, *, params: dict | None = None, timeout: float = 5.0) -> dict:
    socket_path = resolve_pillar_socket(paths, pillar, timeout=timeout)
    if socket_path is None:
        raise PillarUnavailable(f"{pillar} daemon unreachable — Core has no socket for it registered")
    try:
        with client_for_socket(socket_path, timeout=timeout) as client:
            response = client.get(path, params=params or {})
        response.raise_for_status()
        return response.json()
    except (httpx.ConnectError, httpx.TimeoutException, OSError, httpx.HTTPStatusError) as exc:
        raise PillarUnavailable(_failure(pillar, exc, timeout)) from exc


def _mutate(
    paths: Paths, pillar: str, method: str, path: str, *, json: dict | None = None, timeout: float = 10.0
) -> dict:
    socket_path = resolve_pillar_socket(paths, pillar, timeout=timeout)
    if socket_path is None:
        raise PillarUnavailable(f"{pillar} daemon unreachable — Core has no socket for it registered")
    try:
        with client_for_socket(socket_path, timeout=timeout) as client:
            response = client.request(method, path, json=json or {})
        response.raise_for_status()
        return response.json()
    except (httpx.ConnectError, httpx.TimeoutException, OSError, httpx.HTTPStatusError) as exc:
        raise PillarUnavailable(_failure(pillar, exc, timeout)) from exc


def core_status(paths: Paths, *, timeout: float = 3.0) -> dict:
    try:
        with client_for_socket(paths.core_socket_path, timeout=timeout) as client:
            response = client.get("/v1/status")
        response.raise_for_status()
        return response.json()
    except (httpx.ConnectError, httpx.TimeoutException, OSError, httpx.HTTPStatusError) as exc:
        raise PillarUnavailable("Core daemon unreachable") from exc


def create_job(paths: Paths, *, timeout: float = 30.0, **fields) -> dict:
    socket_path = resolve_pillar_socket(paths, "orchestration", timeout=timeout)
    if socket_path is None:
        raise PillarUnavailable("orchestration daemon unreachable — Core has no socket for it registered")
    try:
        with client_for_socket(socket_path, timeout=timeout) as client:
            response = client.post("/v1/jobs", json=fields)
        response.raise_for_status()
        return response.json()
    except (httpx.ConnectError, httpx.TimeoutException, OSError, httpx.HTTPStatusError) as exc:
        raise PillarUnavailable(_failure("orchestration", exc, timeout)) from exc


_TERMINAL = ("succeeded", "failed", "cancelled", "stopped")


def await_job(paths: Paths, job: dict, *, timeout: float = 120.0, poll_seconds: float = 2.0) -> dict:
    """A Job that can stop and ask the user (a step with `needs_attention`) and is still running is
    waited for, up to `timeout` seconds, until it ends or a step waits for an answer; the caller gets
    that Job, with its checklist, to relay the question. Any other Job comes back as it is."""
    if job.get("status") != "running" or not any(s.get("needs_attention") for s in job.get("steps") or []):
        return job
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(poll_seconds)
        try:
            job = job_status(paths, job["id"])
        except PillarUnavailable:
            continue
        if job.get("status") in _TERMINAL or any(s["state"] == "attention" for s in job.get("steps") or []):
            break
    return job


def answer_step(paths: Paths, job_id: str, key: str, choice: str, *, timeout: float = 10.0) -> dict:
    return _mutate(paths, "orchestration", "POST", f"/v1/jobs/{job_id}/steps/{key}/answer", json={"choice": choice}, timeout=timeout)


def list_jobs(paths: Paths, *, status: str | None = None, types: str | None = None, limit: int | None = 50, full: bool = False,
              timeout: float = 5.0):
    """The newest Jobs first, at most `limit` (None: every one), as summaries unless `full`. (No project filter:
    an optional `project_id` would have the scope guard insist on it, and a pillar redeploy names a pillar there.)"""
    params = {"status": status, "type": types, "order": "newest", "limit": limit, "view": None if full else "summary"}
    return _get(paths, "orchestration", "/v1/jobs", params={key: value for key, value in params.items() if value is not None}, timeout=timeout)


def job_status(paths: Paths, job_id: str, *, timeout: float = 5.0) -> dict:
    return _get(paths, "orchestration", f"/v1/jobs/{job_id}", timeout=timeout)


def job_wait(paths: Paths, job_id: str, timeout_seconds: float | None = None) -> dict:
    """Orchestration's long poll; the request may take as long as the wait itself, plus a margin."""
    params = {"timeout": timeout_seconds} if timeout_seconds is not None else None
    return _get(paths, "orchestration", f"/v1/jobs/{job_id}/wait", params=params, timeout=(timeout_seconds or 120.0) + 15.0)


def list_projects(paths: Paths, *, status: str | None = None, timeout: float = 5.0):
    return _get(paths, "registry", "/v1/projects", params={"status": status} if status else None, timeout=timeout)


def get_project(paths: Paths, name: str, *, timeout: float = 5.0) -> dict:
    return _get(paths, "registry", f"/v1/projects/{name}", timeout=timeout)


def backlog_list(paths: Paths, project_id: str, *, timeout: float = 5.0) -> list[dict]:
    return _get(paths, "registry", "/v1/backlog", params={"project_name": project_id}, timeout=timeout)


def backlog_get(paths: Paths, entry_id: str, *, timeout: float = 5.0) -> dict:
    return _get(paths, "registry", f"/v1/backlog/{entry_id}", timeout=timeout)


def backlog_add(paths: Paths, project_id: str, text: str, *, timeout: float = 10.0) -> dict:
    return _mutate(paths, "registry", "POST", "/v1/backlog", json={"project_name": project_id, "primary_entry": text}, timeout=timeout)


def backlog_delete(paths: Paths, entry_id: str, *, timeout: float = 10.0) -> dict:
    return _mutate(paths, "registry", "DELETE", f"/v1/backlog/{entry_id}", timeout=timeout)


def version_status(paths: Paths, kind: str, project_id: str, *, timeout: float = 5.0) -> dict:
    return _get(paths, "version", f"/v1/status/{kind}", params={"project_id": project_id}, timeout=timeout)


def version_config(paths: Paths, project_id: str, *, timeout: float = 5.0) -> dict:
    return _get(paths, "version", "/v1/config", params={"project_id": project_id}, timeout=timeout)


def version_release_candidates(paths: Paths, project_id: str, *, timeout: float = 5.0) -> dict:
    return _get(paths, "version", "/v1/release/candidates", params={"project_id": project_id}, timeout=timeout)


def version_issues(paths: Paths, project_id: str, *, timeout: float = 5.0) -> dict:
    return _get(paths, "version", "/v1/issues", params={"project_id": project_id}, timeout=timeout)


def deployment_status(paths: Paths, project_id: str, *, timeout: float = 5.0) -> dict:
    return _get(paths, "deployment", f"/v1/status/{project_id}", timeout=timeout)


def deployment_history(paths: Paths, project_id: str, *, timeout: float = 5.0) -> dict:
    return _get(paths, "deployment", f"/v1/deployments/{project_id}", timeout=timeout)


def deployment_confirmation(paths: Paths, project_id: str, *, timeout: float = 5.0) -> dict:
    return _get(paths, "deployment", f"/v1/confirmations/{project_id}", timeout=timeout)


def deployment_branches(paths: Paths, project_id: str, *, timeout: float = 5.0) -> dict:
    return _get(paths, "deployment", f"/v1/branches/{project_id}", timeout=timeout)


def requirements_list_test_suites(paths: Paths, project_id: str | None = None, *, timeout: float = 5.0):
    return _get(paths, "requirements", "/v1/test_suites", params={"project_id": project_id} if project_id else None, timeout=timeout)


def requirements_list_requirements(
    paths: Paths, project_id: str | None = None, status: str | None = None, *, timeout: float = 5.0
):
    params = {k: v for k, v in {"project_id": project_id, "status": status}.items() if v}
    return _get(paths, "requirements", "/v1/requirements", params=params or None, timeout=timeout)


def requirements_get_requirement(paths: Paths, requirement_id: str, *, timeout: float = 5.0) -> dict:
    return _get(paths, "requirements", f"/v1/requirements/{requirement_id}", timeout=timeout)


def requirements_report(paths: Paths, project_id: str, *, timeout: float = 10.0) -> dict:
    return _get(paths, "requirements", "/v1/report", params={"project_id": project_id}, timeout=timeout)


def build_list_runs(paths: Paths, project_id: str | None = None, *, timeout: float = 5.0):
    return _get(paths, "build", "/v1/build_runs", params={"project_id": project_id} if project_id else None, timeout=timeout)


def build_get_run(paths: Paths, run_id: str, *, timeout: float = 5.0) -> dict:
    return _get(paths, "build", f"/v1/build_runs/{run_id}", timeout=timeout)


def build_list_packages(
    paths: Paths, project_id: str | None = None, kind: str | None = None, *, timeout: float = 5.0
):
    params = {k: v for k, v in {"project_id": project_id, "kind": kind}.items() if v}
    return _get(paths, "build", "/v1/packages", params=params or None, timeout=timeout)


def policy_list(paths: Paths, topic: str | None = None, *, timeout: float = 5.0):
    return _get(paths, "policy", "/v1/policies", params={"topic": topic} if topic else None, timeout=timeout)


def policy_get(paths: Paths, policy_id: str, *, timeout: float = 5.0) -> dict:
    return _get(paths, "policy", f"/v1/policies/{policy_id}", timeout=timeout)


def policy_winning(paths: Paths, topic: str, project_id: str, *, timeout: float = 10.0) -> dict:
    return _get(paths, "policy", "/v1/policies/winning", params={"topic": topic, "project_id": project_id}, timeout=timeout)


def policy_check_results(paths: Paths, policy_id: str | None = None, project_id: str | None = None, *, timeout: float = 5.0):
    params = {k: v for k, v in {"policy_id": policy_id, "project_id": project_id}.items() if v}
    return _get(paths, "policy", "/v1/policy_check_results", params=params or None, timeout=timeout)


def policy_confirm_job(paths: Paths, job_id: str, *, timeout: float = 10.0) -> dict:
    return _mutate(paths, "orchestration", "POST", f"/v1/jobs/{job_id}/policy_confirm", timeout=timeout)


def policy_list_projects(paths: Paths, policy_id: str | None = None, project_id: str | None = None, *, timeout: float = 5.0):
    params = {k: v for k, v in {"policy_id": policy_id, "project_id": project_id}.items() if v}
    return _get(paths, "policy", "/v1/policy_projects", params=params or None, timeout=timeout)


def policy_list_drafts(paths: Paths, status: str | None = None, *, timeout: float = 5.0):
    return _get(paths, "policy", "/v1/policy_drafts", params={"status": status} if status else None, timeout=timeout)


def policy_gating_for_job(paths: Paths, project_id: str, categories: list[str], *, timeout: float = 10.0):
    return _get(paths, "policy", "/v1/policies/gating-for-job", params={"project_id": project_id, "categories": ",".join(categories)}, timeout=timeout)


def policy_evaluate(paths: Paths, project_id: str, gate_point: str, *, timeout: float = 1900.0) -> dict:
    """Run the policies gating `gate_point` for the project now, unrecorded. Long timeout: a
    branch_finish preflight includes policies that run the project's own test suite."""
    return _mutate(paths, "policy", "POST", "/v1/policies/evaluate",
                   json={"project_id": project_id, "gate_point": gate_point}, timeout=timeout)


def usage_summary(paths: Paths, *, project: str | None = None, week: str | None = None, origin: str | None = None,
                  timeout: float = 20.0) -> dict:
    params = {key: value for key, value in (("project", project), ("week", week), ("origin", origin)) if value}
    return _get(paths, "usage", "/v1/summary", params=params or None, timeout=timeout)


def usage_findings(paths: Paths, *, project: str | None = None, state: str = "open", timeout: float = 10.0) -> dict:
    params = {"state": state, **({"project": project} if project else {})}
    return _get(paths, "usage", "/v1/findings", params=params, timeout=timeout)


def usage_candidates(paths: Paths, *, state: str = "open", limit: int | None = None, timeout: float = 10.0) -> dict:
    params = {"state": state, **({"limit": limit} if limit is not None else {})}
    return _get(paths, "usage", "/v1/candidates", params=params, timeout=timeout)


def list_triggers(paths: Paths, *, timeout: float = 5.0):
    return _get(paths, "orchestration", "/v1/triggers", timeout=timeout)


def add_trigger(paths: Paths, *, name: str, event: str, event_filter: dict, job_template: dict, timeout: float = 10.0) -> dict:
    return _mutate(paths, "orchestration", "POST", "/v1/triggers",
                   json={"name": name, "on": {"event": event, "filter": event_filter}, "job_template": job_template}, timeout=timeout)


def remove_trigger(paths: Paths, trigger_id: str, *, timeout: float = 10.0) -> dict:
    return _mutate(paths, "orchestration", "DELETE", f"/v1/triggers/{trigger_id}", timeout=timeout)


def agent_runs(paths: Paths, project_id: str, *, status: str | None = None, timeout: float = 5.0) -> dict:
    return _get(paths, "agent", f"/v1/runs/{project_id}", params={"status": status} if status else None, timeout=timeout)


def agent_run_status(paths: Paths, project_id: str, run_id: str, *, timeout: float = 5.0) -> dict:
    return _get(paths, "agent", f"/v1/runs/{project_id}/{run_id}", timeout=timeout)


def agent_ci_checks(paths: Paths, project_id: str, *, timeout: float = 5.0) -> dict:
    return _get(paths, "agent", f"/v1/ci_checks/{project_id}", timeout=timeout)


def agent_latest_ci_check(paths: Paths, project_id: str, *, branch: str | None = None, timeout: float = 5.0) -> dict:
    return _get(paths, "agent", f"/v1/ci_checks/{project_id}/latest", params={"branch": branch} if branch else None, timeout=timeout)


def agent_session_status(paths: Paths, project_id: str, *, timeout: float = 5.0) -> dict:
    return _get(paths, "agent", f"/v1/sessions/{project_id}", timeout=timeout)


def stop_job(paths: Paths, job_id: str, *, timeout: float = 10.0) -> dict:
    return _mutate(paths, "orchestration", "POST", f"/v1/jobs/{job_id}/stop", timeout=timeout)


def create_scheduled_task(paths: Paths, *, timeout: float = 10.0, **fields) -> dict:
    return _mutate(paths, "orchestration", "POST", "/v1/scheduled_tasks", json=fields, timeout=timeout)


def list_scheduled_tasks(paths: Paths, *, timeout: float = 5.0):
    return _get(paths, "orchestration", "/v1/scheduled_tasks", timeout=timeout)


def get_scheduled_task(paths: Paths, task_id: str, *, timeout: float = 5.0) -> dict:
    return _get(paths, "orchestration", f"/v1/scheduled_tasks/{task_id}", timeout=timeout)


def edit_scheduled_task(paths: Paths, task_id: str, *, timeout: float = 10.0, **fields) -> dict:
    return _mutate(paths, "orchestration", "PATCH", f"/v1/scheduled_tasks/{task_id}", json=fields, timeout=timeout)


def remove_scheduled_task(paths: Paths, task_id: str, *, timeout: float = 10.0) -> dict:
    return _mutate(paths, "orchestration", "DELETE", f"/v1/scheduled_tasks/{task_id}", timeout=timeout)


def trigger_scheduled_task(paths: Paths, task_id: str, *, timeout: float = 30.0) -> dict:
    return _mutate(paths, "orchestration", "POST", f"/v1/scheduled_tasks/{task_id}/trigger", timeout=timeout)


def cancel_scheduled_task(paths: Paths, task_id: str, *, timeout: float = 10.0) -> dict:
    return _mutate(paths, "orchestration", "POST", f"/v1/scheduled_tasks/{task_id}/cancel", timeout=timeout)


def postpone_scheduled_task(paths: Paths, task_id: str, at: str, *, timeout: float = 10.0) -> dict:
    return _mutate(
        paths, "orchestration", "POST", f"/v1/scheduled_tasks/{task_id}/postpone", json={"at": at}, timeout=timeout
    )


def get_queue_capacity(paths: Paths, queue_key: str, *, timeout: float = 5.0) -> dict:
    return _get(paths, "orchestration", f"/v1/queue_keys/{queue_key}/capacity", timeout=timeout)


def set_queue_capacity(paths: Paths, queue_key: str, capacity: int, *, timeout: float = 10.0) -> dict:
    return _mutate(
        paths, "orchestration", "POST", f"/v1/queue_keys/{queue_key}/capacity",
        json={"capacity": capacity}, timeout=timeout,
    )
