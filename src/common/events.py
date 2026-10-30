"""Telling Orchestration that something happened, so a workshop trigger can react
(docs/pillars/orchestration.md, "Triggers"). Best effort: an event nobody could be told about is
dropped, and the pillar that reported it carries on."""

from __future__ import annotations

import threading

import httpx

from common.paths import Paths
from common.pillar_socket import resolve_pillar_socket
from common.uds_client import client_for_socket


def emit(paths: Paths, name: str, project_id: str | None = None, data: dict | None = None, *, timeout: float = 30.0) -> bool:
    socket_path = resolve_pillar_socket(paths, "orchestration", timeout=2.0)
    if socket_path is None:
        return False
    try:
        with client_for_socket(socket_path, timeout=timeout) as client:
            response = client.post("/v1/events", json={"name": name, "project_id": project_id, "data": data or {}})
        return response.status_code == 200
    except (httpx.HTTPError, OSError):
        return False


def emit_in_background(paths: Paths, name: str, project_id: str | None = None, data: dict | None = None) -> None:
    """For a caller that must answer at once (an intake route, a Job handler Orchestration is waiting on)."""
    threading.Thread(target=emit, args=(paths, name, project_id, data), daemon=True).start()
