"""Registry & Portfolio's own models — the `projects` table is
authoritative (docs/architecture.md's Data model: "Registry & Portfolio
is this system's main responsible pillar for a project's lifecycle and
identity... its row is what 'this project exists' actually means").
`name` is the primary key throughout — it's the same string used as
`Job.project_id` and as the project-files directory name, so there's no
separate opaque id to keep in sync.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


class ProjectStatus(str, Enum):
    ACTIVE = "active"
    REMOVED = "removed"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Project(BaseModel):
    name: str
    path: str
    repo_url: str | None = None
    status: ProjectStatus = ProjectStatus.ACTIVE
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class ProjectSet(BaseModel):
    name: str
    project_names: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_now)


class TicketStatus(str, Enum):
    OPEN = "open"
    CLOSED = "closed"


class Ticket(BaseModel):
    id: str
    project_name: str
    title: str
    status: TicketStatus = TicketStatus.OPEN
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class BootstrapParams(BaseModel):
    name: str
    dest_dir: str
    owner: str = "dfluxcbf"
    description: str = ""
    create_github_repo: bool = False
    public: bool = False
    remote_host: str = "github.com"
    github_token: str | None = None
    ensure_labels: bool = True


class BootstrapResult(BaseModel):
    name: str
    path: str
    repo_url: str | None = None
    github_created: bool = False
    pushed: bool = False
    version_initialized: bool = False
    labels_created: list[str] = Field(default_factory=list)


class ImportParams(BaseModel):
    name: str
    path: str


class ImportResult(BaseModel):
    name: str
    path: str
    repo_url: str | None = None
