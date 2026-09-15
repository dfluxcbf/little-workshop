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
