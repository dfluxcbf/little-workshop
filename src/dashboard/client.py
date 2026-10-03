"""What the Dashboard talks to: every pillar's Pillar Interface over its
Unix socket. A deliberate duplicate of `cli.client`'s HTTP glue rather
than a shared import — docs/architecture.md's PI section enforces no
shared Python import path between two `src/` trees, a rule this project
has kept between every pair of pillars so far and extends here to its
third thin proxy (CLI, MCP Server, and now Dashboard).

Unlike `cli.client`, nothing here falls back to a direct database/file
read when a daemon is unreachable: the Dashboard holds no domain data of
its own (docs/architecture.md's Components table), so a caller reaching
it through a live HTTP request is already talking to a live process —
if the target pillar's daemon is down, that is simply the answer to
render back (an unreachable-pillar panel), not something to paper over
with a local read. The same reasoning `mcp_server/client.py` already
documents for MCP Server.
"""

from __future__ import annotations

import httpx

from common.paths import Paths
from common.pillar_socket import resolve_pillar_socket
from common.uds_client import client_for_socket


class PillarUnavailable(Exception):
    """Raised whenever a pillar's Pillar Interface can't be reached."""


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
        raise PillarUnavailable(f"{pillar} daemon unreachable") from exc


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
        raise PillarUnavailable("orchestration daemon unreachable") from exc


def _send(paths: Paths, pillar: str, method: str, path: str, *, json: dict | None = None, timeout: float = 30.0) -> dict:
    socket_path = resolve_pillar_socket(paths, pillar, timeout=timeout)
    if socket_path is None:
        raise PillarUnavailable(f"{pillar} daemon unreachable — Core has no socket for it registered")
    try:
        with client_for_socket(socket_path, timeout=timeout) as client:
            response = client.request(method, path, json=json)
        response.raise_for_status()
        return response.json()
    except (httpx.ConnectError, httpx.TimeoutException, OSError, httpx.HTTPStatusError) as exc:
        raise PillarUnavailable(f"{pillar} daemon unreachable or refused the request") from exc


def scheduled_tasks(paths: Paths) -> list[dict]:
    return _get(paths, "orchestration", "/v1/scheduled_tasks")


def get_scheduled_task(paths: Paths, task_id: str) -> dict | None:
    return _get_optional(paths, "orchestration", f"/v1/scheduled_tasks/{task_id}")


def create_scheduled_task(paths: Paths, *, name: str, job_template: dict, schedule: dict) -> dict:
    return _send(paths, "orchestration", "POST", "/v1/scheduled_tasks", json={"name": name, "job_template": job_template, "schedule": schedule})


def remove_scheduled_task(paths: Paths, task_id: str) -> dict:
    return _send(paths, "orchestration", "DELETE", f"/v1/scheduled_tasks/{task_id}")


def list_jobs(paths: Paths, *, status: str | None = None, timeout: float = 5.0) -> list[dict]:
    return _get(paths, "orchestration", "/v1/jobs", params={"status": status} if status else None, timeout=timeout)


def get_job(paths: Paths, job_id: str, *, timeout: float = 5.0) -> dict | None:
    return _get_optional(paths, "orchestration", f"/v1/jobs/{job_id}")


def jobs_steps(paths: Paths, job_ids: list[str], *, timeout: float = 10.0) -> dict[str, list[dict]]:
    """{job id: checklist} for the given Jobs."""
    return _get(paths, "orchestration", "/v1/jobs/steps", params={"ids": ",".join(job_ids)}, timeout=timeout)


def answer_step(paths: Paths, job_id: str, key: str, choice: str) -> dict:
    """The user's answer to a Job step waiting for their attention."""
    return _send(paths, "orchestration", "POST", f"/v1/jobs/{job_id}/steps/{key}/answer", json={"choice": choice})


def job_cleanup_preview(paths: Paths, hours: list[int], *, timeout: float = 10.0) -> dict[str, int]:
    """{"168": how many finished jobs are older than 168 hours, ...} — nothing is deleted."""
    body = _get(paths, "orchestration", "/v1/job-cleanup", params={"hours": ",".join(str(h) for h in hours)}, timeout=timeout)
    return body["prunable"]


def job_cleanup(paths: Paths, older_than_hours: int, *, timeout: float = 60.0) -> dict:
    """Delete finished jobs older than this many hours; returns {deleted, pipeline_runs_deleted, kept_for_safety}."""
    socket_path = resolve_pillar_socket(paths, "orchestration", timeout=timeout)
    if socket_path is None:
        raise PillarUnavailable("orchestration daemon unreachable — Core has no socket for it registered")
    try:
        with client_for_socket(socket_path, timeout=timeout) as client:
            response = client.post("/v1/job-cleanup", json={"older_than_hours": older_than_hours})
        response.raise_for_status()
        return response.json()
    except (httpx.ConnectError, httpx.TimeoutException, OSError, httpx.HTTPStatusError) as exc:
        raise PillarUnavailable("orchestration daemon unreachable") from exc


