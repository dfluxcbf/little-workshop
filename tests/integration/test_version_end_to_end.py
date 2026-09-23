"""M3's end-to-end proof: Version Control & Release, running as a real
`systemd --user` unit, resolves a project's repo path by calling
Registry & Portfolio (also a real unit) over a real Pillar Interface —
discovered through Core's own registry, never hardcoded — and then runs
a genuine feature branch through rebase, squash, merge, and a real
version bump, all dispatched as real Orchestration Jobs.
"""

from __future__ import annotations

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
from common.shell import DEFAULT_RUNNER
from core.app import build_app as build_core_app
from core.health import probe_health
from core.registry import PillarStatus, RegistryEntry
from core.supervisor import Supervisor
from common.manifest import Manifest
from orchestration.app import build_app as build_orchestration_app
from orchestration import db as orchestration_db


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
def test_version_resolves_project_via_core_and_registry_then_runs_a_real_feature(tmp_path):
    xdg_config = tmp_path / "xdgconfig"
    xdg_state = Path(tempfile.mkdtemp(prefix="lwsit-"))
    paths = Paths(config_home=xdg_config / "little-workshop", state_home=xdg_state / "little-workshop")
    paths.ensure_layout()

    env = {"PYTHONPATH": str(SRC), "XDG_CONFIG_HOME": str(xdg_config), "XDG_STATE_HOME": str(xdg_state)}
    systemd.start("registry", [sys.executable, "-m", "registry"], environment=env, runner=DEFAULT_RUNNER)
    systemd.start("version", [sys.executable, "-m", "version"], environment=env, runner=DEFAULT_RUNNER)

    try:
        for pillar in ("registry", "version"):
            probe = probe_health(paths.socket_path(pillar), timeout=5.0, max_retries=10)
            assert probe.ok, f"{pillar} pillar never became healthy: {probe.error}"

        # Core: real app, in-process, with its registry already pointing at
        # the real (systemd-launched) registry pillar — the exact thing
        # version.resolve's discovery call depends on.
        core_supervisor = Supervisor(paths=paths, manifest=Manifest())
        core_supervisor.registry.set(
            RegistryEntry(name="registry", socket_path=paths.socket_path("registry"), status=PillarStatus.HEALTHY)
        )
        core_server = _UvicornThread(build_core_app(core_supervisor), paths.core_socket_path)
        core_server.start()

        def socket_lookup(pillar: str):
            if pillar in ("registry", "version"):
                return paths.socket_path(pillar)
            return None

        conn = orchestration_db.connect(paths.pillar_db_path("orchestration"))
        orch_server = _UvicornThread(build_orchestration_app(conn, socket_lookup), paths.socket_path("orchestration"))
        orch_server.start()

        try:
            dest_dir = tmp_path / "workdir"
            dest_dir.mkdir()
            bootstrap = cli_client.create_job(
                paths, type="project.bootstrap", target_pillar="registry", project_id="little-widget",
                params={"name": "little-widget", "dest_dir": str(dest_dir)}, queue_key=None, depends_on=[],
                created_by="integration-test",
            )
            assert bootstrap["status"] == "succeeded", bootstrap

            project_dir = dest_dir / "little-widget"
            # project.bootstrap (M2) already created master+dev — version.init
            # just needs to recognize them.
            init = cli_client.create_job(
                paths, type="version.init", target_pillar="version", project_id="little-widget",
                params={"production_branch": "master", "integration_branch": "dev"}, queue_key="little-widget",
                depends_on=[], created_by="integration-test",
            )
            assert init["status"] == "succeeded", init

            subprocess.run(["git", "add", "-A"], cwd=project_dir, check=True)
            subprocess.run(["git", "commit", "-m", "chore: version init"], cwd=project_dir, check=True, capture_output=True)
            subprocess.run(["git", "checkout", "dev"], cwd=project_dir, check=True, capture_output=True)
            subprocess.run(["git", "merge", "--ff-only", "master"], cwd=project_dir, check=True, capture_output=True)

            start = cli_client.create_job(
                paths, type="version.feature_start", target_pillar="version", project_id="little-widget",
                params={"name": "widgets"}, queue_key="little-widget", depends_on=[], created_by="integration-test",
            )
            assert start["status"] == "succeeded", start

            (project_dir / "widgets.py").write_text("x = 1\n", encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=project_dir, check=True)
            subprocess.run(["git", "commit", "-m", "add widgets"], cwd=project_dir, check=True, capture_output=True)

            finish = cli_client.create_job(
                paths, type="version.feature_finish", target_pillar="version", project_id="little-widget",
                params={}, queue_key="little-widget", depends_on=[], created_by="integration-test",
            )
            assert finish["status"] == "succeeded", finish

            subprocess.run(["git", "checkout", "dev"], cwd=project_dir, check=True, capture_output=True)
            assert (project_dir / "widgets.py").exists()
            assert (project_dir / "VERSION").read_text().strip() == "0.1.1"
        finally:
            orch_server.stop()
            core_server.stop()
            conn.close()
    finally:
        systemd.stop("registry", runner=DEFAULT_RUNNER)
        systemd.stop("version", runner=DEFAULT_RUNNER)
        import shutil

        shutil.rmtree(xdg_state, ignore_errors=True)
