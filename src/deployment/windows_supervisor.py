"""Restart-on-failure for a Windows-hosted server (`common/windows.py`'s `is_windows_checkout`):
little-workshop polls its tracked PID (`Paths.deployment_windows_pid_path`) and relaunches it on crash,
standing in for what `systemd --user`'s `Restart=on-failure` already does for a WSL-hosted server --
there is no Windows unit for this pillar to delegate that to instead.

`tick()` is the whole mechanism and is public on purpose: a test calls it directly to check one pass
deterministically, rather than running `run_forever`'s real timer thread against a fake clock.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable

from common import windows_process
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
                try:
                    pid = int(pid_path.read_text().strip())
                except ValueError:
                    logger.warning("%s: its Windows pid file is not a plain integer, leaving it alone", project_id)
                    continue
                if windows_process.is_alive(self.flow.runner, pid):
                    continue
                try:
                    manifest = self.load_manifest(self.paths, project_id)
                    self.flow.launch(project_id, manifest, cwd=self.paths.state_home, on_windows=True)
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
