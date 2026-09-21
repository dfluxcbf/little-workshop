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


def list_jobs(paths: Paths, *, status: str | None = None, timeout: float = 5.0) -> list[dict]:
    return _get(paths, "orchestration", "/v1/jobs", params={"status": status} if status else None, timeout=timeout)


def list_projects(paths: Paths, *, status: str | None = None, timeout: float = 5.0) -> list[dict]:
    return _get(paths, "registry", "/v1/projects", params={"status": status} if status else None, timeout=timeout)


def policy_list(paths: Paths, *, topic: str | None = None, timeout: float = 5.0) -> list[dict]:
    return _get(paths, "policy", "/v1/policies", params={"topic": topic} if topic else None, timeout=timeout)


def policy_get(paths: Paths, policy_id: str, *, timeout: float = 5.0) -> dict:
    return _get(paths, "policy", f"/v1/policies/{policy_id}", timeout=timeout)


def policy_list_waivers(paths: Paths, *, policy_id: str | None = None, project_id: str | None = None, timeout: float = 5.0) -> list[dict]:
    params = {k: v for k, v in {"policy_id": policy_id, "project_id": project_id}.items() if v}
    return _get(paths, "policy", "/v1/policy_waivers", params=params, timeout=timeout)


def deployment_status(paths: Paths, project_id: str, *, timeout: float = 5.0) -> dict:
    return _get(paths, "deployment", f"/v1/status/{project_id}", timeout=timeout)


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


def requirements_report(paths: Paths, project_id: str) -> dict:
    return _get(paths, "requirements", "/v1/report", params={"project_id": project_id}, timeout=10.0)


def session_name_for(project_id: str) -> str:
    """Mirrors `agent.session_flow.session_name` — duplicated, never imported
    (no shared import between two pillars' `src/` trees)."""
    return f"lws-{project_id.split('/')[-1]}-session"
