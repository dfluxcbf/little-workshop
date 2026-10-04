"""Workshop triggers: "when event X happens, run job Y" (docs/pillars/orchestration.md, "Triggers").

A schedule fires on time; a trigger fires on an event. The first events need no bus: Orchestration
sees every Job settle, so `job.settled` (and the names derived from it) come from `db.update_job`;
other pillars post theirs to `POST /v1/events`. An event is only queued where it happens. The pump
(`fire_pending`, called with the dispatch lock held) turns queued events into Jobs, so a triggered
Job is created like any other: policy-gated, on its queue, with `created_by: "trigger:<id>"`.

Safety, checked when a trigger is created and never left to firing time: a trigger cannot target a
Job type that merges, finishes, releases, removes or deletes ("automation never merges, deletes or
finishes on its own initiative"). A Job a trigger created cannot fire that same trigger, and a chain
of triggers stops at `MAX_DEPTH`."""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from orchestration.models import Job, JobStatus, JobTemplate

MAX_DEPTH = 3
EVENT_JOB_SETTLED = "job.settled"
SETTLED = (JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.STOPPED)
# Job types no trigger may create.
FORBIDDEN_TARGET = re.compile(
    r"(_finish$|^version\.release$|^version\.stabilize$|\.remove$|_remove$|_delete$|\.delete|^usage\.purge_project$"
    r"|^agent\.finish_task$|^agent\.skip_task$|^policy\.confirm|^deployment\.confirm$)"
)
BRANCH_FINISH_TYPES = ("version.feature_finish", "version.improvement_finish", "version.hotfix_finish", "version.support_finish")
RELEASE_FINISH_TYPES = ("version.release_finish", "version.release")
# A template's `project_id` that means "no project": the Job is about every project, not the event's.
ALL_PROJECTS = "*"
EVENT_VALUE = re.compile(r"^\$event\.([A-Za-z_][\w.]*)$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS triggers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    event TEXT NOT NULL,
    filter TEXT NOT NULL DEFAULT '{}',
    job_template TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    last_fired_at TEXT,
    last_job_id TEXT
);
CREATE TABLE IF NOT EXISTS trigger_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    project_id TEXT,
    data TEXT NOT NULL DEFAULT '{}',
    source_job_id TEXT UNIQUE,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS trigger_jobs (
    job_id TEXT PRIMARY KEY,
    trigger_id TEXT NOT NULL,
    depth INTEGER NOT NULL
);
"""


class TriggerError(ValueError):
    """A trigger that may not exist: its message says why."""


class TriggerOn(BaseModel):
    event: str
    # Every key must equal the event's value: `type`, `project_id` and `status` for a settled Job, any data key otherwise.
    filter: dict[str, Any] = Field(default_factory=dict)


class Trigger(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    name: str
    on: TriggerOn
    job_template: JobTemplate
    enabled: bool = True
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_fired_at: datetime | None = None
    last_job_id: str | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def validate(trigger: Trigger) -> None:
    job_type = trigger.job_template.type
    if FORBIDDEN_TARGET.search(job_type):
        raise TriggerError(
            f"a trigger cannot create a {job_type!r} Job: automation never merges, finishes, releases, removes or deletes on "
            "its own initiative. Trigger a job that prepares or reports, and leave that step to a person.")
    if not trigger.on.event.strip():
        raise TriggerError("a trigger needs the event it fires on")


# -- storage ------------------------------------------------------------------


def _row_to_trigger(row: sqlite3.Row) -> Trigger:
    return Trigger(
        id=row["id"], name=row["name"], on=TriggerOn(event=row["event"], filter=json.loads(row["filter"])),
        job_template=JobTemplate(**json.loads(row["job_template"])), enabled=bool(row["enabled"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        last_fired_at=datetime.fromisoformat(row["last_fired_at"]) if row["last_fired_at"] else None, last_job_id=row["last_job_id"],
    )


def save(conn: sqlite3.Connection, trigger: Trigger) -> Trigger:
    validate(trigger)
    conn.execute(
        """INSERT OR REPLACE INTO triggers (id, name, event, filter, job_template, enabled, created_at, last_fired_at, last_job_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (trigger.id, trigger.name, trigger.on.event, json.dumps(trigger.on.filter), trigger.job_template.model_dump_json(),
         int(trigger.enabled), trigger.created_at.isoformat(), trigger.last_fired_at.isoformat() if trigger.last_fired_at else None,
         trigger.last_job_id))
    conn.commit()
    return trigger


