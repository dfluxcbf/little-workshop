"""Orchestration's change feed: every change of a Job, a Job's step or a scheduled task gets the next
number, and a caller asking for the changes after a number it holds gets them as soon as there are any
(a long poll). The Dashboard's live pages follow it to update the moment a Job moves, instead of
asking every few seconds whether anything did.

The feed lives in memory: it is a notice that something changed, never the record of what (the
database is). A restart starts a new count under a new `boot` id, and a caller whose number is too old
for the changes kept is told to `reset`: to read again what it shows."""

from __future__ import annotations

import threading
import time
import uuid
from collections import deque

KEEP = 5000  # changes kept for callers that fell behind
MAX_WAIT_SECONDS = 30.0


class ChangeFeed:
    def __init__(self, keep: int = KEEP) -> None:
        self.boot = uuid.uuid4().hex[:12]
        self._seq = 0
        self._log: deque[dict] = deque(maxlen=keep)
        self._changed = threading.Condition()

    @property
    def seq(self) -> int:
        with self._changed:
            return self._seq

    def note(self, kind: str, ident: str, **fields) -> None:
        """Record one change: `kind` is job, step, scheduled_task or jobs_deleted; `fields` say what
        a watcher needs to tell who cares (a Job's type, project and status)."""
        with self._changed:
            self._seq += 1
            self._log.append({"seq": self._seq, "kind": kind, "id": ident, **fields})
            self._changed.notify_all()

    def since(self, after: int | None, *, boot: str | None = None, timeout: float = 0.0) -> dict:
        """The changes numbered after `after`, waiting up to `timeout` seconds for the first one.
        Without `after` (or with another boot's number) the answer comes at once, carrying the current
        number to follow from; `reset` says the caller cannot be told everything it missed."""
        deadline = time.monotonic() + max(0.0, min(timeout, MAX_WAIT_SECONDS))
        with self._changed:
            if after is None or (boot is not None and boot != self.boot) or after > self._seq:
                return {"boot": self.boot, "seq": self._seq, "changes": [], "reset": after is not None}
            while self._seq <= after:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._changed.wait(remaining)
            oldest = self._log[0]["seq"] if self._log else self._seq + 1
            return {
                "boot": self.boot, "seq": self._seq, "reset": after < oldest - 1,
                "changes": [change for change in self._log if change["seq"] > after],
            }


# The one feed of this process: every database write below notes itself here.
FEED = ChangeFeed()


def note_job(job, kind: str = "job") -> None:
    FEED.note(kind, job.id, type=job.type, project_id=job.project_id,
              status=getattr(job.status, "value", job.status), created_by=job.created_by)
