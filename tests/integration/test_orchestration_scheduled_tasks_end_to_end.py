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

import pytest

from cli import client as cli_client
from common import systemd
from common.manifest import Manifest
from common.shell import DEFAULT_RUNNER
from core.app import build_app as build_core_app
from core.health import probe_health
from core.registry import PillarStatus, RegistryEntry
from core.supervisor import Supervisor
from helpers import INTEGRATION, UvicornThread, requires_systemd_user
from orchestration import db as orchestration_db
from orchestration.app import build_app as build_orchestration_app
from orchestration.schedule import next_occurrence_after
from reference_pillar.app import build_app as build_reference_app


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


# -- The remaining operations (cancel/postpone/remove/edit/list/get, and
# stop against a non-running job) don't need a real process restart or a
# real tick, so they run against a real Orchestration app + real
# reference_pillar app in-process on real Unix sockets -- the same
# simplification test_orchestration_lifecycle_end_to_end.py's own
# `orch_env` fixture already establishes -- rather than paying for a
# full systemd --user unit each. ------------------------------------


@pytest.fixture
def sched_env(workshop_env):
    paths = workshop_env.paths
    reference_server = UvicornThread(build_reference_app(), paths.socket_path("reference"))
    reference_server.start()

    def socket_lookup(pillar: str):
        return paths.socket_path("reference") if pillar == "reference" else None

    conn = orchestration_db.connect(paths.pillar_db_path("orchestration"))
    orch_server = UvicornThread(build_orchestration_app(conn, socket_lookup), paths.socket_path("orchestration"))
    orch_server.start()
    try:
        yield workshop_env
    finally:
        orch_server.stop()
        reference_server.stop()
        conn.close()


def _add(env, *, name="task", type_="noop", at=None, days=None, time_of_day=None):
    job_template = {"type": type_, "target_pillar": "reference", "params": {}}
    schedule = {"kind": "once", "at": at} if at else {
        "kind": "recurring", "days_of_week": days, "time_of_day": time_of_day,
    }
    return cli_client.create_scheduled_task(env.paths, name=name, job_template=job_template, schedule=schedule)


def test_cancel_skips_the_next_occurrence_for_a_recurring_task(sched_env):
    from orchestration.models import Schedule

    # Every weekday, 09:00 -- next_occurrence_after gives us the real
    # occurrence after any anchor, so the test never hardcodes a weekday.
    created = _add(sched_env, days=[0, 1, 2, 3, 4], time_of_day="09:00")
    task_id = created["id"]
    original_next = datetime.fromisoformat(created["next_run_at"])
    schedule = Schedule.model_validate(created["schedule"])
    expected_after_skip = next_occurrence_after(schedule, after=original_next)

    cancelled = cli_client.cancel_scheduled_task(sched_env.paths, task_id)
    assert cancelled == {"cancelled": "next_occurrence"}

    # The skip only ever moves next_run_at forward by exactly one real
    # occurrence of the recurring cadence, computed from whatever the
    # task's own next_run_at already was, never anything hardcoded here.
    reloaded = cli_client.get_scheduled_task(sched_env.paths, task_id)["scheduled_task"]
    assert reloaded["next_run_at"] == expected_after_skip.isoformat()
    assert reloaded["missed_run_at"] is None


def test_cancel_is_refused_for_a_once_task_pointing_at_remove_instead(sched_env):
    due = (datetime.now() + timedelta(days=1)).replace(microsecond=0)
    created = _add(sched_env, at=due.isoformat())

    result = cli_client.cancel_scheduled_task(sched_env.paths, created["id"])
    assert result["cancelled"] is None
    assert "remove" in result["reason"]

    # Refused, not silently a no-op -- the task's own next_run_at is
    # still exactly what it was.
    reloaded = cli_client.get_scheduled_task(sched_env.paths, created["id"])["scheduled_task"]
    assert reloaded["next_run_at"] == due.isoformat()


