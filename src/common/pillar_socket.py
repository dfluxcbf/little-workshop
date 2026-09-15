"""Looking up any pillar's socket path through Core's registry — the
general form of what `common/registry_client.py` originally did only
for Registry & Portfolio, pulled out once a second caller (M5's
self-redeploy dispatch worker, resolving Orchestration's socket to call
its Job-completion callback) needed the identical lookup for a
different pillar. Core's registry is the only place a socket path is
looked up (docs/architecture.md's PI "Discovery" rule) — never
hardcoded, no matter which pillar is being resolved.
"""

from __future__ import annotations

from pathlib import Path

import httpx

from common.paths import Paths
from common.uds_client import client_for_socket

UNAVAILABLE_STATUSES = {"disabled", "faulted"}


def resolve_pillar_socket(paths: Paths, pillar: str, *, timeout: float = 5.0) -> Path | None:
    """The socket path for `pillar`, or None if Core is unreachable, or
    doesn't know about that pillar, or reports it disabled/faulted."""
    try:
        with client_for_socket(paths.core_socket_path, timeout=timeout) as client:
            response = client.get("/v1/status")
        response.raise_for_status()
    except (httpx.ConnectError, httpx.TimeoutException, OSError, httpx.HTTPStatusError):
        return None
    entry = response.json().get("pillars", {}).get(pillar)
    if entry is None or entry["status"] in UNAVAILABLE_STATUSES:
        return None
    return Path(entry["socket_path"])
