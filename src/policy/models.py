"""Policy & Compliance's own models — `docs/pillars/policy_compliance.md`'s
"Data model" section: `policies`, `policy_projects` (the projects a policy
applies to) and `policy_check_results`, all real tables this pillar owns. `project_sets` is deliberately NOT here — it's
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
    `docs/requirements.md`'s policy-usability-refinement follow-up.

    `COMMIT` (2026-10-02) gates no Job: it is checked by the project's
    `commit-msg` git hook (`lws policy evaluate --gate-point commit`,
    installed with the VERSION pre-commit hook) against the commit being
    made, so a commit-level rule refuses the commit itself instead of
    holding the branch finish much later. Only project-bound policies may
    gate it."""
    COMMIT = "commit"
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


class PolicyProject(BaseModel):
    """One project a policy applies to, selected explicitly by the user.
    `gating_enabled` is per project and independent of the policy's own
    `gate_points` (where a failure would block is a property of the policy,
    never chosen per project). With gating off the check still runs and is
    recorded — for analysis — but its failure never holds a Job."""
    project_id: str
    gating_enabled: bool = True


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
    projects: list[PolicyProject] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_now)
    amended_at: datetime = Field(default_factory=_now)


class DraftStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class PolicyDraft(BaseModel):
    """Gates 1-3's output, kept for a human to review in the Dashboard before
    Gate 4. A draft is not a policy: nothing enforces it, and it never becomes
    one except through `policy.approve_draft`, which only the Dashboard drives.
    `concerns` flags a draft whose review notes say it needs work before it
    should be approved; `source` is where the rule came from."""
    id: str
    topic: str
    target_pillar: str
    ears_text: str
    ears_refined: bool = False
    test_script: str
    scope_level: ScopeLevel = ScopeLevel.ALL_PROJECTS
    scope_target: str | None = None
    created_by: CreatedBy = CreatedBy.FRAMEWORK
    gate_points: list[GatePoint] = Field(default_factory=list)
    # The Gate 1-3 pipeline's (or a human's, via `lws policy draft import`) own recommended
    # project selection and gating — pre-filled on the Dashboard's approval page rather than
    # forcing the reviewer to reconstruct it from scratch; the reviewer's own choice at
    # approval time, submitted with the decision, is still what actually takes effect.
    projects: list[PolicyProject] = Field(default_factory=list)
    source: str = ""
    notes: str = ""
    concerns: bool = False
    status: DraftStatus = DraftStatus.PENDING
    policy_id: str | None = None
    reject_reason: str = ""
    created_at: datetime = Field(default_factory=_now)
    decided_at: datetime | None = None


class PolicyCheckResult(BaseModel):
    __test__ = False  # not a pytest test class, despite the name
    id: str
    policy_id: str
    project_id: str
    outcome: CheckOutcome
    detail: str = ""
    job_id: str | None = None
    checked_at: datetime = Field(default_factory=_now)
    # Fingerprint of what was actually checked (project HEAD commit SHA plus
    # the policy's own amended_at) — lets policy.check_project recognize an
    # unchanged (project, policy) pair and skip re-running the sandbox
    # (docs/pillars/policy_compliance.md's "once per change" rule).
    checked_state: str = ""
