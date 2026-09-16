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


def resolve_winning_policy(
    conn: sqlite3.Connection, *, topic: str, project_id: str, project_sets_for_project: ProjectSetsForProject,
) -> Policy | None:
    candidates = db.list_policies(conn, topic=topic)
    if not candidates:
        return None

    framework = next((p for p in candidates if p.created_by == CreatedBy.FRAMEWORK), None)
    if framework is not None:
        return framework

    project_specific = next(
        (p for p in candidates if p.scope_level == ScopeLevel.PROJECT and p.scope_target == project_id), None
    )
    if project_specific is not None:
        return project_specific

    member_sets = set(project_sets_for_project(project_id))
    if member_sets:
        set_scoped = [p for p in candidates if p.scope_level == ScopeLevel.PROJECT_SET and p.scope_target in member_sets]
        if set_scoped:
            # A project belonging to more than one policy-bearing set on
            # the same topic is a genuine ambiguity this design leaves
            # open — resolved deterministically (lowest scope_target),
            # not left to dict/list ordering.
            return sorted(set_scoped, key=lambda p: p.scope_target or "")[0]

    all_projects = next((p for p in candidates if p.scope_level == ScopeLevel.ALL_PROJECTS), None)
    return all_projects
