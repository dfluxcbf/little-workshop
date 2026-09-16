"""Testing-fortification pass: Core's process-supervision machinery
beyond M1's original single-relaunch proof — crash-count accounting all
the way to FAULTED, a genuinely stopped unit's status, and a real health-
probe timeout against a process that never opens its socket.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

from common import systemd
from common.manifest import Manifest, PillarEntry
from common.shell import DEFAULT_RUNNER
from core.health import probe_health
from core.registry import PillarStatus
from core.supervisor import Supervisor

from helpers import INTEGRATION, requires_systemd_user


@requires_systemd_user
def test_pillar_reaches_faulted_after_exceeding_crash_relaunch_max_attempts(workshop_env):
    """A pillar whose command exits immediately (never binds its socket)
    can never come back healthy -- reconcile_once should keep retrying up
    to `crash_relaunch_max_attempts`, then stop and mark it FAULTED."""
    always_dies = [sys.executable, "-c", "import sys; sys.exit(1)"]
    manifest = Manifest(health_probe_timeout_seconds=0.3, health_probe_max_retries=1, crash_relaunch_max_attempts=2)
    manifest.pillars = {
        "reference": PillarEntry(name="reference", enabled=True, command=always_dies, environment={}),
    }
    supervisor = Supervisor(paths=workshop_env.paths, manifest=manifest, runner=DEFAULT_RUNNER)

    try:
        supervisor.startup()
        entry = supervisor.registry.get("reference")
        assert entry.status == PillarStatus.CRASHED  # never became healthy on first launch

        # Attempt 1 and 2 (crash_relaunch_max_attempts=2): still relaunching.
        supervisor.reconcile_once()
        assert supervisor.registry.get("reference").status in (PillarStatus.CRASHED, PillarStatus.FAULTED)
        supervisor.reconcile_once()
        # Attempt 3 exceeds the max -- must now be FAULTED.
        supervisor.reconcile_once()
        faulted = supervisor.registry.get("reference")
        assert faulted.status == PillarStatus.FAULTED
        assert faulted.crash_count > manifest.crash_relaunch_max_attempts

        # Once FAULTED, reconcile_once must stop touching it entirely --
        # crash_count must not keep climbing.
        stalled_count = faulted.crash_count
        supervisor.reconcile_once()
        supervisor.reconcile_once()
        assert supervisor.registry.get("reference").crash_count == stalled_count
    finally:
        systemd.stop("reference", runner=DEFAULT_RUNNER)


@requires_systemd_user
def test_a_genuinely_stopped_unit_is_reported_inactive_not_stale(workshop_env):
    reference_command = [sys.executable, "-m", "reference_pillar", str(workshop_env.paths.socket_path("reference"))]
    systemd.start("reference", reference_command, environment={"PYTHONPATH": str(INTEGRATION)}, runner=DEFAULT_RUNNER)
    try:
        probe = probe_health(workshop_env.paths.socket_path("reference"), timeout=5.0, max_retries=10)
        assert probe.ok, probe.error
        assert systemd.is_active("reference", runner=DEFAULT_RUNNER)

        systemd.stop("reference", runner=DEFAULT_RUNNER)
        deadline = time.time() + 10
        while systemd.is_active("reference", runner=DEFAULT_RUNNER) and time.time() < deadline:
            time.sleep(0.2)
        assert not systemd.is_active("reference", runner=DEFAULT_RUNNER)

        # A fresh Supervisor asked to start up now must see it as genuinely
        # down and launch it fresh, not silently reuse stale in-memory
        # status from before the stop.
        manifest = Manifest()
        manifest.pillars = {
            "reference": PillarEntry(name="reference", enabled=True, command=reference_command, environment={"PYTHONPATH": str(INTEGRATION)}),
        }
        supervisor = Supervisor(paths=workshop_env.paths, manifest=manifest, runner=DEFAULT_RUNNER)
        supervisor.startup()
        assert supervisor.registry.get("reference").status == PillarStatus.HEALTHY
        assert systemd.is_active("reference", runner=DEFAULT_RUNNER)
    finally:
        systemd.stop("reference", runner=DEFAULT_RUNNER)


@requires_systemd_user
def test_health_probe_times_out_against_a_process_that_never_binds(workshop_env):
    """A real subprocess that starts, keeps running, but never opens the
    socket at all -- distinct from `port_is_listening`-style mocks, this
    is a genuine process the probe genuinely can't connect to."""
    socket_path = workshop_env.paths.socket_path("reference")
    never_binds = [sys.executable, "-c", "import time; time.sleep(30)"]
    systemd.start("reference", never_binds, environment={}, runner=DEFAULT_RUNNER)
    try:
        assert systemd.is_active("reference", runner=DEFAULT_RUNNER)  # the process really is running

        result = probe_health(socket_path, timeout=0.3, max_retries=2)
        assert not result.ok
        assert result.error
    finally:
        systemd.stop("reference", runner=DEFAULT_RUNNER)


@requires_systemd_user
def test_crash_relaunch_recovers_a_unit_stopped_out_from_under_core(workshop_env):
    """The M1 proof's own "crash + relaunch" scenario, isolated here with
    explicit crash_count assertions the original test didn't make."""
    reference_command = [sys.executable, "-m", "reference_pillar", str(workshop_env.paths.socket_path("reference"))]
    manifest = Manifest()
    manifest.pillars = {
        "reference": PillarEntry(name="reference", enabled=True, command=reference_command, environment={"PYTHONPATH": str(INTEGRATION)}),
    }
    supervisor = Supervisor(paths=workshop_env.paths, manifest=manifest, runner=DEFAULT_RUNNER)

    try:
        supervisor.startup()
        assert supervisor.registry.get("reference").status == PillarStatus.HEALTHY

        systemd.stop("reference", runner=DEFAULT_RUNNER)
        deadline = time.time() + 10
        while systemd.is_active("reference", runner=DEFAULT_RUNNER) and time.time() < deadline:
            time.sleep(0.2)

        supervisor.reconcile_once()
        recovered = supervisor.registry.get("reference")
        assert recovered.status == PillarStatus.HEALTHY
        assert recovered.crash_count == 0  # a clean relaunch resets the count, not accumulates it
        assert systemd.is_active("reference", runner=DEFAULT_RUNNER)
    finally:
        systemd.stop("reference", runner=DEFAULT_RUNNER)
