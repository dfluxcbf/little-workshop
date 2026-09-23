"""Build & Packages' own models — docs/architecture.md's Data model row
for this pillar: `packages` (metadata; blobs stay on disk) and
`build_runs`, both `project_id`-scoped.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


class BuildRunStatus(str, Enum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class BuildRun(BaseModel):
    __test__ = False  # not a pytest test class, despite little_runner's own "run" naming
    id: str
    project_id: str
    commitish: str
    command: list[str]
    status: BuildRunStatus = BuildRunStatus.RUNNING
    from_fork: bool = False
    exit_code: int | None = None
    log: str = ""
    started_at: datetime = Field(default_factory=_now)
    finished_at: datetime | None = None


class PackageKind(str, Enum):
    PYPI = "pypi"
    VSIX = "vsix"


class Package(BaseModel):
    id: str
    project_id: str
    kind: PackageKind
    name: str
    normalized_name: str
    version: str
    filename: str
    source_build_run_id: str | None = None
    uploaded_at: datetime = Field(default_factory=_now)
