"""Resolving a `project_id` to a repo path by asking Registry & Portfolio
over its Pillar Interface — the shared half of "one pillar, fully
working, talking to Registry & Portfolio over a real Pillar Interface"
(docs/milestones.md M3), pulled out here once a *second* pillar
(Deployment & Servers, M4) needed the exact same capability. The
registry pillar's socket is looked up through Core's registry, never
hardcoded (docs/architecture.md's PI "Discovery" rule).
"""

from __future__ import annotations

from pathlib import Path

import httpx

from common.paths import Paths
from common.uds_client import client_for_socket

_UNAVAILABLE_STATUSES = {"disabled", "faulted"}


class ProjectResolutionError(Exception):
    pass


def _registry_socket_via_core(paths: Paths, *, timeout: float) -> Path | None:
    try:
        with client_for_socket(paths.core_socket_path, timeout=timeout) as client:
            response = client.get("/v1/status")
        response.raise_for_status()
    except (httpx.ConnectError, httpx.TimeoutException, OSError, httpx.HTTPStatusError):
        return None
    entry = response.json().get("pillars", {}).get("registry")
    if entry is None or entry["status"] in _UNAVAILABLE_STATUSES:
        return None
    return Path(entry["socket_path"])


def resolve_project_path(paths: Paths, project_id: str | None, *, timeout: float = 5.0) -> Path:
    if not project_id:
        raise ProjectResolutionError("this Job type needs a project_id")

    socket_path = _registry_socket_via_core(paths, timeout=timeout)
    if socket_path is None:
        # Core's registry is the only place a socket path is looked up
        # (docs/architecture.md's PI "Discovery" rule) — no hardcoded
        # fallback path, even though this pillar knows the well-known
        # convention. Registry & Portfolio being unreachable/disabled is
        # a clean failure, not a guess at where it might be anyway.
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
