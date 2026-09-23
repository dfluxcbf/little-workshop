"""Calling Requirements & Quality's Pillar Interface directly — the same
synchronous-subroutine-call pattern `agent/version_client.py` already
uses for Version Control & Release, applied to a second pillar:
`agent.ci_check`'s own multi-step flow (resolve PR head, run the real
sandboxed test suite, post a GitHub commit status) needs a blocking
`requirements.run_tests` call, not a second trip through Orchestration's
Job queue.
"""

from __future__ import annotations

import httpx

from common.paths import Paths
from common.pillar_socket import resolve_pillar_socket
from common.uds_client import client_for_socket


class RequirementsClientError(Exception):
    pass


def run_job(paths: Paths, project_id: str, job_type: str, params: dict, *, timeout: float = 300.0) -> dict:
    socket_path = resolve_pillar_socket(paths, "requirements", timeout=5.0)
    if socket_path is None:
        raise RequirementsClientError("Requirements & Quality pillar is unavailable")
    try:
        with client_for_socket(socket_path, timeout=timeout) as client:
            response = client.post(
                f"/v1/jobs/{job_type}", json={"job_id": "agent-flow", "project_id": project_id, "params": params}
            )
        response.raise_for_status()
        body = response.json()
    except (httpx.ConnectError, httpx.TimeoutException, OSError, httpx.HTTPStatusError) as exc:
        raise RequirementsClientError(f"could not run {job_type}: {exc}") from exc
    if body.get("outcome") != "succeeded":
        raise RequirementsClientError(f"{job_type} failed: {body.get('error')}")
    return body.get("result") or {}
