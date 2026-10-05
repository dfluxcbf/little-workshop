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

from datetime import datetime

import httpx

from common import origin as origins
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
    fields.setdefault("origin", origins.UI_JOB)  # a Job triggered through the UI: never bypassable
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


def _job_filters(status=None, types=None, project_id=None, created_by=None, since=None, until=None, updated_since=None,
                 ids=None, q=None) -> dict:
    """Orchestration's job filters as query params: a status or type may be one or several; times are datetimes."""
    def several(value):
        return ",".join([value] if isinstance(value, str) else list(value)) if value is not None else None

    params = {"status": several(status), "type": several(types), "project_id": project_id, "created_by": created_by,
              "since": since.isoformat() if since else None, "until": until.isoformat() if until else None,
              "updated_since": updated_since.isoformat() if updated_since else None, "ids": several(ids), "q": q or None}
    return {key: value for key, value in params.items() if value is not None}


def list_jobs(paths: Paths, *, summary: bool = False, newest_first: bool = False, limit: int | None = None,
              timeout: float = 5.0, **filters) -> list[dict]:
    """Jobs as Orchestration selects them (`_job_filters`). A list reads summaries (`summary=True`: each Job's
    brief in place of its params and result) and only the Jobs it shows; a page that shows one Job in
    full reads it with `get_job`."""
    if filters.get("ids") is not None and not list(filters["ids"]):
        return []
    params = _job_filters(**filters)
    if summary:
        params["view"] = "summary"
    if newest_first:
        params["order"] = "newest"
    if limit is not None:
        params["limit"] = limit
    return _get(paths, "orchestration", "/v1/jobs", params=params or None, timeout=timeout)


def job_counts(paths: Paths, *, timeout: float = 5.0, **filters) -> dict[str, int]:
    """{status: how many Jobs} among those the filters select (`_job_filters`)."""
    return _get(paths, "orchestration", "/v1/jobs/counts", params=_job_filters(**filters) or None, timeout=timeout)


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


def policy_list(paths: Paths, *, topic: str | None = None, summary: bool = False, timeout: float = 5.0) -> list[dict]:
    """Every policy; a summary leaves out each one's test script (what a list of policies reads)."""
    params = {"topic": topic, "view": "summary" if summary else None}
    return _get(paths, "policy", "/v1/policies", params={k: v for k, v in params.items() if v is not None}, timeout=timeout)


def policy_get(paths: Paths, policy_id: str, *, timeout: float = 5.0) -> dict:
    return _get(paths, "policy", f"/v1/policies/{policy_id}", timeout=timeout)


def policy_check_results(paths: Paths, *, latest: bool = False, outcome: str | None = None, policy_id: str | None = None,
                         checked_since: datetime | None = None, timeout: float = 5.0) -> list[dict]:
    """Check results, oldest first: `latest` only the newest of each policy and project (with `outcome`,
    those of that outcome), `checked_since` only those recorded since. A Policy pillar from before these
    answers with the whole history, so a caller still folds them to the newest."""
    params = {"latest": "true" if latest else None, "outcome": outcome, "policy_id": policy_id,
              "checked_since": checked_since.isoformat() if checked_since else None}
    return _get(paths, "policy", "/v1/policy_check_results", params={k: v for k, v in params.items() if v is not None}, timeout=timeout)


def policy_bypass(paths: Paths, *, timeout: float = 3.0) -> dict:
    """The `bypass_policies` flag: `{"bypass_policies": bool, "remaining_seconds", "expires_at", "enabled_by"}`."""
    return _get(paths, "policy", "/v1/bypass", timeout=timeout)


def policy_bypass_off(paths: Paths, *, timeout: float = 5.0) -> dict:
    """The Dashboard's button: the flag back to FALSE now. It can only lower the flag, never raise it."""
    return _send(paths, "policy", "POST", "/v1/bypass", json={"enabled": False, "origin": origins.UI_JOB, "actor": "dashboard"}, timeout=timeout)


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


