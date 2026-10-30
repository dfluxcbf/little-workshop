"""Calling Agentic Development's `agent.query` directly — a synchronous
subroutine call from within this pillar's own multi-step authoring
pipeline (Gates 1/3b, script generation), not routed through
Orchestration's Job queue. Mirrors `agent/version_client.py`'s own
`run_job` — "one pillar calling another over a real PI," the same
pattern, just this pillar's own use of it. Every Claude CLI call in the
authoring pipeline goes through here — this pillar never reimplements
agentic execution locally.
"""

from __future__ import annotations

import httpx

from common.job_steps import current_reporter
from common.paths import Paths
from common.pillar_socket import resolve_pillar_socket
from common.uds_client import client_for_socket


class AgentClientError(Exception):
    pass


def query(paths: Paths, prompt: str, *, timeout: float = 300.0) -> str:
    """Claude's answer. Asked inside a Job (the authoring pipeline runs under its Job's step reporter),
    the session is recorded against that Job, so its page can show the session as it works."""
    reporter = current_reporter()
    job_id = reporter.job_id if reporter is not None else "policy-authoring"
    socket_path = resolve_pillar_socket(paths, "agent", timeout=5.0)
    if socket_path is None:
        raise AgentClientError("Agentic Development pillar is unavailable (unreachable, disabled, or faulted)")
    try:
        with client_for_socket(socket_path, timeout=timeout) as client:
            response = client.post(
                "/v1/jobs/agent.query",
                json={"job_id": job_id, "params": {"prompt": prompt}},
            )
        response.raise_for_status()
        body = response.json()
    except (httpx.ConnectError, httpx.TimeoutException, OSError, httpx.HTTPStatusError) as exc:
        raise AgentClientError(f"agent.query unreachable: {exc}") from exc
    if body.get("outcome") != "succeeded":
        raise AgentClientError(f"agent.query failed: {body.get('error')}")
    return body.get("result", {}).get("text", "")
