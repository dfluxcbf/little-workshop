from common.manifest import Manifest, PillarEntry
from common.paths import Paths
from common.shell import CommandResult
from core.health import HealthResult
from core.registry import PillarStatus, Registry
from core.supervisor import Supervisor


class FakeRunner:
    """Tracks `systemctl is-active` state per pillar and every call made."""

    def __init__(self, active: dict[str, bool] | None = None):
        self.active = dict(active or {})
        self.calls: list[list[str]] = []

    def run(self, argv, timeout=None):
        self.calls.append(argv)
        if argv[:3] == ["systemctl", "--user", "is-active"]:
            unit = argv[3]
            pillar = unit.removeprefix("little-workshop-").removesuffix(".service")
            return CommandResult(0, "active\n" if self.active.get(pillar) else "inactive\n", "")
        if argv[0] == "systemd-run":
            unit_arg = next(a for a in argv if a.startswith("--unit="))
            pillar = unit_arg.removeprefix("--unit=little-workshop-")
            self.active[pillar] = True
            return CommandResult(0, "", "")
        if argv[:3] == ["systemctl", "--user", "stop"]:
            unit = argv[3]
            pillar = unit.removeprefix("little-workshop-").removesuffix(".service")
            self.active[pillar] = False
            return CommandResult(0, "", "")
        return CommandResult(0, "", "")


def make_manifest(**overrides) -> Manifest:
    manifest = Manifest()
    manifest.pillars = {
        "orchestration": PillarEntry(name="orchestration", enabled=True, command=["run-orch"]),
        "policy": PillarEntry(name="policy", enabled=False, command=None),
    }
    for key, value in overrides.items():
        setattr(manifest, key, value)
    return manifest


def make_paths(tmp_path) -> Paths:
    return Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")


def probe_always(result: HealthResult):
    def _probe(socket_path, timeout, max_retries):
        return result

    return _probe


def test_startup_launches_a_not_yet_running_healthy_pillar(tmp_path):
    manifest = make_manifest()
    runner = FakeRunner(active={})
    supervisor = Supervisor(
        paths=make_paths(tmp_path),
        manifest=manifest,
        runner=runner,
        probe=probe_always(HealthResult(ok=True, attempts=1)),
    )
    supervisor.startup()

    entry = supervisor.registry.get("orchestration")
    assert entry.status == PillarStatus.HEALTHY
    assert any(c[0] == "systemd-run" for c in runner.calls)


def test_startup_reconnects_without_relaunching_a_running_healthy_pillar(tmp_path):
    manifest = make_manifest()
    runner = FakeRunner(active={"orchestration": True})
    supervisor = Supervisor(
        paths=make_paths(tmp_path),
        manifest=manifest,
        runner=runner,
        probe=probe_always(HealthResult(ok=True, attempts=1)),
    )
    supervisor.startup()

    entry = supervisor.registry.get("orchestration")
    assert entry.status == PillarStatus.RECONNECTED
    assert not any(c[0] == "systemd-run" for c in runner.calls)


def test_startup_stops_a_disabled_pillar_that_is_still_running(tmp_path):
    manifest = make_manifest()
    runner = FakeRunner(active={"policy": True})
    manifest.pillars["policy"].command = ["run-policy"]  # installed, but disabled
    supervisor = Supervisor(
        paths=make_paths(tmp_path),
        manifest=manifest,
        runner=runner,
        probe=probe_always(HealthResult(ok=True, attempts=1)),
    )
    supervisor.startup()

    assert runner.active["policy"] is False
    assert supervisor.registry.get("policy").status == PillarStatus.DISABLED


def test_startup_skips_pillars_with_no_command(tmp_path):
    manifest = make_manifest()  # "policy" has command=None
    manifest.pillars["policy"].enabled = True
    runner = FakeRunner()
    supervisor = Supervisor(
        paths=make_paths(tmp_path),
        manifest=manifest,
        runner=runner,
        probe=probe_always(HealthResult(ok=True, attempts=1)),
    )
    supervisor.startup()

    assert supervisor.registry.get("policy") is None


def test_reconcile_relaunches_a_dead_pillar_and_counts_the_crash(tmp_path):
    manifest = make_manifest()
    runner = FakeRunner(active={"orchestration": False})
    supervisor = Supervisor(
        paths=make_paths(tmp_path),
        manifest=manifest,
        runner=runner,
        probe=probe_always(HealthResult(ok=True, attempts=1)),
    )
    supervisor.registry.entries["orchestration"] = _registry_entry_crashed(supervisor, count=0)

    supervisor.reconcile_once()

    entry = supervisor.registry.get("orchestration")
    assert entry.status == PillarStatus.HEALTHY
    assert entry.crash_count == 0  # reset once confirmed healthy again


def test_reconcile_marks_faulted_after_max_attempts(tmp_path):
    manifest = make_manifest(crash_relaunch_max_attempts=3)
    runner = FakeRunner(active={"orchestration": False})
    supervisor = Supervisor(
        paths=make_paths(tmp_path),
        manifest=manifest,
        runner=runner,
        probe=probe_always(HealthResult(ok=False, attempts=1, error="down")),
    )

    for _ in range(4):
        supervisor.reconcile_once()
        runner.active["orchestration"] = False  # stays dead every time

    entry = supervisor.registry.get("orchestration")
    assert entry.status == PillarStatus.FAULTED
    assert entry.crash_count == 4  # the 4th attempt is what tips max_attempts=3 over


def test_reconcile_never_retries_a_faulted_pillar(tmp_path):
    manifest = make_manifest(crash_relaunch_max_attempts=1)
    runner = FakeRunner(active={"orchestration": False})
    supervisor = Supervisor(
        paths=make_paths(tmp_path),
        manifest=manifest,
        runner=runner,
        probe=probe_always(HealthResult(ok=False, attempts=1, error="down")),
    )
    supervisor.reconcile_once()
    supervisor.reconcile_once()
    assert supervisor.registry.get("orchestration").status == PillarStatus.FAULTED

    calls_before = len(runner.calls)
    supervisor.reconcile_once()
    # No new systemd-run attempted once faulted.
    assert not any(c[0] == "systemd-run" for c in runner.calls[calls_before:])


def _registry_entry_crashed(supervisor: Supervisor, count: int):
    from core.registry import RegistryEntry

    return RegistryEntry(
        name="orchestration",
        socket_path=supervisor.paths.socket_path("orchestration"),
        status=PillarStatus.CRASHED,
        crash_count=count,
    )