def usage_summary(paths: Paths, *, week: str | None = None, project: str | None = None, origin: str | None = None) -> dict:
    params = {key: value for key, value in (("week", week), ("project", project), ("origin", origin)) if value}
    return _get(paths, "usage", "/v1/summary", params=params, timeout=20.0)


def usage_findings(paths: Paths, *, project: str | None = None, state: str = "open") -> list[dict]:
    params = {"state": state, **({"project": project} if project else {})}
    return _get(paths, "usage", "/v1/findings", params=params, timeout=10.0)


def set_usage_finding_state(paths: Paths, finding_id: str, state: str) -> dict:
    return _send(paths, "usage", "POST", f"/v1/findings/{finding_id}/state", json={"state": state})


def usage_candidates(paths: Paths, *, state: str = "all") -> list[dict]:
    return _get(paths, "usage", "/v1/candidates", params={"state": state}, timeout=10.0)


def set_usage_candidate(paths: Paths, candidate_id: str, **fields) -> dict:
    return _send(paths, "usage", "POST", f"/v1/candidates/{candidate_id}", json=fields)


def triggers(paths: Paths) -> list[dict]:
    return _get(paths, "orchestration", "/v1/triggers")


def trigger_action(paths: Paths, method: str, path: str = "", json: dict | None = None):
    return _send(paths, "orchestration", method, f"/v1/triggers{path}", json=json)


def usage_estimate_method(paths: Paths) -> dict | None:
    return _get(paths, "usage", "/v1/estimate_method").get("method")


def usage_explorer(paths: Paths, *, days: int = 28) -> dict:
    return _get(paths, "usage", "/v1/explorer", params={"days": days}, timeout=10.0)


def usage_doctor(paths: Paths) -> dict | None:
    return _get(paths, "usage", "/v1/doctor").get("report")


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


def stop_job(paths: Paths, job_id: str, *, force: bool = False) -> dict:
    """Stop a running Job, or cancel one still queued: {stopped, reason}. `force` settles it as stopped
    whatever its pillar answers (the user decided, after the page warned what that means)."""
    return _send(paths, "orchestration", "POST", f"/v1/jobs/{job_id}/stop", json={"force": True} if force else None)


def confirm_gated_job(paths: Paths, job_id: str) -> dict:
    """Dispatch a policy-gated Job that is pending_confirmation despite the failure: {confirmed, ...}."""
    return _send(paths, "orchestration", "POST", f"/v1/jobs/{job_id}/policy_confirm")


def fail_gated_job(paths: Paths, job_id: str) -> dict:
    """Settle a policy-gated Job as failed (unconfirmed) and release its queue: {timed_out, ...}."""
    return _send(paths, "orchestration", "POST", f"/v1/jobs/{job_id}/policy_confirm_timeout")


def edit_scheduled_task(paths: Paths, task_id: str, **fields) -> dict:
    return _send(paths, "orchestration", "PATCH", f"/v1/scheduled_tasks/{task_id}", json=fields)


# -- backlog (Registry & Portfolio) ---------------------------------------------


def backlog_list(paths: Paths, project: str | None = None) -> list[dict]:
    return _get(paths, "registry", "/v1/backlog", params={"project_name": project} if project else None)


def backlog_get(paths: Paths, entry_id: str) -> dict | None:
    return _get_optional(paths, "registry", f"/v1/backlog/{entry_id}")


def backlog_version(paths: Paths, *, timeout: float = 3.0) -> str:
    return _get(paths, "registry", "/v1/backlog/version", timeout=timeout)["version"]


def backlog_add(paths: Paths, project: str, text: str) -> dict:
    """Refused (4xx, `Refused`) for an empty entry or an unregistered project."""
    return _call(paths, "registry", "POST", "/v1/backlog", json={"project_name": project, "primary_entry": text}).json()


