"""Requirements & Quality's own models — docs/architecture.md's Data
model row for this pillar: `requirements`, `test_suites`, `test_results`,
`use_cases`, `milestones`, all `project_id`-scoped. Carries over
`little-requirements`' Project->Suite->Requirement->TestResult shape
(with Registry & Portfolio now owning "Project" instead of a per-tool
row) plus its own never-built M8 — Use Cases and Milestones, an axis
independent of test suites entirely.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


class RequirementStatus(str, Enum):
    DRAFT = "draft"
    PUBLISHED = "published"
    SUPERSEDED = "superseded"  # replaced by `superseded_by`; out of the definition-of-done report (REQ-000000448)


class TestSuite(BaseModel):
    __test__ = False  # not a pytest test class, despite the name
    id: str
    project_id: str
    name: str
    description: str = ""
    created_at: datetime = Field(default_factory=_now)


class UseCase(BaseModel):
    id: str
    project_id: str
    name: str
    description: str = ""
    created_at: datetime = Field(default_factory=_now)


class Milestone(BaseModel):
    id: str
    project_id: str
    name: str
    description: str = ""
    created_at: datetime = Field(default_factory=_now)


class Requirement(BaseModel):
    id: str
    project_id: str
    key: str
    title: str
    description: str = ""
    category: str = ""
    status: RequirementStatus = RequirementStatus.DRAFT
    use_case_id: str | None = None
    milestone_id: str | None = None
    superseded_by: str | None = None  # the key of the requirement that replaced this one
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class TestVerdict(str, Enum):  # not a pytest test class, despite the name
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    ERROR = "error"


class TestResult(BaseModel):
    __test__ = False  # not a pytest test class, despite the name
    id: str
    project_id: str
    suite_id: str
    test_name: str
    verdict: TestVerdict
    detail: str = ""
    source: str = "manual"  # "manual" | "pytest" | "junit"
    # The requirement whose marker section in the test file holds this test, when one does (REQ-000000500).
    requirement_key: str | None = None
    executed_at: datetime = Field(default_factory=_now)
