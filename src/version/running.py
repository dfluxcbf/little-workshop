"""The Jobs this pillar answered `accepted` and still runs on a background thread: a release, or a
branch finish waiting for the user's answer. The thread dies with the process, and only it would have
told Orchestration how the Job ended, so a Job still listed when the pillar starts was cut by a restart
(REQ-000000410). Kept in a small JSON file beside the pillars' databases: this pillar has no database."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path


class RunningJobs:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()

    def _read(self) -> dict[str, dict]:
        try:
            return json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}

    def _write(self, jobs: dict[str, dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        scratch = self.path.with_suffix(".tmp")
        scratch.write_text(json.dumps(jobs, indent=1))
        scratch.replace(self.path)

    def add(self, job_id: str, job_type: str, project_id: str | None) -> None:
        with self._lock:
            jobs = self._read()
            jobs[job_id] = {"type": job_type, "project_id": project_id, "since": datetime.now(timezone.utc).isoformat()}
            self._write(jobs)

    def remove(self, job_id: str) -> None:
        with self._lock:
            jobs = self._read()
            if jobs.pop(job_id, None) is not None:
                self._write(jobs)

    def all(self) -> dict[str, dict]:
        with self._lock:
            return self._read()
