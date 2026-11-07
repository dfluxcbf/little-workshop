"""Project deletions that did not finish: the purge steps still owed by a pillar that could not be reached.

Deleting a project (System > Data management) removes its registration first and then asks each pillar to
purge its records. A pillar that does not answer leaves its step pending here, in one small JSON file under the
workshop's state directory (like `prefs`), and the running Dashboard tries the pending steps again every
`RETRY_SECONDS`. A deletion still unfinished `STUCK_AFTER` after it began needs the user's attention, with the
errors the attempts met. `try again` restarts that clock and the automatic retries; `try again (once)` makes one
attempt now and leaves both as they were.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from common.paths import Paths

RETRY_SECONDS = 600
STUCK_AFTER = timedelta(hours=2)
KEPT_ERRORS = 30

# A step is {"type": job type or "orchestration.purge", "pillar": ...}; `run_step(project, step)` answers (ok, error, job_id).
RunStep = Callable[[str, dict], tuple[bool, str, str | None]]


def _file(paths: Paths) -> Path:
    return paths.state_home / "dashboard" / "deletions.json"


def load(paths: Paths) -> dict[str, dict]:
    """{project: deletion} for every deletion still owing a step; a missing or unreadable file is none."""
    try:
        data = json.loads(_file(paths).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save(paths: Paths, data: dict) -> None:
    target = _file(paths)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=target.parent, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
    os.replace(tmp, target)


def _iso(now: datetime) -> str:
    return now.astimezone(timezone.utc).isoformat()


def _error(now: datetime, step: dict, error: str, job_id: str | None) -> dict:
    return {"at": _iso(now), "pillar": step["pillar"], "type": step["type"], "error": error, "job_id": job_id}


def begin(paths: Paths, project: str, pending: list[dict], errors: list[dict], now: datetime) -> None:
    """Record a deletion that left `pending` steps owing. Nothing is recorded when none is owing."""
    data = load(paths)
    if not pending:
        data.pop(project, None)
    else:
        data[project] = {"project": project, "started_at": _iso(now), "pending": pending, "errors": errors[-KEPT_ERRORS:],
                         "attempts": 1, "last_attempt_at": _iso(now)}
    _save(paths, data)


def stuck(deletion: dict, now: datetime) -> bool:
    started = datetime.fromisoformat(deletion["started_at"])
    return bool(deletion["pending"]) and now - started > STUCK_AFTER


def retry(paths: Paths, project: str, run_step: RunStep, now: datetime, *, restart_clock: bool = False) -> dict | None:
    """Try the project's pending steps once. Returns the deletion still owing, or None when it is finished (or
    unknown). `restart_clock` starts the two-hour wait over, as `try again` does."""
    data = load(paths)
    deletion = data.get(project)
    if deletion is None:
        return None
    still = []
    for step in deletion["pending"]:
        ok, error, job_id = run_step(project, step)
        if not ok:
            still.append(step)
            deletion["errors"] = (deletion["errors"] + [_error(now, step, error, job_id)])[-KEPT_ERRORS:]
    deletion["pending"], deletion["attempts"], deletion["last_attempt_at"] = still, deletion["attempts"] + 1, _iso(now)
    if restart_clock:
        deletion["started_at"] = _iso(now)
    if still:
        data[project] = deletion
    else:
        data.pop(project, None)
    _save(paths, data)
    return deletion if still else None


def retry_all(paths: Paths, run_step: RunStep, now: datetime) -> None:
    for project in list(load(paths)):
        retry(paths, project, run_step, now)