def list_projects(paths: Paths, *, status: str | None = None, timeout: float = 5.0) -> list[dict]:
    return _get(paths, "registry", "/v1/projects", params={"status": status} if status else None, timeout=timeout)


def list_secrets(paths: Paths, *, timeout: float = 5.0) -> list[dict]:
    return _get(paths, "registry", "/v1/secrets", timeout=timeout)


def policy_list(paths: Paths, *, topic: str | None = None, timeout: float = 5.0) -> list[dict]:
    return _get(paths, "policy", "/v1/policies", params={"topic": topic} if topic else None, timeout=timeout)


def policy_get(paths: Paths, policy_id: str, *, timeout: float = 5.0) -> dict:
    return _get(paths, "policy", f"/v1/policies/{policy_id}", timeout=timeout)


def policy_check_results(paths: Paths, *, timeout: float = 5.0) -> list[dict]:
    return _get(paths, "policy", "/v1/policy_check_results", timeout=timeout)


def policy_drafts(paths: Paths, *, status: str | None = None, timeout: float = 5.0) -> list[dict]:
    return _get(paths, "policy", "/v1/policy_drafts", params={"status": status} if status else None, timeout=timeout)


def policy_draft(paths: Paths, draft_id: str, *, timeout: float = 5.0) -> dict | None:
    return _get_optional(paths, "policy", f"/v1/policy_drafts/{draft_id}", timeout=timeout)


def policy_script_link(paths: Paths, script: str, *, timeout: float = 5.0) -> dict | None:
    """The GitHub link to the exact policy test script in use, or None when it
    only lives in the Policy database (or Deployment is unreachable)."""
    socket_path = resolve_pillar_socket(paths, "deployment", timeout=timeout)
    if socket_path is None:
        return None
    try:
        with client_for_socket(socket_path, timeout=timeout) as client:
            response = client.post("/v1/policy-scripts/locate", json={"script": script})
        response.raise_for_status()
        body = response.json()
    except (httpx.ConnectError, httpx.TimeoutException, OSError, httpx.HTTPStatusError, ValueError):
        return None
    return body if body.get("found") else None


def deployment_status(paths: Paths, project_id: str, *, timeout: float = 5.0) -> dict:
    return _get(paths, "deployment", f"/v1/status/{project_id}", timeout=timeout)


def deployment_pillars(paths: Paths, *, timeout: float = 5.0) -> dict:
    """{"pillars": {name: {commit_sha, updated_at}}, "repo_head": sha|None}."""
    return _get(paths, "deployment", "/v1/pillars", timeout=timeout)


def _get_optional(paths: Paths, pillar: str, path: str, *, params: dict | None = None, timeout: float = 5.0) -> dict | None:
    """Like `_get`, but a 404 is an answer ("nothing there"), not an outage."""
    socket_path = resolve_pillar_socket(paths, pillar, timeout=timeout)
    if socket_path is None:
        raise PillarUnavailable(f"{pillar} daemon unreachable — Core has no socket for it registered")
    try:
        with client_for_socket(socket_path, timeout=timeout) as client:
            response = client.get(path, params=params or {})
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()
    except (httpx.ConnectError, httpx.TimeoutException, OSError, httpx.HTTPStatusError) as exc:
        raise PillarUnavailable(f"{pillar} daemon unreachable") from exc


def deployment_status_optional(paths: Paths, project_id: str) -> dict | None:
    """None when the project has no server manifest configured."""
    return _get_optional(paths, "deployment", f"/v1/status/{project_id}")


def deployment_branches(paths: Paths, project_id: str) -> list[str]:
    try:
        result = _get(paths, "deployment", f"/v1/branches/{project_id}")
    except PillarUnavailable:
        return []
    return result if isinstance(result, list) else result.get("branches", [])


def agent_session_status(paths: Paths, project_id: str) -> dict:
    return _get(paths, "agent", f"/v1/sessions/{project_id}")


def agent_runs(paths: Paths, project_id: str) -> list[dict]:
    return _get(paths, "agent", f"/v1/runs/{project_id}")


def agent_run(paths: Paths, project_id: str, run_id: str) -> dict | None:
    return _get_optional(paths, "agent", f"/v1/runs/{project_id}/{run_id}")


def agent_latest_ci_check(paths: Paths, project_id: str) -> dict | None:
    return _get_optional(paths, "agent", f"/v1/ci_checks/{project_id}/latest")


def list_incidents(paths: Paths, *, status: str | None = None) -> list[dict]:
    return _get(paths, "agent", "/v1/incidents", params={"status": status} if status else None)


