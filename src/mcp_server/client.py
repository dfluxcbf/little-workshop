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


def create_job(paths: Paths, *, timeout: float = 10.0, **fields) -> dict:
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


def list_jobs(paths: Paths, *, status: str | None = None, timeout: float = 5.0):
    return _get(paths, "orchestration", "/v1/jobs", params={"status": status} if status else None, timeout=timeout)


def job_status(paths: Paths, job_id: str, *, timeout: float = 5.0) -> dict:
    return _get(paths, "orchestration", f"/v1/jobs/{job_id}", timeout=timeout)


def list_projects(paths: Paths, *, status: str | None = None, timeout: float = 5.0):
    return _get(paths, "registry", "/v1/projects", params={"status": status} if status else None, timeout=timeout)


def get_project(paths: Paths, name: str, *, timeout: float = 5.0) -> dict:
    return _get(paths, "registry", f"/v1/projects/{name}", timeout=timeout)


def version_status(paths: Paths, kind: str, project_id: str, *, timeout: float = 5.0) -> dict:
    return _get(paths, "version", f"/v1/status/{kind}", params={"project_id": project_id}, timeout=timeout)


def version_config(paths: Paths, project_id: str, *, timeout: float = 5.0) -> dict:
    return _get(paths, "version", "/v1/config", params={"project_id": project_id}, timeout=timeout)


def version_release_candidates(paths: Paths, project_id: str, *, timeout: float = 5.0) -> dict:
    return _get(paths, "version", "/v1/release/candidates", params={"project_id": project_id}, timeout=timeout)


def deployment_status(paths: Paths, project_id: str, *, timeout: float = 5.0) -> dict:
    return _get(paths, "deployment", f"/v1/status/{project_id}", timeout=timeout)


def deployment_history(paths: Paths, project_id: str, *, timeout: float = 5.0) -> dict:
    return _get(paths, "deployment", f"/v1/deployments/{project_id}", timeout=timeout)


def deployment_confirmation(paths: Paths, project_id: str, *, timeout: float = 5.0) -> dict:
    return _get(paths, "deployment", f"/v1/confirmations/{project_id}", timeout=timeout)


def deployment_branches(paths: Paths, project_id: str, *, timeout: float = 5.0) -> dict:
    return _get(paths, "deployment", f"/v1/branches/{project_id}", timeout=timeout)


def agent_runs(paths: Paths, project_id: str, *, status: str | None = None, timeout: float = 5.0) -> dict:
    return _get(paths, "agent", f"/v1/runs/{project_id}", params={"status": status} if status else None, timeout=timeout)


def agent_run_status(paths: Paths, project_id: str, run_id: str, *, timeout: float = 5.0) -> dict:
    return _get(paths, "agent", f"/v1/runs/{project_id}/{run_id}", timeout=timeout)
