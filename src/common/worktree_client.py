"""Calling Deployment & Servers' worktree-provisioning Pillar Interface
(`POST /v1/worktrees`, `DELETE /v1/worktrees/{id}`) — the shared
capability `deployment/worktrees.py` was built to offer starting in M4
("the thing Requirements & Quality's sandboxed test execution and
Policy & Compliance's test-script sandbox both call into rather than
each provisioning their own"), reached like any other cross-pillar call:
over a real Pillar Interface, discovered through Core, never a shared
Python import into `deployment/`'s own tree.
"""

from __future__ import annotations

from pathlib import Path

import httpx

from common.paths import Paths
from common.pillar_socket import resolve_pillar_socket
from common.uds_client import client_for_socket


class WorktreeUnavailable(Exception):
    pass


def provision_worktree(
    paths: Paths, project_id: str, commitish: str, *, timeout: float = 30.0
) -> tuple[str, Path]:
    socket_path = resolve_pillar_socket(paths, "deployment", timeout=timeout)
    if socket_path is None:
        raise WorktreeUnavailable("Deployment & Servers pillar is unavailable (unreachable, disabled, or faulted)")
    try:
        with client_for_socket(socket_path, timeout=timeout) as client:
            response = client.post("/v1/worktrees", json={"project_id": project_id, "commitish": commitish})
        response.raise_for_status()
    except (httpx.ConnectError, httpx.TimeoutException, OSError, httpx.HTTPStatusError) as exc:
        raise WorktreeUnavailable(f"Deployment & Servers pillar unreachable: {exc}") from exc
    payload = response.json()
    return payload["worktree_id"], Path(payload["path"])


def release_worktree(paths: Paths, project_id: str, worktree_id: str, *, timeout: float = 30.0) -> None:
    socket_path = resolve_pillar_socket(paths, "deployment", timeout=timeout)
    if socket_path is None:
        raise WorktreeUnavailable("Deployment & Servers pillar is unavailable (unreachable, disabled, or faulted)")
    try:
        with client_for_socket(socket_path, timeout=timeout) as client:
            response = client.delete("/v1/worktrees/{}".format(worktree_id), params={"project_id": project_id})
        response.raise_for_status()
    except (httpx.ConnectError, httpx.TimeoutException, OSError, httpx.HTTPStatusError) as exc:
        raise WorktreeUnavailable(f"Deployment & Servers pillar unreachable: {exc}") from exc
