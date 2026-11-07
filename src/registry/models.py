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
from typing import Literal

from pydantic import BaseModel, Field


Governance = Literal["light", "full"]
GOVERNANCE_LEVELS: tuple[str, ...] = ("light", "full")
Host = Literal["wsl", "windows"]


class ProjectStatus(str, Enum):
    ACTIVE = "active"
    REMOVED = "removed"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Project(BaseModel):
    name: str
    path: str
    repo_url: str | None = None
    # The checkout folder (relative) whose assets the Dashboard shows; None: asset review is off.
    assets_dir: str | None = None
    # How much requirement work little-workshop asks of the project: `light` (the default) runs only the
    # safety policies and asks for no requirements; `full` adds the requirement policies and the DoD.
    governance: Governance = "light"
    # Where the project's code and tools run: `windows` for a checkout on a Windows drive (git.exe, native
    # hooks and servers; common/windows.py), `wsl` otherwise. Set from the path at bootstrap and import.
    host: Host = "wsl"
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
    template: Literal["python", "unity"] = "python"
    owner: str = "dfluxcbf"
    description: str = ""
    create_github_repo: bool = False
    public: bool = False
    remote_host: str = "github.com"
    github_token: str | None = None
    ensure_labels: bool = True
    # After scaffolding, start a task that interviews the owner and fills docs/design.md (REQ-000000519).
    interview: bool = False


class BootstrapResult(BaseModel):
    name: str
    path: str
    repo_url: str | None = None
    github_created: bool = False
    pushed: bool = False
    version_initialized: bool = False
    labels_created: list[str] = Field(default_factory=list)
    interview_job_id: str | None = None
    interview_error: str | None = None


class ConnectGitHubParams(BaseModel):
    name: str
    owner: str = "dfluxcbf"
    description: str = ""
    public: bool = False
    remote_host: str = "github.com"
    github_token: str | None = None
    ensure_labels: bool = True


class ConnectGitHubResult(BaseModel):
    name: str
    path: str
    repo_url: str
    adopted_remote: bool
    github_created: bool = False
    pushed: bool = False
    labels_created: list[str] = Field(default_factory=list)


class ImportParams(BaseModel):
    name: str
    path: str


class ImportResult(BaseModel):
    name: str
    path: str
    repo_url: str | None = None
    # What followed the registration (REQ-000000520): the Claude-file sync Job and the branch_finish preflight.
    sync: dict | None = None
    preflight: dict | None = None
