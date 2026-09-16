"""Reading Registry & Portfolio's own data this pillar needs but never
stores itself: every registered project (to fan `policy.check_all` out
across an all-projects-scoped policy) and project-set membership (to
resolve which projects a project-set-scoped policy covers). A direct
Pillar Interface read, the same pattern `common/registry_client.py`
already uses for a single project's path.
"""

from __future__ import annotations

import httpx

from common.paths import Paths
from common.pillar_socket import resolve_pillar_socket
from common.uds_client import client_for_socket


class RegistryReadError(Exception):
    pass


def list_active_project_ids(paths: Paths, *, timeout: float = 10.0) -> list[str]:
    socket_path = resolve_pillar_socket(paths, "registry", timeout=timeout)
    if socket_path is None:
        raise RegistryReadError("Registry & Portfolio pillar is unavailable")
    try:
        with client_for_socket(socket_path, timeout=timeout) as client:
            response = client.get("/v1/projects", params={"status": "active"})
        response.raise_for_status()
    except (httpx.ConnectError, httpx.TimeoutException, OSError, httpx.HTTPStatusError) as exc:
        raise RegistryReadError(f"Registry & Portfolio unreachable: {exc}") from exc
    return [p["name"] for p in response.json()]


def project_sets_containing(paths: Paths, project_id: str, *, timeout: float = 10.0) -> list[str]:
    socket_path = resolve_pillar_socket(paths, "registry", timeout=timeout)
    if socket_path is None:
        raise RegistryReadError("Registry & Portfolio pillar is unavailable")
    try:
        with client_for_socket(socket_path, timeout=timeout) as client:
            response = client.get("/v1/project_sets")
        response.raise_for_status()
    except (httpx.ConnectError, httpx.TimeoutException, OSError, httpx.HTTPStatusError) as exc:
        raise RegistryReadError(f"Registry & Portfolio unreachable: {exc}") from exc
    return [s["name"] for s in response.json() if project_id in s.get("project_names", [])]


def project_set_members(paths: Paths, set_name: str, *, timeout: float = 10.0) -> list[str]:
    socket_path = resolve_pillar_socket(paths, "registry", timeout=timeout)
    if socket_path is None:
        raise RegistryReadError("Registry & Portfolio pillar is unavailable")
    try:
        with client_for_socket(socket_path, timeout=timeout) as client:
            response = client.get(f"/v1/project_sets/{set_name}")
        if response.status_code == 404:
            return []
        response.raise_for_status()
    except (httpx.ConnectError, httpx.TimeoutException, OSError, httpx.HTTPStatusError) as exc:
        raise RegistryReadError(f"Registry & Portfolio unreachable: {exc}") from exc
    return response.json().get("project_names", [])