def backlog_edit(paths: Paths, entry_id: str, **fields) -> dict:
    return _call(paths, "registry", "PATCH", f"/v1/backlog/{entry_id}", json=fields).json()


def backlog_delete(paths: Paths, entry_id: str) -> dict:
    return _call(paths, "registry", "DELETE", f"/v1/backlog/{entry_id}").json()


def usage_candidate_task(paths: Paths, candidate_id: str) -> dict | None:
    """The task "Make it a task" fills New task with: {project_id, title, intent, review_mode, description}."""
    return _get_optional(paths, "usage", f"/v1/candidates/{candidate_id}/task", timeout=10.0)


def usage_candidate_task_started(paths: Paths, candidate_id: str, job_id: str | None) -> dict:
    return _send(paths, "usage", "POST", f"/v1/candidates/{candidate_id}/task_started", json={"job_id": job_id})


# -- what the live pages watch (dashboard/live.py) --------------------------------


def orchestration_changes(paths: Paths, *, after: int | None, boot: str | None, timeout: float = 0.0) -> dict:
    """Orchestration's change feed: {boot, seq, changes, reset}, waiting up to `timeout` seconds for a change."""
    params = {"timeout": timeout, **({"after": after} if after is not None else {}), **({"boot": boot} if boot else {})}
    return _get(paths, "orchestration", "/v1/changes", params=params, timeout=timeout + 10.0)


def usage_version(paths: Paths, *, timeout: float = 3.0) -> str:
    return _get(paths, "usage", "/v1/version", timeout=timeout)["version"]


def incidents_version(paths: Paths, *, timeout: float = 3.0) -> str:
    return _get(paths, "agent", "/v1/incidents/version", timeout=timeout)["version"]


def agent_peek(paths: Paths, job_id: str, *, after: int = 0, limit: int = 200, timeout: float = 5.0) -> dict | None:
    """What a task's (or an update-doc job's) Claude has done so far, from its transcript: {available, running,
    events, next, skipped}; `after` is where the caller's copy ends (-1: only where it ends now). None for a Job
    the agent has no session for."""
    return _get_optional(paths, "agent", f"/v1/peek/{job_id}", params={"after": after, "limit": limit}, timeout=timeout)


def agent_remote_control(paths: Paths, job_id: str, *, project_id: str = "") -> dict | None:
    """Remote Control of a Job's running Claude session: {running, active, can_activate, can_deactivate,
    already_set_up, session_name, started_at, stopped_at}. None for a Job that has no Claude session."""
    return _get_optional(paths, "agent", f"/v1/remote-control/{job_id}", params={"project_id": project_id})


class RemoteControlRefused(Exception):
    """The agent refused to (de)activate remote control; the message says why."""


def agent_remote_control_act(paths: Paths, job_id: str, action: str, *, project_id: str = "") -> dict:
    """`action` is "activate" (resume the session with remote control) or "deactivate" (drop only that connection)."""
    socket_path = resolve_pillar_socket(paths, "agent", timeout=30.0)
    if socket_path is None:
        raise PillarUnavailable("agent daemon unreachable — Core has no socket for it registered")
    try:
        with client_for_socket(socket_path, timeout=30.0) as client:
            response = client.post(f"/v1/remote-control/{job_id}/{action}", params={"project_id": project_id})
    except (httpx.ConnectError, httpx.TimeoutException, OSError) as exc:
        raise PillarUnavailable("agent daemon unreachable") from exc
    if response.status_code in (404, 409):
        raise RemoteControlRefused(str(response.json().get("detail") or "refused"))
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise PillarUnavailable("agent daemon refused the request") from exc
    return response.json()


def agent_run_for_job(paths: Paths, job_id: str) -> dict | None:
    """The task (agent run) an `agent.run_task` Job started, if it has one yet."""
    return _get_optional(paths, "agent", f"/v1/runs/by-job/{job_id}")
