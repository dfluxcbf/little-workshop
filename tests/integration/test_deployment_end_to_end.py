"""M4's end-to-end proof: Registry & Portfolio bootstraps a project,
Deployment & Servers (both real `systemd --user` units) deploys it from
a real throwaway git worktree into a *genuinely listening* process
(python's own `http.server`, not a mock), then a stop genuinely frees
the port back up — defence-in-depth, for real.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest
import uvicorn

INTEGRATION = Path(__file__).resolve().parent
SRC = INTEGRATION.parents[1] / "src"


def _systemd_user_available() -> bool:
    try:
        result = subprocess.run(
            ["systemctl", "--user", "is-system-running"], capture_output=True, text=True, timeout=5
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return result.stdout.strip() in ("running", "degraded")


requires_systemd_user = pytest.mark.skipif(
    not _systemd_user_available(), reason="systemd --user session not available"
)

from cli import client as cli_client
from common import systemd
from common.paths import Paths
from common.projectfiles import ProjectFilesClient
from common.shell import DEFAULT_RUNNER
from core.app import build_app as build_core_app
from core.health import probe_health
from core.registry import PillarStatus, RegistryEntry
from core.supervisor import Supervisor
from common.manifest import Manifest
from deployment.netutil import port_is_listening
from orchestration.app import build_app as build_orchestration_app
from orchestration import db as orchestration_db


PORT = 18765


class _UvicornThread:
    def __init__(self, app, socket_path):
        self._config = uvicorn.Config(app, uds=str(socket_path), log_level="warning")
        self._server = uvicorn.Server(self._config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)

    def start(self) -> None:
        self._thread.start()
        for _ in range(100):
            if getattr(self._server, "started", False):
                return
            time.sleep(0.05)
        raise RuntimeError("test server did not start in time")

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=5)


@requires_systemd_user
def test_deployment_deploys_and_stops_a_real_listening_process(tmp_path):
    xdg_config = tmp_path / "xdgconfig"
    xdg_state = Path(tempfile.mkdtemp(prefix="lwsit-"))
    paths = Paths(config_home=xdg_config / "little-workshop", state_home=xdg_state / "little-workshop")
    paths.ensure_layout()

    env = {"PYTHONPATH": str(SRC), "XDG_CONFIG_HOME": str(xdg_config), "XDG_STATE_HOME": str(xdg_state)}
    systemd.start("registry", [sys.executable, "-m", "registry"], environment=env, runner=DEFAULT_RUNNER)
    systemd.start("deployment", [sys.executable, "-m", "deployment"], environment=env, runner=DEFAULT_RUNNER)

    try:
        for pillar in ("registry", "deployment"):
            probe = probe_health(paths.socket_path(pillar), timeout=5.0, max_retries=10)
            assert probe.ok, f"{pillar} pillar never became healthy: {probe.error}"

        core_supervisor = Supervisor(paths=paths, manifest=Manifest())
        core_supervisor.registry.set(
            RegistryEntry(name="registry", socket_path=paths.socket_path("registry"), status=PillarStatus.HEALTHY)
        )
        core_server = _UvicornThread(build_core_app(core_supervisor), paths.core_socket_path)
        core_server.start()

        def socket_lookup(pillar: str):
            if pillar in ("registry", "deployment"):
                return paths.socket_path(pillar)
            return None

        conn = orchestration_db.connect(paths.pillar_db_path("orchestration"))
        orch_server = _UvicornThread(build_orchestration_app(conn, socket_lookup), paths.socket_path("orchestration"))
        orch_server.start()

        try:
            dest_dir = tmp_path / "workdir"
            dest_dir.mkdir()
            bootstrap = cli_client.create_job(
                paths, type="project.bootstrap", target_pillar="registry", project_id="little-meals",
                params={"name": "little-meals", "dest_dir": str(dest_dir)}, queue_key=None, depends_on=[],
                created_by="integration-test",
            )
            assert bootstrap["status"] == "succeeded", bootstrap
            project_dir = dest_dir / "little-meals"

            # A trivially-installable, genuinely-listening "server" —
            # python's own http.server, not a mock — the manifest just
            # points at what project.bootstrap already committed on master.
            manifest_toml = f"""
[server]
name = "little-meals"
command = "python3 -m http.server {PORT}"
port = {PORT}
match = "http.server {PORT}"
install = ["true"]
ready_timeout_seconds = 15
stop_timeout_seconds = 15
"""
            ProjectFilesClient(paths, caller_pillar="deployment").write(
                "little-meals", "manifest.deployment.toml", manifest_toml
            )

            deploy = cli_client.create_job(
                paths, type="deployment.deploy", target_pillar="deployment", project_id="little-meals",
                params={"branch": "master"}, queue_key="little-meals", depends_on=[], created_by="integration-test",
            )
            assert deploy["status"] == "succeeded", deploy
            assert deploy["result"]["version"] == "0.1.0"

            assert port_is_listening(PORT), "deployed server never actually started listening"

            from common.uds_client import client_for_socket

            with client_for_socket(paths.socket_path("deployment"), timeout=5.0) as deployment_client:
                status = deployment_client.get("/v1/status/little-meals")
            body = status.json()
            assert body["unit"] == "active"
            assert body["listening"] is True

            stop = cli_client.create_job(
                paths, type="deployment.stop", target_pillar="deployment", project_id="little-meals",
                params={}, queue_key="little-meals", depends_on=[], created_by="integration-test",
            )
            assert stop["status"] == "succeeded", stop
            assert stop["result"]["port_free"] is True
            assert not port_is_listening(PORT), "server was still listening after a real stop"
        finally:
            orch_server.stop()
            core_server.stop()
            conn.close()
    finally:
        systemd.stop("registry", runner=DEFAULT_RUNNER)
        systemd.stop("deployment", runner=DEFAULT_RUNNER)
        systemd.stop("deploy-little-meals", runner=DEFAULT_RUNNER)
        shutil.rmtree(xdg_state, ignore_errors=True)
