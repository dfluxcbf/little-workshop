"""M9's end-to-end proof: a genuine `systemd --user` Orchestration unit,
with its real tick loop (sped up via `LWS_SCHEDULER_TICK_SECONDS`, never
set in production) firing a `ScheduledTask` for real against the real
`reference_pillar` test app (also a real `systemd --user` unit), plus a
real missed-firing recovery across an actual Orchestration process
restart, and a real `stop` against a Job the target pillar genuinely
interrupts.
"""

from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta

from cli import client as cli_client
from common import systemd
from common.manifest import Manifest
from common.shell import DEFAULT_RUNNER
from core.app import build_app as build_core_app
from core.health import probe_health
from core.registry import PillarStatus, RegistryEntry
from core.supervisor import Supervisor
from helpers import INTEGRATION, UvicornThread, requires_systemd_user


def _wait_until(predicate, timeout=10.0, interval=0.2):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


class _Session:
    """Real `reference` and `orchestration` systemd --user units, plus an
    in-process Core knowing about `reference`'s real socket — the same
    simplification test_version_end_to_end.py/test_deployment_end_to_end.py
    already use for Core specifically."""

    def __init__(self, workshop_env, *, tick_seconds: float):
        self.workshop_env = workshop_env
        self.tick_seconds = tick_seconds

    def __enter__(self):
        env = self.workshop_env.env
        reference_command = [sys.executable, "-m", "reference_pillar", str(self.workshop_env.paths.socket_path("reference"))]
        systemd.start("reference", reference_command, environment={**env, "PYTHONPATH": str(INTEGRATION)}, runner=DEFAULT_RUNNER)

        orch_env = {**env, "LWS_SCHEDULER_TICK_SECONDS": str(self.tick_seconds)}
        systemd.start("orchestration", [sys.executable, "-m", "orchestration"], environment=orch_env, runner=DEFAULT_RUNNER)

        for pillar in ("reference", "orchestration"):
            probe = probe_health(self.workshop_env.paths.socket_path(pillar), timeout=5.0, max_retries=10)
            assert probe.ok, f"{pillar}: {probe.error}"

        core_supervisor = Supervisor(paths=self.workshop_env.paths, manifest=Manifest())
        core_supervisor.registry.set(
            RegistryEntry(name="reference", socket_path=self.workshop_env.paths.socket_path("reference"), status=PillarStatus.HEALTHY)
        )
        self.core_server = UvicornThread(build_core_app(core_supervisor), self.workshop_env.paths.core_socket_path)
        self.core_server.start()
        return self

    def __exit__(self, *exc):
        self.core_server.stop()
        systemd.stop("orchestration", runner=DEFAULT_RUNNER)
        systemd.stop("reference", runner=DEFAULT_RUNNER)

    def restart_orchestration(self):
        """Simulates "little-workshop was down": stop and relaunch just
        the orchestration unit. Its own startup_check runs again, fresh,
        on the new process."""
        systemd.stop("orchestration", runner=DEFAULT_RUNNER)
        env = {**self.workshop_env.env, "LWS_SCHEDULER_TICK_SECONDS": str(self.tick_seconds)}
        systemd.start("orchestration", [sys.executable, "-m", "orchestration"], environment=env, runner=DEFAULT_RUNNER)
        probe = probe_health(self.workshop_env.paths.socket_path("orchestration"), timeout=5.0, max_retries=10)
        assert probe.ok, probe.error


@requires_systemd_user
def test_a_scheduled_task_fires_for_real_on_the_real_tick_loop(workshop_env):
    with _Session(workshop_env, tick_seconds=0.5):
        due_soon = (datetime.now() + timedelta(seconds=1)).replace(microsecond=0)
        created = cli_client.create_scheduled_task(
            workshop_env.paths, name="nightly-noop",
            job_template={"type": "noop", "target_pillar": "reference", "params": {"hi": "there"}},
            schedule={"kind": "once", "at": due_soon.isoformat()},
        )
        assert created["next_run_at"] == due_soon.isoformat()

        assert _wait_until(
            lambda: cli_client.get_scheduled_task(workshop_env.paths, created["id"])["scheduled_task"]["last_job_id"] is not None
        )

        task = cli_client.get_scheduled_task(workshop_env.paths, created["id"])["scheduled_task"]
        assert task["next_run_at"] is None  # once-kind: spent

        job = cli_client.job_status(workshop_env.paths, task["last_job_id"])["job"]
        assert job["created_by"] == "scheduler"
        assert job["status"] == "succeeded"
        assert job["result"] == {"echoed_params": {"hi": "there"}}


@requires_systemd_user
def test_a_missed_firing_is_flagged_across_a_real_orchestration_restart(workshop_env):
    with _Session(workshop_env, tick_seconds=600.0) as session:
        overdue = datetime(2020, 1, 1, 2, 0)
        created = cli_client.create_scheduled_task(
            workshop_env.paths, name="already-overdue",
            job_template={"type": "noop", "target_pillar": "reference"},
            schedule={"kind": "once", "at": overdue.isoformat()},
        )
        assert created["next_run_at"] == overdue.isoformat()
        assert created["missed_run_at"] is None

        # Simulate "little-workshop was down": restart the real unit.
        # Its own startup_check must discover the overdue task on the
        # new process, before this test calls anything else.
        session.restart_orchestration()

        reloaded = cli_client.get_scheduled_task(workshop_env.paths, created["id"])["scheduled_task"]
        assert reloaded["missed_run_at"] == overdue.isoformat()
        assert reloaded["next_run_at"] is None
        assert cli_client.list_jobs(workshop_env.paths)["jobs"] == []  # never actually fired

        # trigger resolves it, for real, against the real reference pillar.
        job = cli_client.trigger_scheduled_task(workshop_env.paths, created["id"])
        assert job["status"] == "succeeded"
        settled = cli_client.get_scheduled_task(workshop_env.paths, created["id"])["scheduled_task"]
        assert settled["missed_run_at"] is None


@requires_systemd_user
def test_stop_genuinely_interrupts_a_running_job_on_the_real_reference_pillar(workshop_env):
    with _Session(workshop_env, tick_seconds=600.0):
        created = cli_client.create_job(
            workshop_env.paths, type="slow", target_pillar="reference", params={},
            queue_key=None, depends_on=[], created_by="integration-test",
        )
        assert created["status"] == "running"

        result = cli_client.stop_job(workshop_env.paths, created["id"])
        assert result == {"stopped": True, "reason": None}

        settled = cli_client.job_status(workshop_env.paths, created["id"])["job"]
        assert settled["status"] == "stopped"
        assert settled["error"] == "stopped by user"


@requires_systemd_user
def test_queue_capacity_allows_real_concurrent_dispatch(workshop_env):
    with _Session(workshop_env, tick_seconds=600.0):
        cli_client.set_queue_capacity(workshop_env.paths, "little-meals", 2)

        first = cli_client.create_job(
            workshop_env.paths, type="slow", target_pillar="reference", queue_key="little-meals",
            params={}, depends_on=[], created_by="integration-test",
        )
        assert first["status"] == "running"

        second = cli_client.create_job(
            workshop_env.paths, type="noop", target_pillar="reference", queue_key="little-meals",
            params={}, depends_on=[], created_by="integration-test",
        )
        # Capacity 2: the second job dispatches immediately alongside
        # the first, rather than queuing behind it.
        assert second["status"] == "succeeded"
