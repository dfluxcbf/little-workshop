"""Calling Version Control & Release's own Pillar Interface directly
(never through Orchestration) to initialize gitflow tracking for a
project this pillar just bootstrapped — closing M16's
`bootstrap-little-project` parity gap: the old tool always ran `lvx
init` as part of `create`, and little-workshop's own `version` pillar
already writes a byte-compatible `.lvx/config.json`
(`version/schema.py`'s own docstring). A direct Pillar Interface call,
the same pattern `policy/registry_client.py` already uses for its own
cross-pillar reads — never a Python import of `version`'s own code.

Best-effort, by design: an unreachable Version pillar degrades to "this
new project doesn't have gitflow tracking initialized yet," never a
hard failure of the bootstrap itself — the same "documented
simplification" every other cross-pillar dependency in this project
already accepts rather than blocking on.
"""

from __future__ import annotations

import httpx

from common.paths import Paths
from common.pillar_socket import resolve_pillar_socket
from common.uds_client import client_for_socket


def init_version_tracking(
    paths: Paths, project_id: str, *, production_branch: str = "master", integration_branch: str = "dev", timeout: float = 10.0,
) -> bool:
    """Returns True if `.lvx/config.json` was genuinely written, False if
    Version Control & Release couldn't be reached — never raises."""
    socket_path = resolve_pillar_socket(paths, "version", timeout=timeout)
    if socket_path is None:
        return False
    try:
        with client_for_socket(socket_path, timeout=timeout) as client:
            response = client.post(
                "/v1/jobs/version.init",
                json={
                    "job_id": "registry-bootstrap-version-init", "project_id": project_id,
                    "params": {"production_branch": production_branch, "integration_branch": integration_branch},
                },
            )
        response.raise_for_status()
    except (httpx.ConnectError, httpx.TimeoutException, OSError, httpx.HTTPStatusError):
        return False
    return response.json().get("outcome") == "succeeded"
