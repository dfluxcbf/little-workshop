"""The launch environment of a Claude this pillar starts, from the Usage & Efficiency pillar: the
telemetry variables that point the run at its intake, plus resource attributes saying which project,
Job and run it is, so its usage is attributed exactly (docs/pillars/usage.md, "Attribution").

Usage & Efficiency is optional. Switched off or unreachable, there is no environment to add and the
run starts as it always did."""

from __future__ import annotations

import httpx

from common.paths import Paths
from common.pillar_socket import resolve_pillar_socket
from common.uds_client import client_for_socket

PILLAR = "usage"


def launch_env(
    paths: Paths, *, project: str | None = None, job_id: str | None = None, job_type: str | None = None,
    run_id: str | None = None, timeout: float = 2.0,
) -> dict[str, str]:
    socket_path = resolve_pillar_socket(paths, PILLAR, timeout=timeout)
    if socket_path is None:
        return {}
    try:
        with client_for_socket(socket_path, timeout=timeout) as client:
            response = client.get("/v1/config")
        response.raise_for_status()
        env = dict(response.json().get("launch_env") or {})
    except (httpx.HTTPError, OSError, ValueError):
        return {}
    if not env:
        return {}
    tags = {"lws.project": project, "lws.job": job_id, "lws.job_type": job_type, "lws.run": run_id}
    attributes = ",".join(f"{key}={value}" for key, value in tags.items() if value)
    if attributes:
        env["OTEL_RESOURCE_ATTRIBUTES"] = attributes
    return env
