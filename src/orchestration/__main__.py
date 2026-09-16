"""Orchestration's real entrypoint: `python -m orchestration`. Serves its
Pillar Interface over its own Unix domain socket, and resolves other
pillars' sockets by asking Core — "Core's pillar registry is the only
place a socket path is looked up" (docs/architecture.md's PI section).
"""

from __future__ import annotations

import os
from pathlib import Path

import httpx
import uvicorn

from common.paths import Paths
from common.uds_client import client_for_socket
from orchestration import db
from orchestration.app import build_app
from orchestration.scheduler import DEFAULT_TICK_INTERVAL_SECONDS, SchedulerThread

# Registry statuses that mean "this pillar is enabled and has a socket
# worth trying" — see core/registry.py's PillarStatus. FAULTED and
# DISABLED both mean "don't bother," so a Job targeting either fails
# immediately with the same "pillar disabled" reason dispatch.py already
# gives a genuinely-disabled pillar; distinguishing the two is future
# polish, not needed to prove M1's dispatch path end to end.
_UNAVAILABLE_STATUSES = {"disabled", "faulted"}


def make_core_socket_lookup(paths: Paths):
    def _lookup(pillar: str) -> Path | None:
        try:
            with client_for_socket(paths.core_socket_path, timeout=3.0) as client:
                response = client.get("/v1/status")
            response.raise_for_status()
        except (httpx.ConnectError, httpx.TimeoutException, OSError, httpx.HTTPStatusError):
            return None
        entry = response.json().get("pillars", {}).get(pillar)
        if entry is None or entry["status"] in _UNAVAILABLE_STATUSES:
            return None
        return Path(entry["socket_path"])

    return _lookup


def main() -> None:
    paths = Paths.from_env()
    paths.ensure_layout()

    conn = db.connect(paths.pillar_db_path("orchestration"))
    socket_lookup = make_core_socket_lookup(paths)
    app = build_app(conn, socket_lookup)

    # docs/milestones.md M9: one tick loop per process, started here so
    # its one-time startup check (a firing missed while little-workshop
    # itself was down) runs before the socket is even opened for real
    # traffic — see orchestration/scheduler.py. The env var override
    # exists purely so integration tests can prove the tick loop fires
    # for real without waiting the production 10-minute interval;
    # production never sets it.
    tick_interval = float(os.environ.get("LWS_SCHEDULER_TICK_SECONDS", DEFAULT_TICK_INTERVAL_SECONDS))
    scheduler_thread = SchedulerThread(conn, socket_lookup, interval_seconds=tick_interval)
    scheduler_thread.start()

    socket_path = paths.socket_path("orchestration")
    if socket_path.exists():
        socket_path.unlink()

    uvicorn.run(app, uds=str(socket_path), log_level="info")


if __name__ == "__main__":
    main()
