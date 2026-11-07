"""The Orchestration queue key of an `agent.run_task` Job. A task works in a worktree of its own
(REQ-000000518), so tasks of one project no longer wait for each other: the key is per branch: the named
branch of a task on an existing one, else the branch a task named like this one gets. A task in a live
checkout (a Windows-hosted Unity project) is still serialized per project by Agentic Development itself."""

from __future__ import annotations

import re


def run_task_queue_key(project_id: str, params: dict | None) -> str:
    params = params or {}
    branching = params.get("branching") or {}
    if branching.get("mode") == "existing" and branching.get("branch"):
        return f"{project_id}:branch:{branching['branch']}"
    slug = re.sub(r"[^a-z0-9]+", "-", str(params.get("title") or "").lower()).strip("-")[:40].rstrip("-") or "task"
    return f"{project_id}:task:{slug}"