def get(conn: sqlite3.Connection, trigger_id: str) -> Trigger | None:
    row = conn.execute("SELECT * FROM triggers WHERE id = ?", (trigger_id,)).fetchone()
    return _row_to_trigger(row) if row else None


def list_all(conn: sqlite3.Connection) -> list[Trigger]:
    return [_row_to_trigger(row) for row in conn.execute("SELECT * FROM triggers ORDER BY created_at")]


def delete(conn: sqlite3.Connection, trigger_id: str) -> bool:
    removed = conn.execute("DELETE FROM triggers WHERE id = ?", (trigger_id,)).rowcount
    conn.commit()
    return bool(removed)


# -- events -------------------------------------------------------------------


def queue_event(conn: sqlite3.Connection, name: str, project_id: str | None, data: dict, *, source_job_id: str | None = None) -> None:
    """Remember that something happened. Nothing is kept when no trigger exists: there is nobody to tell."""
    if conn.execute("SELECT 1 FROM triggers WHERE enabled = 1 LIMIT 1").fetchone() is None:
        return
    conn.execute(
        "INSERT OR IGNORE INTO trigger_events (name, project_id, data, source_job_id, created_at) VALUES (?, ?, ?, ?, ?)",
        (name, project_id, json.dumps(data), source_job_id, _now()))
    conn.commit()


def on_job_updated(conn: sqlite3.Connection, job: Job) -> None:
    """Called by `db.update_job`: a Job that has settled is queued as an event, once."""
    if job.status in SETTLED:
        queue_event(conn, EVENT_JOB_SETTLED, job.project_id, {
            "type": job.type, "project_id": job.project_id, "status": job.status.value, "job_id": job.id,
            "created_by": job.created_by, "target_pillar": job.target_pillar,
        }, source_job_id=job.id)


def event_names(name: str, data: dict) -> tuple[str, ...]:
    """The names an event answers to: a settled Job is also `job.succeeded` or `job.failed`, and a
    succeeded finish or release is `version.branch_finished` or `version.release_finished`."""
    if name != EVENT_JOB_SETTLED:
        return (name,)
    names = [name, "job.succeeded" if data.get("status") == JobStatus.SUCCEEDED.value else "job.failed"]
    if data.get("status") == JobStatus.SUCCEEDED.value:
        if data.get("type") in BRANCH_FINISH_TYPES:
            names.append("version.branch_finished")
        if data.get("type") in RELEASE_FINISH_TYPES:
            names.append("version.release_finished")
    return tuple(names)


def matches(trigger: Trigger, name: str, data: dict) -> bool:
    if not trigger.enabled or trigger.on.event not in event_names(name, data):
        return False
    return all(data.get(key) == value for key, value in trigger.on.filter.items())


def _lookup(data: dict, path: str):
    value: Any = data
    for part in path.split("."):
        value = value.get(part) if isinstance(value, dict) else None
    return value


