"""How a pillar serves its Pillar Interface on its Unix socket, and leaves in bounded time when asked to
stop (REQ-000000404).

uvicorn waits for every open connection to close before it stops, with no limit of its own: a long-poll
(Orchestration's `/v1/jobs/{id}/wait`, up to 300s) or a browser's live-update stream kept a pillar
"Waiting for connections to close" until systemd killed it 90s later (Orchestration, 2026-10-04). Here
the wait is bounded: after `GRACEFUL_SHUTDOWN_SECONDS` the requests still running are cut, the app's
shutdown hooks run, and uvicorn hands the stop signal back, which ends the process at once even with a
request thread still busy. `stopping` is set the moment the stop is asked for, so long-polls and live
streams end straight away and a sandbox run the stop cut short is told apart from one that finished;
what is left is real work (a branch finish answered in its request), which gets up to a minute, well
inside systemd's 90 seconds.
"""

from __future__ import annotations

import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import uvicorn
from uvicorn.main import STARTUP_FAILURE

GRACEFUL_SHUTDOWN_SECONDS = 60.0
HEALTH_LOCK_SECONDS = 1.0

# Set when this process is asked to stop (SIGTERM or SIGINT). Never cleared in a running pillar.
stopping = threading.Event()


@contextmanager
def held_or_busy(lock, *, timeout: float = HEALTH_LOCK_SECONDS) -> Iterator[bool]:
    """For a health check (REQ-000000409): the pillar's lock if it can be had within `timeout`, else
    False, and the check says the pillar is busy. A health check that waited for work holding the lock
    for minutes (Registry connecting a repository) would look hung to Core, which restarts a pillar that
    runs but does not answer."""
    acquired = lock.acquire(timeout=timeout)
    try:
        yield acquired
    finally:
        if acquired:
            lock.release()


class _Server(uvicorn.Server):
    def handle_exit(self, sig, frame) -> None:
        stopping.set()
        super().handle_exit(sig, frame)


def serve(app, socket_path: Path) -> None:
    """Serve `app` on `socket_path` until asked to stop; a socket file left by an earlier process is
    removed first."""
    if socket_path.exists():
        socket_path.unlink()
    config = uvicorn.Config(app, uds=str(socket_path), log_level="info", timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_SECONDS)
    server = _Server(config)
    server.run()
    if not server.started:
        sys.exit(STARTUP_FAILURE)
