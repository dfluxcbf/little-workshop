"""M2's end-to-end proof, mirroring M1's pattern: Registry & Portfolio
runs as a real `systemd --user` unit, and Orchestration dispatches a
real `project.bootstrap` Job to it over its Pillar Interface — the
result is an actual git repository on disk, not a mock.
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
            ["systemctl", "--user", "is-system-running"],
            capture_output=True,
            text=True,
            timeout=5,
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
from core.health import probe_health
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
def test_orchestration_dispatches_a_real_bootstrap_job_to_registry(tmp_path):
    # Unix-domain-socket paths are capped at ~108 bytes — pytest's own
    # tmp_path (nested under the test name) is often already too long once
    # "little-workshop/sockets/registry.sock" is appended, so the XDG
    # *state* base (where sockets live) gets its own short /tmp directory
    # instead. registry's own __main__ uses Paths.from_env(), which
    # appends "little-workshop" onto whatever XDG base it's given — mirror
    # that here so this test's `paths` points at exactly what the
    # subprocess will actually use.
    xdg_config = tmp_path / "xdgconfig"
    xdg_state = Path(tempfile.mkdtemp(prefix="lwsit-"))
    paths = Paths(config_home=xdg_config / "little-workshop", state_home=xdg_state / "little-workshop")
    paths.ensure_layout()

    registry_command = [sys.executable, "-m", "registry"]
    systemd.start(
        "registry",
        registry_command,
        environment={
            "PYTHONPATH": str(SRC),
            "XDG_CONFIG_HOME": str(xdg_config),
            "XDG_STATE_HOME": str(xdg_state),
        },
        runner=DEFAULT_RUNNER,
    )
    try:
        probe = probe_health(paths.socket_path("registry"), timeout=5.0, max_retries=10)
        assert probe.ok, f"registry pillar never became healthy: {probe.error}"

        def socket_lookup(pillar: str):
            return paths.socket_path("registry") if pillar == "registry" else None

        conn = orchestration_db.connect(paths.pillar_db_path("orchestration"))
        app = build_orchestration_app(conn, socket_lookup)
        server = _UvicornThread(app, paths.socket_path("orchestration"))
        server.start()
        try:
            dest_dir = tmp_path / "workdir"
            dest_dir.mkdir()
            created = cli_client.create_job(
                paths,
                type="project.bootstrap",
                target_pillar="registry",
                project_id="little-widget",
                params={"name": "little-widget", "dest_dir": str(dest_dir)},
                queue_key=None,
                depends_on=[],
                created_by="integration-test",
            )
            assert created["status"] == "succeeded", created

            project_dir = dest_dir / "little-widget"
            assert (project_dir / ".git").is_dir()
            assert (project_dir / "docs" / "design.md").exists()

            listed = cli_client.list_projects(paths)
            assert listed["source"] == "daemon"
            assert [p["name"] for p in listed["projects"]] == ["little-widget"]
        finally:
            server.stop()
            conn.close()
    finally:
        systemd.stop("registry", runner=DEFAULT_RUNNER)
        shutil.rmtree(xdg_state, ignore_errors=True)
