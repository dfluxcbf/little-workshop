"""Restart-on-failure for a Windows-hosted server (`common/windows.py`'s `project_host`):
little-workshop polls its tracked PID (`Paths.deployment_windows_pid_path`) and relaunches it on crash,
standing in for what `systemd --user`'s `Restart=on-failure` already does for a WSL-hosted server --
there is no Windows unit for this pillar to delegate that to instead.

`tick()` is the whole mechanism and is public on purpose: a test calls it directly to check one pass
deterministically, rather than running `run_forever`'s real timer thread against a fake clock.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Callable

from common import windows, windows_process
from common.paths import Paths
from deployment import db
from deployment.flow import DeploymentFlow
from deployment.manifest import ServerManifest

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_SECONDS = 20.0


class WindowsServerSupervisor:
    def __init__(
        self, paths: Paths, flow: DeploymentFlow, conn, load_manifest: Callable[[Paths, str], ServerManifest],
        lock: threading.Lock, *, interval: float = DEFAULT_INTERVAL_SECONDS,
    ):
        self.paths = paths
        self.flow = flow
        self.conn = conn
        self.load_manifest = load_manifest
        self.lock = lock
        self.interval = interval
        self._stop = threading.Event()

    def tick(self) -> list[str]:
        """One pass over every project with cached server state: relaunches any Windows-hosted one
        whose tracked PID has died. Returns the project ids it relaunched; empty when everything's
        alive or nothing here is a Windows-hosted deployment (no pid file at all -- `launch()` never
        writes one for a WSL-hosted or external server)."""
        relaunched = []
        for state in db.list_server_states(self.conn):
            project_id = state["project_id"]
            with self.lock:
                pid_path = self.paths.deployment_windows_pid_path(project_id)
                if not pid_path.exists():
                    continue
                record = windows_process.read_pid_record(pid_path)
                if record is None:
                    logger.warning("%s: its Windows pid file is unreadable, leaving it alone", project_id)
                    continue
                if windows_process.is_alive(self.flow.runner, record["pid"], record["started"]):
                    continue
                # Relaunched from the deploy folder it ran from (a record from before folders were kept has none).
                cwd = Path(windows.to_wsl_path(record["cwd"], windows.mount_root())) if record["cwd"] else self.paths.state_home
                try:
                    manifest = self.load_manifest(self.paths, project_id)
                    self.flow.launch(project_id, manifest, cwd=cwd, on_windows=True)
                except Exception:  # noqa: BLE001 - one project's relaunch failing must never take this thread down
                    logger.exception("%s: its Windows process died and could not be relaunched", project_id)
                    continue
            relaunched.append(project_id)
        return relaunched

    def stop(self) -> None:
        self._stop.set()

    def run_forever(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                self.tick()
            except Exception:  # noqa: BLE001 - one bad tick must never end the loop
                logger.exception("windows server supervisor tick failed")
