"""Policy & Compliance's own models — `docs/pillars/policy_compliance.md`'s
"Data model" section: `policies` and `policy_check_results`, both real
tables this pillar owns. `project_sets` is deliberately NOT here — it's
Registry & Portfolio's own table, reached through that pillar's Pillar
Interface whenever a project-set-scoped policy's targets need resolving.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ScopeLevel(str, Enum):
    PROJECT = "project"
    PROJECT_SET = "project_set"
    ALL_PROJECTS = "all_projects"


class CreatedBy(str, Enum):
    USER = "user"
    FRAMEWORK = "framework"


class CheckOutcome(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    NOT_APPLICABLE = "not_applicable"


class GatePoint(str, Enum):
    """Where a policy's failure can block a Job from ever running —
    docs/pillars/policy_compliance.md's "Action-gating checks", refined:
    gating is a per-policy, user-chosen set of these, not a fixed
    Job-type table. A Job type belongs to zero or more categories
    (declared by the pillar that owns it); a policy gates zero or more
    categories (chosen at authoring/configuration time, independent of
    its EARS text and script).

    `SELF_REDEPLOY`, `SERVER_STOP`, `PACKAGE_PUBLISH`, and
    `PROJECT_REGISTRATION` added 2026-09-16, split out from
    `DEPLOY_REDEPLOY`/`RELEASE` (or newly covering a previously
    ungated Job type) once real usage showed the original six
    conflated meaningfully different blast radii — see
    `docs/requirements.md`'s policy-usability-refinement follow-up."""
    BUILD_INSTALL = "build_install"
    SERVER_LAUNCH = "server_launch"
    DEPLOY_REDEPLOY = "deploy_redeploy"
    SELF_REDEPLOY = "self_redeploy"
    SERVER_STOP = "server_stop"
    RELEASE = "release"
    PACKAGE_PUBLISH = "package_publish"
    BRANCH_FINISH = "branch_finish"
    PROJECT_REMOVAL = "project_removal"
    PROJECT_REGISTRATION = "project_registration"


class Policy(BaseModel):
    id: str
    topic: str
    target_pillar: str
    ears_text: str
    test_script: str
    scope_level: ScopeLevel
    scope_target: str | None = None
    created_by: CreatedBy
    enabled: bool = True
    gate_points: list[GatePoint] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_now)
    amended_at: datetime = Field(default_factory=_now)


class PolicyWaiver(BaseModel):
    """An explicit, auditable exception: this one project doesn't have
    to comply with this one policy — never a silent bypass, and never a
    change to the policy itself (which stays exactly what it is for
    every other project). Framework tier still means "can't be shadowed
    by a lower-scope policy" — a waiver is a different, explicit act,
    granted per project, not a scope-precedence outcome."""
    id: str
    policy_id: str
    project_id: str
    reason: str
    created_at: datetime = Field(default_factory=_now)


class PolicyCheckResult(BaseModel):
    __test__ = False  # not a pytest test class, despite the name
    id: str
    policy_id: str
    project_id: str
    outcome: CheckOutcome
    detail: str = ""
    job_id: str | None = None
    checked_at: datetime = Field(default_factory=_now)