def test_postpone_retargets_a_pending_firing_and_clears_missed_run(sched_env):
    original = (datetime.now() + timedelta(days=2)).replace(microsecond=0)
    created = _add(sched_env, at=original.isoformat())

    new_at = (datetime.now() + timedelta(days=5)).replace(microsecond=0)
    postponed = cli_client.postpone_scheduled_task(sched_env.paths, created["id"], new_at.isoformat())
    assert postponed["next_run_at"] == new_at.isoformat()
    assert postponed["missed_run_at"] is None

    reloaded = cli_client.get_scheduled_task(sched_env.paths, created["id"])["scheduled_task"]
    assert reloaded["next_run_at"] == new_at.isoformat()


def test_remove_stops_a_running_scheduled_job_and_deletes_the_task(sched_env):
    created = _add(sched_env, type_="slow", at=datetime.now().isoformat())
    triggered = cli_client.trigger_scheduled_task(sched_env.paths, created["id"])
    assert triggered["status"] == "running"

    removed = cli_client.remove_scheduled_task(sched_env.paths, created["id"])
    assert removed == {"id": created["id"], "removed": True}

    settled_job = cli_client.job_status(sched_env.paths, triggered["id"])["job"]
    assert settled_job["status"] == "stopped"

    gone = cli_client.get_scheduled_task(sched_env.paths, created["id"])["scheduled_task"]
    assert gone is None


def test_remove_of_a_task_with_a_pending_future_firing_never_creates_a_job(sched_env):
    due = (datetime.now() + timedelta(days=3)).replace(microsecond=0)
    created = _add(sched_env, at=due.isoformat())

    removed = cli_client.remove_scheduled_task(sched_env.paths, created["id"])
    assert removed == {"id": created["id"], "removed": True}

    assert cli_client.list_jobs(sched_env.paths)["jobs"] == []
    assert cli_client.list_scheduled_tasks(sched_env.paths)["scheduled_tasks"] == []


def test_edit_recomputes_next_run_at_and_clears_missed_run_at(sched_env):
    original = (datetime.now() + timedelta(days=1)).replace(microsecond=0)
    created = _add(sched_env, at=original.isoformat())

    new_at = (datetime.now() + timedelta(days=10)).replace(microsecond=0)
    edited = cli_client.edit_scheduled_task(
        sched_env.paths, created["id"], schedule={"kind": "once", "at": new_at.isoformat()},
    )
    assert edited["next_run_at"] == new_at.isoformat()
    assert edited["missed_run_at"] is None
    assert edited["schedule"]["at"] == new_at.isoformat()

    renamed = cli_client.edit_scheduled_task(sched_env.paths, created["id"], name="renamed-task")
    assert renamed["name"] == "renamed-task"
    # A name-only edit never touches the schedule already set above.
    assert renamed["next_run_at"] == new_at.isoformat()


def test_list_and_get_scheduled_tasks_reflect_real_state(sched_env):
    first = _add(sched_env, name="first", at=(datetime.now() + timedelta(days=1)).isoformat())
    second = _add(sched_env, name="second", days=[5, 6], time_of_day="10:00")

    listed = cli_client.list_scheduled_tasks(sched_env.paths)["scheduled_tasks"]
    assert {t["id"] for t in listed} == {first["id"], second["id"]}
    assert {t["name"] for t in listed} == {"first", "second"}

    fetched = cli_client.get_scheduled_task(sched_env.paths, second["id"])["scheduled_task"]
    assert fetched["name"] == "second"
    assert fetched["schedule"]["kind"] == "recurring"
    assert fetched["schedule"]["days_of_week"] == [5, 6]


def test_stop_reports_not_running_for_an_already_succeeded_job(sched_env):
    created = _add(sched_env, type_="noop", at=datetime.now().isoformat())
    triggered = cli_client.trigger_scheduled_task(sched_env.paths, created["id"])
    assert triggered["status"] == "succeeded"

    result = cli_client.stop_job(sched_env.paths, triggered["id"])
    assert result["stopped"] is False
    assert "not running" in result["reason"]
