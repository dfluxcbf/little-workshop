"""Core's real entrypoint: `python -m core`. Loads the manifest,
reconnects to/launches every enabled pillar, then serves its own status
interface while reconciling pillar health on a background cadence.
"""

from __future__ import annotations

import os
import threading
import time

import uvicorn

from common.manifest import load_manifest
from common.paths import Paths
from core.app import build_app
from core.supervisor import Supervisor


def main() -> None:
    paths = Paths.from_env()
    paths.ensure_layout()

    manifest = load_manifest(paths.manifest_path)
    supervisor = Supervisor(paths=paths, manifest=manifest)
    supervisor.startup()

    stop_event = threading.Event()

    def reconcile_loop() -> None:
        while not stop_event.wait(manifest.crash_relaunch_interval_seconds):
            supervisor.reconcile_once()

    thread = threading.Thread(target=reconcile_loop, daemon=True)
    thread.start()

    socket_path = paths.core_socket_path
    if socket_path.exists():
        socket_path.unlink()

    app = build_app(supervisor, paths)
    try:
        uvicorn.run(app, uds=str(socket_path), log_level="info")
    finally:
        stop_event.set()


if __name__ == "__main__":
    main()
