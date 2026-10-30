"""Asking Claude for text through Agentic Development's `agent.query`: a direct call from inside one
of this pillar's own Jobs (refining a backlog entry, writing its task prompt), as Policy &
Compliance's authoring pipeline does with its own copy of this client (no shared import between
two pillars' `src/` trees). This pillar never runs Claude itself."""

from __future__ import annotations

import httpx

from common.paths import Paths
from common.pillar_socket import resolve_pillar_socket
from common.uds_client import client_for_socket

QUERY_TIMEOUT_SECONDS = 320.0  # agent.query's own ceiling is 300s
CHECKOUT_QUERY_TIMEOUT_SECONDS = 620.0  # and 600s for one that reads the project's checkout


class AgentClientError(Exception):
    pass


def query(paths: Paths, prompt: str, *, job_id: str, project_id: str | None = None, ref: str | None = None,
          read_checkout: bool = False, timeout: float | None = None) -> str:
    """Claude's answer to `prompt`. `job_id` is the Job asking: the call's usage is counted against it, and
    its session can be watched as that Job's peek. `ref` names what the session worked on (a backlog
    entry), so that `forget` can delete its transcript with it. `read_checkout` has Claude answer from
    `project_id`'s checkout, which it can read but not change."""
    socket_path = resolve_pillar_socket(paths, "agent", timeout=5.0)
    if socket_path is None:
        raise AgentClientError("the Agentic Development pillar is unavailable (unreachable, disabled or faulted)")
    params = {"prompt": prompt, **({"ref": ref} if ref else {}), **({"read_checkout": True} if read_checkout else {})}
    timeout = timeout or (CHECKOUT_QUERY_TIMEOUT_SECONDS if read_checkout else QUERY_TIMEOUT_SECONDS)
    try:
        with client_for_socket(socket_path, timeout=timeout) as client:
            response = client.post("/v1/jobs/agent.query", json={"job_id": job_id, "project_id": project_id, "params": params})
        response.raise_for_status()
        body = response.json()
    except (httpx.HTTPError, OSError, ValueError) as exc:
        raise AgentClientError(f"agent.query unreachable: {exc}") from exc
    if body.get("outcome") != "succeeded":
        raise AgentClientError(f"agent.query failed: {body.get('error')}")
    text = (body.get("result") or {}).get("text") or ""
    if not text.strip():
        raise AgentClientError("Claude answered with no text")
    return text.strip()


def forget(paths: Paths, ref: str, *, timeout: float = 30.0) -> int | None:
    """Have the agent delete the transcripts of every Claude session that worked on `ref`: how many it
    deleted, or None when the agent could not be reached (nothing was deleted then)."""
    socket_path = resolve_pillar_socket(paths, "agent", timeout=5.0)
    if socket_path is None:
        return None
    try:
        with client_for_socket(socket_path, timeout=timeout) as client:
            response = client.post("/v1/query-sessions/forget", json={"ref": ref})
        response.raise_for_status()
        return int(response.json().get("deleted", 0))
    except (httpx.HTTPError, OSError, ValueError):
        return None
