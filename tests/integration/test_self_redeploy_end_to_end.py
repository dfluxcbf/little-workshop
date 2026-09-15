"""M5's end-to-end proof: Deployment & Servers, running as a genuine
`systemd --user` unit, is asked to redeploy *itself* from this actual
repo's own HEAD commit. Because the request executes inside the very
unit that redeploy would stop, it must detect that (a real
`/proc/self/cgroup` check, not a mock) and dispatch to an independent,
detached `systemd-run` unit — which then genuinely stops the old
`little-workshop-deployment` unit and launches a new one from freshly
checked-out, freshly smoke-checked code, reporting completion back to
Orchestration's Job-completion callback.
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
REPO_ROOT = INTEGRATION.parents[1]


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

# The self-redeploy dispatch mechanism fundamentally depends on `lws`
# being resolvable via PATH inside a detached systemd-run unit — the same
# real dependency little-runner's own self-restart has on its own CLI
# being pipx-installed (see common/systemd.py's user_path_env). Skip
# cleanly, don't fail the suite, when that hasn't been set up
# (`bazel run //:install`) in this environment.
requires_lws_installed = pytest.mark.skipif(
    shutil.which("lws") is None, reason="lws is not installed (run `bazel run //:install`)"
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


def _wait_until(predicate, timeout=60.0, interval=0.5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


@requires_systemd_user
@requires_lws_installed
def test_deployment_redeploys_itself_via_a_real_dispatched_worker(tmp_path):
    xdg_config = tmp_path / "xdgconfig"
    xdg_state = Path(tempfile.mkdtemp(prefix="lwsit-"))
    paths = Paths(config_home=xdg_config / "little-workshop", state_home=xdg_state / "little-workshop")
    paths.ensure_layout()

    env = {"PYTHONPATH": str(SRC), "XDG_CONFIG_HOME": str(xdg_config), "XDG_STATE_HOME": str(xdg_state)}
    systemd.start("deployment", [sys.executable, "-m", "deployment"], environment=env, runner=DEFAULT_RUNNER)

    try:
        probe = probe_health(paths.socket_path("deployment"), timeout=5.0, max_retries=10)
        assert probe.ok, f"deployment pillar never became healthy: {probe.error}"

        core_supervisor = Supervisor(paths=paths, manifest=Manifest())
        core_supervisor.registry.set(
            RegistryEntry(name="deployment", socket_path=paths.socket_path("deployment"), status=PillarStatus.HEALTHY)
        )
        # The dispatched worker's own completion callback resolves
        # Orchestration's socket through Core, same as any other pillar —
        # it needs an entry here too, not just "deployment".
        core_supervisor.registry.set(
            RegistryEntry(name="orchestration", socket_path=paths.socket_path("orchestration"), status=PillarStatus.HEALTHY)
        )
        core_server = _UvicornThread(build_core_app(core_supervisor), paths.core_socket_path)
        core_server.start()

        def socket_lookup(pillar: str):
            return paths.socket_path("deployment") if pillar == "deployment" else None

        conn = orchestration_db.connect(paths.pillar_db_path("orchestration"))
        orch_server = _UvicornThread(build_orchestration_app(conn, socket_lookup), paths.socket_path("orchestration"))
        orch_server.start()

        try:
            head = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
            ).stdout.strip()

            created = cli_client.create_job(
                paths, type="deployment.redeploy_pillar", target_pillar="deployment", project_id="deployment",
                params={"pillar": "deployment", "commit": head}, queue_key="deployment", depends_on=[],
                created_by="integration-test",
            )
            # The request executed inside deployment's own unit's cgroup —
            # for real — so the Job comes back "running" (accepted), not
            # settled yet; the actual redeploy happens in a dispatched,
            # independent unit.
            assert created["status"] == "running", created
            job_id = created["id"]

            def job_settled():
                status = cli_client.job_status(paths, job_id)
                return status["job"] is not None and status["job"]["status"] in ("succeeded", "failed")

            assert _wait_until(job_settled, timeout=90.0), "self-redeploy Job never completed"

            final = cli_client.job_status(paths, job_id)["job"]
            assert final["status"] == "succeeded", final

            # The pillar is healthy again, at the same socket path, now
            # running from a freshly checked-out worktree of this exact
            # commit — not the process this test originally launched.
            probe_after = probe_health(paths.socket_path("deployment"), timeout=5.0, max_retries=10)
            assert probe_after.ok, f"deployment pillar unhealthy after self-redeploy: {probe_after.error}"

            with client_for(paths, "deployment") as deployment_client:
                confirmation_check = deployment_client.get("/v1/health")
            assert confirmation_check.json()["ok"] is True
        finally:
            orch_server.stop()
            core_server.stop()
            conn.close()
    finally:
        systemd.stop("deployment", runner=DEFAULT_RUNNER)
        # Sweep any dispatched worker/redeploy/watchdog units this test
        # spawned — including the confirm-gate timer, which would
        # otherwise sit armed for up to 10 minutes pointing at a socket
        # this test already tore down.
        for pattern in ("little-workshop-redeploy-*", "little-workshop-deploy-timeout-*"):
            subprocess.run(
                ["bash", "-c", f"systemctl --user list-units --all --no-legend '{pattern}' "
                 "| awk '{print $1}' | xargs -r systemctl --user stop"],
                check=False,
            )
        shutil.rmtree(xdg_state, ignore_errors=True)


def client_for(paths: Paths, pillar: str):
    from common.uds_client import client_for_socket

    return client_for_socket(paths.socket_path(pillar), timeout=5.0)
