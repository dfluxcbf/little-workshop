"""Resolving a `project_id` to a repo path by asking Registry & Portfolio
over its Pillar Interface — the shared half of "one pillar, fully
working, talking to Registry & Portfolio over a real Pillar Interface"
(docs/milestones.md M3), pulled out once a *second* pillar
(Deployment & Servers, M4) needed the exact same capability.
"""

from __future__ import annotations

from pathlib import Path

import httpx

from common.paths import Paths
from common.pillar_socket import resolve_pillar_socket
from common.uds_client import client_for_socket


class ProjectResolutionError(Exception):
    pass


def resolve_project_path(paths: Paths, project_id: str | None, *, timeout: float = 5.0) -> Path:
    if not project_id:
        raise ProjectResolutionError("this Job type needs a project_id")

    socket_path = resolve_pillar_socket(paths, "registry", timeout=timeout)
    if socket_path is None:
        raise ProjectResolutionError("Registry & Portfolio pillar is unavailable (unreachable, disabled, or faulted)")

    try:
        with client_for_socket(socket_path, timeout=timeout) as client:
            response = client.get(f"/v1/projects/{project_id}")
    except (httpx.ConnectError, httpx.TimeoutException, OSError) as exc:
        raise ProjectResolutionError(f"Registry & Portfolio pillar unreachable: {exc}") from exc

    if response.status_code == 404:
        raise ProjectResolutionError(f"project {project_id!r} is not registered with Registry & Portfolio")
    response.raise_for_status()
    return Path(response.json()["path"])
