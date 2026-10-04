"""Scheduling around plan usage (docs/pillars/orchestration.md, "Plan pressure").

A Claude plan has a 5-hour usage window. While the Usage & Efficiency pillar reports it fuller than
the owner's limit, a Job that uses Claude and that nobody is waiting for (one a schedule or a trigger
created) stays queued, so unattended work never uses up what the owner needs for their own. A Job a
person started, and every Job that runs only code, never waits. With the usage pillar switched off,
unreachable, or without a recent reading, nothing is held."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Callable

import httpx

from common.uds_client import client_for_socket
from orchestration.models import Job

# Job types that spend plan usage: they start Claude, or start a task that does.
CLAUDE_JOB_TYPES = ("agent.run_task", "agent.update_docs", "agent.query", "agent.ci_check", "usage.optimize", "usage.automate")
CACHE_SECONDS = 60.0
PILLAR = "usage"

_cache: dict = {"at": 0.0, "hold": False, "reading": None}
_cache_lock = threading.Lock()


def unattended(job: Job) -> bool:
    """Created by a schedule or a trigger: nobody is waiting at a screen for it."""
    return job.created_by == "scheduler" or job.created_by.startswith("trigger:")


def applies(job: Job) -> bool:
    return job.type in CLAUDE_JOB_TYPES and unattended(job)


def fetch(socket_lookup: Callable[[str], Path | None]) -> dict | None:
    socket_path = socket_lookup(PILLAR)
    if socket_path is None:
        return None
    try:
        with client_for_socket(socket_path, timeout=3.0) as client:
            response = client.get("/v1/plan_pressure")
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPError, OSError, ValueError):
        return None


def holding(socket_lookup: Callable[[str], Path | None], *, now: float | None = None) -> bool:
    """Whether unattended Claude Jobs should wait right now. The reading is asked for at most once a
    minute. Never call with the dispatch lock held: it calls another pillar."""
    now = time.monotonic() if now is None else now
    with _cache_lock:
        if now - _cache["at"] < CACHE_SECONDS and _cache["at"]:
            return _cache["hold"]
    reading = fetch(socket_lookup)
    with _cache_lock:
        _cache.update(at=now, hold=bool(reading and reading.get("hold")), reading=reading)
        return _cache["hold"]


def reset() -> None:
    with _cache_lock:
        _cache.update(at=0.0, hold=False, reading=None)
