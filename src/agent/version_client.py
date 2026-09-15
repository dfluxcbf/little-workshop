"""Calling Version Control & Release's Pillar Interface directly — a
synchronous subroutine call from within this pillar's own multi-step
workflow (branch preparation, then finishing), not routed through
Orchestration's Job queue the way the *triggering* `agent.run_task` Job
itself was. Mirrors the same "one pillar calling another over a real
PI" pattern Version Control & Release itself established in M3, just
called from a second direction.
"""

from __future__ import annotations

import httpx

from common.paths import Paths
from common.pillar_socket import resolve_pillar_socket
from common.uds_client import client_for_socket


class VersionClientError(Exception):
    pass


def get_config(paths: Paths, project_id: str, *, timeout: float = 5.0) -> dict:
    socket_path = resolve_pillar_socket(paths, "version", timeout=timeout)
    if socket_path is None:
        raise VersionClientError("Version Control & Release pillar is unavailable")
    try:
        with client_for_socket(socket_path, timeout=timeout) as client:
            response = client.get("/v1/config", params={"project_id": project_id})
        response.raise_for_status()
        return response.json()
    except (httpx.ConnectError, httpx.TimeoutException, OSError, httpx.HTTPStatusError) as exc:
        raise VersionClientError(f"could not read version config: {exc}") from exc


def run_job(paths: Paths, project_id: str, job_type: str, params: dict, *, timeout: float = 300.0) -> dict:
    socket_path = resolve_pillar_socket(paths, "version", timeout=5.0)
    if socket_path is None:
        raise VersionClientError("Version Control & Release pillar is unavailable")
    try:
        with client_for_socket(socket_path, timeout=timeout) as client:
            response = client.post(
                f"/v1/jobs/{job_type}", json={"job_id": "agent-flow", "project_id": project_id, "params": params}
            )
        response.raise_for_status()
        body = response.json()
    except (httpx.ConnectError, httpx.TimeoutException, OSError, httpx.HTTPStatusError) as exc:
        raise VersionClientError(f"could not run {job_type}: {exc}") from exc
    if body.get("outcome") != "succeeded":
        raise VersionClientError(f"{job_type} failed: {body.get('error')}")
    return body.get("result") or {}