def get_incident(paths: Paths, incident_id: str) -> dict | None:
    return _get_optional(paths, "agent", f"/v1/incidents/{incident_id}")


def requirements_report(paths: Paths, project_id: str) -> dict:
    return _get(paths, "requirements", "/v1/report", params={"project_id": project_id}, timeout=10.0)


def requirements_list(paths: Paths, project_id: str) -> list[dict]:
    return _get(paths, "requirements", "/v1/requirements", params={"project_id": project_id}, timeout=10.0)


def requirements_suites(paths: Paths, project_id: str) -> list[dict]:
    return _get(paths, "requirements", "/v1/test_suites", params={"project_id": project_id}, timeout=10.0)


def requirements_test_results(paths: Paths, project_id: str) -> list[dict]:
    return _get(paths, "requirements", "/v1/test_results", params={"project_id": project_id}, timeout=30.0)


def session_name_for(project_id: str) -> str:
    """Mirrors `agent.session_flow.session_name` — duplicated, never imported
    (no shared import between two pillars' `src/` trees)."""
    return f"lws-{project_id.split('/')[-1]}-session"


# -- documentation review (Agentic Development) -------------------------------


class Refused(Exception):
    """The pillar answered, and said no (a 4xx) — `detail` says why."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _call(paths: Paths, pillar: str, method: str, path: str, *, params: dict | None = None, json: dict | None = None,
          timeout: float = 10.0) -> httpx.Response:
    socket_path = resolve_pillar_socket(paths, pillar, timeout=timeout)
    if socket_path is None:
        raise PillarUnavailable(f"{pillar} daemon unreachable — Core has no socket for it registered")
    try:
        with client_for_socket(socket_path, timeout=timeout) as client:
            response = client.request(method, path, params=params or {}, json=json)
    except (httpx.ConnectError, httpx.TimeoutException, OSError) as exc:
        raise PillarUnavailable(f"{pillar} daemon unreachable") from exc
    if 400 <= response.status_code < 500:
        try:
            detail = response.json().get("detail", response.text)
        except ValueError:
            detail = response.text
        raise Refused(response.status_code, str(detail))
    if response.status_code >= 500:
        raise PillarUnavailable(f"{pillar} daemon failed ({response.status_code})")
    return response


def docs_overview(paths: Paths, project_id: str) -> dict:
    """{branch, branches, dirty, files: [{path, open, done}]}"""
    return _call(paths, "agent", "GET", f"/v1/docs/{project_id}").json()


def doc_file(paths: Paths, project_id: str, path: str) -> dict:
    return _call(paths, "agent", "GET", f"/v1/docs/{project_id}/file", params={"path": path}).json()


def doc_image(paths: Paths, project_id: str, path: str) -> tuple[bytes, str]:
    response = _call(paths, "agent", "GET", f"/v1/docs/{project_id}/image", params={"path": path})
    return response.content, response.headers.get("content-type", "application/octet-stream")


def review_comments(paths: Paths, project_id: str, *, branch: str | None = None, path: str | None = None) -> list[dict]:
    params = {k: v for k, v in {"branch": branch, "path": path}.items() if v is not None}
    return _call(paths, "agent", "GET", f"/v1/reviews/{project_id}", params=params).json()["comments"]


def put_review_comment(paths: Paths, project_id: str, comment_id: str, fields: dict) -> dict:
    return _call(paths, "agent", "PUT", f"/v1/reviews/{project_id}/comments/{comment_id}", json=fields).json()


def delete_review_comment(paths: Paths, project_id: str, comment_id: str) -> dict:
    return _call(paths, "agent", "DELETE", f"/v1/reviews/{project_id}/comments/{comment_id}").json()


def doc_updates(paths: Paths, project_id: str) -> list[dict]:
    return _get(paths, "agent", f"/v1/doc_updates/{project_id}")


def stop_job(paths: Paths, job_id: str) -> dict:
    """Stop a running Job, or cancel one still queued: {stopped, reason}."""
    return _send(paths, "orchestration", "POST", f"/v1/jobs/{job_id}/stop")


def confirm_gated_job(paths: Paths, job_id: str) -> dict:
    """Dispatch a policy-gated Job that is pending_confirmation despite the failure: {confirmed, ...}."""
    return _send(paths, "orchestration", "POST", f"/v1/jobs/{job_id}/policy_confirm")


def fail_gated_job(paths: Paths, job_id: str) -> dict:
    """Settle a policy-gated Job as failed (unconfirmed) and release its queue: {timed_out, ...}."""
    return _send(paths, "orchestration", "POST", f"/v1/jobs/{job_id}/policy_confirm_timeout")


def edit_scheduled_task(paths: Paths, task_id: str, **fields) -> dict:
    return _send(paths, "orchestration", "PATCH", f"/v1/scheduled_tasks/{task_id}", json=fields)
