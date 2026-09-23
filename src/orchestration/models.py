"""The Job and Pipeline/PipelineRun models — docs/design.md's Core
concepts, verbatim on the Job shape: `{id, type, target_pillar,
project_id, params, status, queue_key, depends_on, created_by, result}`.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    PENDING_CONFIRMATION = "pending_confirmation"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    # A running Job interrupted by a real `stop` call (docs/milestones.md
    # M9) — distinct from `cancelled`, which only ever applied to a Job
    # that hadn't started running yet.
    STOPPED = "stopped"


def _new_id() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Job(BaseModel):
    id: str = Field(default_factory=_new_id)
    type: str
    target_pillar: str
    project_id: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    status: JobStatus = JobStatus.QUEUED
    # A job holding a `queue_key` may not run concurrently with another
    # job holding the same key — docs/design.md's "the queue_key mutex."
    queue_key: str | None = None
    depends_on: list[str] = Field(default_factory=list)
    created_by: str = "unknown"
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class PipelineNode(BaseModel):
    node_id: str
    job_type: str
    target_pillar: str
    project_id: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    queue_key: str | None = None
    # References other nodes' `node_id`s within the same pipeline.
    depends_on: list[str] = Field(default_factory=list)


class Pipeline(BaseModel):
    """A named, versioned DAG of Job templates — docs/design.md's
    "Pipeline / PipelineRun": expanded into concrete Jobs at trigger time,
    needing no execution primitive beyond `depends_on`-linked Jobs."""

    name: str
    version: int = 1
    nodes: list[PipelineNode]


class PipelineRun(BaseModel):
    id: str = Field(default_factory=_new_id)
    pipeline_name: str
    pipeline_version: int
    project_id: str | None = None
    created_by: str = "unknown"
    # node_id -> Job.id, the concrete Jobs this run expanded into.
    job_ids: dict[str, str] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_now)


class ScheduleKind(str, Enum):
    ONCE = "once"
    RECURRING = "recurring"


class Schedule(BaseModel):
    """`docs/pillars/orchestration.md`'s "Scheduled tasks": deliberately
    not full cron. `once` uses `at`; `recurring` uses `days_of_week`
    (0=Monday..6=Sunday) plus `time_of_day` ("HH:MM") — both interpreted
    in the host's own local time, never stored with a timezone, since
    little-workshop runs on one household's machine."""

    kind: ScheduleKind
    at: datetime | None = None
    days_of_week: list[int] = Field(default_factory=list)
    time_of_day: str | None = None


class JobTemplate(BaseModel):
    """A Job's own creation payload, minus the fields a `ScheduledTask`
    fills in itself (`created_by: "scheduler"`) each time it fires."""

    type: str
    target_pillar: str
    project_id: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    queue_key: str | None = None
    depends_on: list[str] = Field(default_factory=list)


class ScheduledTaskState(str, Enum):
    ACTIVE = "active"
    STOPPED = "stopped"


class ScheduledTask(BaseModel):
    id: str = Field(default_factory=_new_id)
    name: str
    job_template: JobTemplate
    schedule: Schedule
    state: ScheduledTaskState = ScheduledTaskState.ACTIVE
    # Local-time, naive datetimes throughout — see `Schedule`'s own note.
    next_run_at: datetime | None = None
    # Set only when a firing was missed while little-workshop itself was
    # down; a pending decision (`trigger` resolves it, `cancel` dismisses
    # it), never acted on automatically. See `docs/pillars/orchestration.md`'s
    # "Missed firings."
    missed_run_at: datetime | None = None
    # The most recently created Job.id from this task, if any — what
    # `remove` stops if it's still running, and what a caller can look
    # up to see how the last firing went.
    last_job_id: str | None = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
