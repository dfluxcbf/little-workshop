"""M1's real end-to-end proof: Core launches and supervises a genuine
`systemd --user` unit (not a mock), and Orchestration dispatches a real
Job to it over a real Unix-domain-socket Pillar Interface call —
docs/milestones.md M1: "Proven end-to-end against one minimal
reference/stub pillar built for this milestone only."
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
import uvicorn

INTEGRATION = Path(__file__).resolve().parent


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
from common.manifest import Manifest, PillarEntry
from common.paths import Paths
from common.shell import DEFAULT_RUNNER
from core.health import probe_health
from core.registry import PillarStatus
from core.supervisor import Supervisor
from orchestration.app import build_app as build_orchestration_app
from orchestration import db as orchestration_db


class _UvicornThread:
    """Runs a real uvicorn server bound to a Unix socket in a background
    thread, for the duration of one test."""

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
        raise RuntimeError("orchestration test server did not start in time")

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=5)


@pytest.fixture
def paths(tmp_path) -> Paths:
    p = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    p.ensure_layout()
    return p


@requires_systemd_user
def test_core_supervises_a_real_systemd_unit_for_the_reference_pillar(paths):
    reference_command = [
        sys.executable,
        "-m",
        "reference_pillar",
        str(paths.socket_path("reference")),
    ]
    manifest = Manifest()
    manifest.pillars = {
        "reference": PillarEntry(
            name="reference",
            enabled=True,
            command=reference_command,
            environment={"PYTHONPATH": str(INTEGRATION)},
        ),
    }

    supervisor = Supervisor(paths=paths, manifest=manifest, runner=DEFAULT_RUNNER)

    try:
        # -- Fresh launch: Core starts a real transient systemd --user unit --
        supervisor.startup()
        entry = supervisor.registry.get("reference")
        assert entry.status == PillarStatus.HEALTHY
        assert systemd.is_active("reference", runner=DEFAULT_RUNNER)

        # A second Core "startup" (simulating Core's own restart) must
        # RECONNECT, not relaunch — docs/design.md's "Core's own recovery."
        supervisor_two = Supervisor(paths=paths, manifest=manifest, runner=DEFAULT_RUNNER)
        supervisor_two.startup()
        assert supervisor_two.registry.get("reference").status == PillarStatus.RECONNECTED

        # -- Crash + relaunch: stop the unit out from under Core, then --
        # -- confirm reconcile_once brings it back healthy. --
        systemd.stop("reference", runner=DEFAULT_RUNNER)
        deadline = time.time() + 10
        while systemd.is_active("reference", runner=DEFAULT_RUNNER) and time.time() < deadline:
            time.sleep(0.2)
        assert not systemd.is_active("reference", runner=DEFAULT_RUNNER)

        supervisor.reconcile_once()
        relaunched = supervisor.registry.get("reference")
        assert relaunched.status == PillarStatus.HEALTHY
        assert relaunched.crash_count == 0
        assert systemd.is_active("reference", runner=DEFAULT_RUNNER)
    finally:
        systemd.stop("reference", runner=DEFAULT_RUNNER)


@requires_systemd_user
def test_orchestration_dispatches_a_real_job_over_a_real_socket(paths):
    reference_command = [
        sys.executable,
        "-m",
        "reference_pillar",
        str(paths.socket_path("reference")),
    ]
    systemd.start(
        "reference",
        reference_command,
        environment={"PYTHONPATH": str(INTEGRATION)},
        runner=DEFAULT_RUNNER,
    )
    try:
        probe = probe_health(paths.socket_path("reference"), timeout=5.0, max_retries=10)
        assert probe.ok, f"reference pillar never became healthy: {probe.error}"

        def socket_lookup(pillar: str):
            return paths.socket_path("reference") if pillar == "reference" else None

        conn = orchestration_db.connect(paths.pillar_db_path("orchestration"))
        app = build_orchestration_app(conn, socket_lookup)
        server = _UvicornThread(app, paths.socket_path("orchestration"))
        server.start()
        try:
            created = cli_client.create_job(
                paths,
                type="noop",
                target_pillar="reference",
                project_id="little-meals",
                params={"greeting": "hello from Orchestration"},
                queue_key=None,
                depends_on=[],
                created_by="integration-test",
            )
            assert created["status"] == "succeeded"
            assert created["result"] == {
                "echoed_params": {"greeting": "hello from Orchestration"}
            }

            fetched = cli_client.job_status(paths, created["id"])
            assert fetched["source"] == "daemon"
            assert fetched["job"]["status"] == "succeeded"

            # A Job type the reference pillar deliberately fails.
            failed = cli_client.create_job(
                paths,
                type="always_fail",
                target_pillar="reference",
                project_id=None,
                params={},
                queue_key=None,
                depends_on=[],
                created_by="integration-test",
            )
            assert failed["status"] == "failed"
            assert "deliberate failure" in failed["error"]
        finally:
            server.stop()
            conn.close()
    finally:
        systemd.stop("reference", runner=DEFAULT_RUNNER)
