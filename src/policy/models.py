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


class Policy(BaseModel):
    id: str
    topic: str
    target_pillar: str
    ears_text: str
    test_script: str
    scope_level: ScopeLevel
    scope_target: str | None = None
    created_by: CreatedBy
    created_at: datetime = Field(default_factory=_now)
    amended_at: datetime = Field(default_factory=_now)


class PolicyCheckResult(BaseModel):
    __test__ = False  # not a pytest test class, despite the name
    id: str
    policy_id: str
    project_id: str
    outcome: CheckOutcome
    detail: str = ""
    job_id: str | None = None
    checked_at: datetime = Field(default_factory=_now)
