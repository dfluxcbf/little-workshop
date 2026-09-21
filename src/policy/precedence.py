"""Which policy wins on a topic for a project
(`docs/pillars/policy_compliance.md`'s "Scope levels and precedence").

A policy is a candidate for a project only if the user has *selected* that
project for it (`policy_projects`). Among the candidates, authoring scope
still orders them:

    framework policy on this topic          (always wins if one exists)
      > user policy authored for this project specifically
      > user policy authored for a project set
      > user policy authored for all projects

A disabled policy is skipped, falling through to the next one on the same
topic exactly as if it did not exist for this project.
"""

from __future__ import annotations

import sqlite3

from policy import db
from policy.models import CreatedBy, Policy, ScopeLevel

_SCOPE_RANK = {ScopeLevel.PROJECT: 1, ScopeLevel.PROJECT_SET: 2, ScopeLevel.ALL_PROJECTS: 3}


def resolve_winning_policy(conn: sqlite3.Connection, *, topic: str, project_id: str) -> Policy | None:
    candidates = [
        p for p in db.list_policies(conn, topic=topic)
        if p.enabled and any(sel.project_id == project_id for sel in p.projects)
    ]
    # Framework first, then narrower authoring scope; ties broken by scope
    # target so the outcome never depends on list ordering.
    candidates.sort(key=lambda p: (p.created_by != CreatedBy.FRAMEWORK, _SCOPE_RANK[p.scope_level], p.scope_target or ""))
    return candidates[0] if candidates else None


def gating_enabled_for(policy: Policy, project_id: str) -> bool:
    """Per-project gating switch; a project not selected has none to enable."""
    return any(sel.project_id == project_id and sel.gating_enabled for sel in policy.projects)