def expand(template: JobTemplate, project_id: str | None, data: dict) -> dict:
    """The Job a trigger creates for an event: a template without a project takes the event's (one
    whose project is `*` gets none: a Job about every project), and a parameter whose value is
    `$event.<key>` takes that value of the event."""
    def fill(value):
        if isinstance(value, str):
            match = EVENT_VALUE.match(value)
            return _lookup(data, match.group(1)) if match else value
        if isinstance(value, dict):
            return {key: fill(item) for key, item in value.items()}
        if isinstance(value, list):
            return [fill(item) for item in value]
        return value

    target_project = None if template.project_id == ALL_PROJECTS else (template.project_id or project_id)
    queue_key = project_id if template.queue_key == "$event.project_id" else template.queue_key
    return {"type": template.type, "target_pillar": template.target_pillar, "project_id": target_project,
            "params": fill(template.params), "queue_key": queue_key, "depends_on": list(template.depends_on)}


def fire_pending(conn: sqlite3.Connection, insert_job) -> list[Job]:
    """Turn every queued event into the Jobs its triggers create. `insert_job(job)` stores a queued
    Job as Orchestration creates any other (with its policy gate). Call with the dispatch lock held."""
    created: list[Job] = []
    events = conn.execute("SELECT * FROM trigger_events ORDER BY seq").fetchall()
    if not events:
        return created
    triggers = list_all(conn)
    for event in events:
        data = json.loads(event["data"])
        source = conn.execute("SELECT trigger_id, depth FROM trigger_jobs WHERE job_id = ?", (event["source_job_id"],)).fetchone() \
            if event["source_job_id"] else None
        depth = source["depth"] if source else 0
        for trigger in triggers:
            if not matches(trigger, event["name"], data):
                continue
            if source and source["trigger_id"] == trigger.id:
                continue  # a Job this trigger created never fires it again
            if depth >= MAX_DEPTH:
                continue
            job = Job(**expand(trigger.job_template, event["project_id"], data), created_by=f"trigger:{trigger.id}")
            insert_job(job)
            conn.execute("INSERT INTO trigger_jobs (job_id, trigger_id, depth) VALUES (?, ?, ?)", (job.id, trigger.id, depth + 1))
            conn.execute("UPDATE triggers SET last_fired_at = ?, last_job_id = ? WHERE id = ?", (_now(), job.id, trigger.id))
            created.append(job)
        conn.execute("DELETE FROM trigger_events WHERE seq = ?", (event["seq"],))
    conn.commit()
    return created


# The triggers little-workshop sets up for itself (`lws trigger install-defaults`): each replaces
# work Claude was doing by hand (docs/claude_usage_optimization.md §11.3).
WORKSHOP = "little-workshop"
DEFAULTS = (
    {"name": "Redeploy the pillars a finished branch changed",
     "on": {"event": "version.branch_finished", "filter": {"project_id": WORKSHOP}},
     "job_template": {"type": "deployment.redeploy_changed_pillars", "target_pillar": "deployment", "project_id": WORKSHOP,
                      "params": {}, "queue_key": "redeploy-changed-pillars"}},
    {"name": "Report which projects' Claude files differ after a finished branch",
     "on": {"event": "version.branch_finished", "filter": {"project_id": WORKSHOP}},
     "job_template": {"type": "registry.sync_claude_files", "target_pillar": "registry", "project_id": ALL_PROJECTS,
                      "params": {"write": False}, "queue_key": "claude-files-sync"}},
    {"name": "Ingest a Claude session's transcript when it ends",
     "on": {"event": "claude.session_ended", "filter": {}},
     "job_template": {"type": "usage.ingest_transcripts", "target_pillar": "usage", "project_id": ALL_PROJECTS,
                      "params": {}, "queue_key": "usage"}},
)


def install_defaults(conn: sqlite3.Connection) -> list[Trigger]:
    """Add the default triggers that are not there yet (matched by name); returns the ones added."""
    present = {trigger.name for trigger in list_all(conn)}
    added = []
    for spec in DEFAULTS:
        if spec["name"] not in present:
            added.append(save(conn, Trigger(name=spec["name"], on=TriggerOn(**spec["on"]), job_template=JobTemplate(**spec["job_template"]))))
    return added
