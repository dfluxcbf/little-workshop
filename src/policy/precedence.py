"""Scope precedence resolution
(`docs/pillars/policy_compliance.md`'s "Scope levels and precedence"):

    framework policy on this topic          (always wins if one exists)
      > user policy, this project specifically
      > user policy, a project set this project belongs to
      > user policy, all projects

Project-set membership is read live from Registry & Portfolio's Pillar
Interface — this pillar never stores that membership itself.
"""

from __future__ import annotations

import sqlite3
from typing import Callable

from policy import db
from policy.models import CreatedBy, Policy, ScopeLevel

ProjectSetsForProject = Callable[[str], list[str]]


def _is_waived(conn: sqlite3.Connection, policy: Policy, project_id: str) -> bool:
    return db.find_waiver(conn, policy_id=policy.id, project_id=project_id) is not None


def resolve_winning_policy(
    conn: sqlite3.Connection, *, topic: str, project_id: str, project_sets_for_project: ProjectSetsForProject,
) -> Policy | None:
    """Precedence order, per the design: framework > project-specific >
    project-set > all-projects. A disabled policy, or one this project
    holds an explicit waiver against, is skipped — never returned, but
    also never removed from consideration for other projects — falling
    through to whatever the next-lower-precedence policy on this same
    topic is, exactly as if the skipped one didn't exist for this
    project specifically."""
    candidates = db.list_policies(conn, topic=topic)
    if not candidates:
        return None

    member_sets = set(project_sets_for_project(project_id))
    ordered: list[Policy] = []
    ordered.extend(p for p in candidates if p.created_by == CreatedBy.FRAMEWORK)
    ordered.extend(p for p in candidates if p.scope_level == ScopeLevel.PROJECT and p.scope_target == project_id)
    set_scoped = [p for p in candidates if p.scope_level == ScopeLevel.PROJECT_SET and p.scope_target in member_sets]
    # A project belonging to more than one policy-bearing set on the
    # same topic is a genuine ambiguity this design leaves open —
    # resolved deterministically (lowest scope_target), not left to
    # dict/list ordering.
    ordered.extend(sorted(set_scoped, key=lambda p: p.scope_target or ""))
    ordered.extend(p for p in candidates if p.scope_level == ScopeLevel.ALL_PROJECTS)

    for policy in ordered:
        if not policy.enabled:
            continue
        if _is_waived(conn, policy, project_id):
            continue
        return policy
    return None
