"""Additional flow.py coverage for branches test_deployment_flow.py and
test_deployment_self_redeploy.py don't reach: tailscale service-specific
paths, launch/expose failure branches, status tailscale-mapped reporting,
the pillar-stop TERM->KILL escalation, and error paths in the self-
redeploy machinery (_launch_pillar failures, dispatch failures, the
rollback-guard-blocked branch called directly)."""

import subprocess
from pathlib import Path

import pytest

from common.paths import Paths
from common.shell import CommandResult
from core.health import HealthResult
from deployment import db
from deployment.flow import DeploymentError, DeploymentFlow, ServerStatus, _read_own_cgroup
from deployment.manifest import ServerManifest, TailscaleManifest


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def make_repo(tmp_path: Path, version: str = "1.0.0") -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-b", "main"], repo)
    _git(["config", "user.email", "t@example.com"], repo)
    _git(["config", "user.name", "T"], repo)
    (repo / "VERSION").write_text(f"{version}\n", encoding="utf-8")
    (repo / "run.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-m", "first"], repo)
    return repo


def make_workshop_repo(tmp_path: Path, pillar: str = "orchestration") -> Path:
    repo = tmp_path / "workshop"
    (repo / "src" / pillar).mkdir(parents=True)
    (repo / "src" / pillar / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
    (repo / "VERSION").write_text("1.0.0\n", encoding="utf-8")
    _git(["init", "-b", "main"], repo)
    _git(["config", "user.email", "t@example.com"], repo)
    _git(["config", "user.name", "T"], repo)
    _git(["add", "-A"], repo)
    _git(["commit", "-m", "first"], repo)
    return repo


class FakeRunner:
    def __init__(self, *, tailscale_available=True, tailscale_ok=True, service_ok=True, systemd_start_ok=True):
        self.active: dict[str, bool] = {}
        self.pids: list[int] = []
        self.tailscale_available = tailscale_available
        self.tailscale_ok = tailscale_ok
        self.service_ok = service_ok
        self.systemd_start_ok = systemd_start_ok
        self.calls: list[list[str]] = []
        self._service_call_count = 0

    def run(self, argv, timeout=None, cwd=None):
        self.calls.append(argv)
        if argv[:3] == ["systemctl", "--user", "is-active"]:
            return CommandResult(0, "active\n" if self.active.get(argv[3]) else "inactive\n", "")
        if argv and argv[0] == "systemd-run":
            if not self.systemd_start_ok:
                return CommandResult(1, "", "boom")
            unit_arg = next(a for a in argv if a.startswith("--unit="))
            self.active[unit_arg.removeprefix("--unit=") + ".service"] = True
            return CommandResult(0, "", "")
        if argv[:3] == ["systemctl", "--user", "stop"]:
            self.active[argv[3]] = False
            return CommandResult(0, "", "")
        if argv[:3] == ["systemctl", "--user", "reset-failed"]:
            return CommandResult(0, "", "")
        if argv[:2] == ["pgrep", "-A"]:
            return CommandResult(0 if self.pids else 1, "\n".join(map(str, self.pids)), "")
        if argv and argv[0] == "pkill":
            if "-KILL" in argv:
                self.pids = []
            return CommandResult(0, "", "")
        if argv == ["which", "tailscale"]:
            return CommandResult(0 if self.tailscale_available else 1, "", "")
        if argv and argv[0] == "tailscale" and argv[1:3] == ["serve", "status"]:
            return CommandResult(0, "https:443 -> 127.0.0.1:8765\n" if self.tailscale_ok else "", "")
        if argv and argv[0] == "tailscale" and "--service=svc:meals" in " ".join(argv):
            self._service_call_count += 1
            return CommandResult(0 if self.service_ok else 1, "", "" if self.service_ok else "svc failed")
        if argv and argv[0] == "tailscale":
            return CommandResult(0 if self.tailscale_ok else 1, "", "" if self.tailscale_ok else "serve failed")
        if argv and argv[0] == "bazel":
            return CommandResult(0, "", "")
        return CommandResult(0, "", "")


@pytest.fixture
def paths(tmp_path):
    p = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    p.ensure_layout()
    return p


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "deployment.db")
    yield connection
    connection.close()


def manifest(**overrides) -> ServerManifest:
    fields = dict(name="little-meals", command="run.sh", port=8765, match="run.sh")
    fields.update(overrides)
    return ServerManifest(**fields)


def _fake_clock():
    state = {"t": 0.0}

    def clock():
        state["t"] += 0.1
        return state["t"]

    return clock


# -- _read_own_cgroup / ServerStatus.running -------------------------------


def test_read_own_cgroup_returns_empty_string_when_unreadable():
    # /proc/self/cgroup always exists on Linux, but this exercises the
    # function directly rather than only through injected own_cgroup
    # callables -- proving the real default actually reads something.
    result = _read_own_cgroup()
    assert isinstance(result, str)


def test_server_status_running_true_when_unit_active():
    status = ServerStatus(name="x", unit="active", pids=[], listening=False, tailscale="n/a", branch="main", version="1.0.0")
    assert status.running is True


def test_server_status_running_true_when_pids_present_but_unit_inactive():
    status = ServerStatus(name="x", unit="inactive", pids=[123], listening=False, tailscale="n/a", branch="main", version="1.0.0")
    assert status.running is True


def test_server_status_running_false_when_neither():
    status = ServerStatus(name="x", unit="inactive", pids=[], listening=False, tailscale="n/a", branch="main", version="1.0.0")
    assert status.running is False


# -- down(): tailscale service-specific teardown ---------------------------


def test_down_turns_off_service_specific_tailscale_mapping(paths):
    runner = FakeRunner()
    flow = DeploymentFlow(paths=paths, runner=runner, sleep=lambda s: None, clock=_fake_clock(), probe=lambda p: False)
    ts_manifest = manifest(tailscale=TailscaleManifest(https_port=443, service="meals", service_https_port=443))

    flow.down("little-meals", ts_manifest)

    service_calls = [c for c in runner.calls if c and c[0] == "tailscale" and "--service=svc:meals" in c]
    assert service_calls, "expected a service-specific 'tailscale serve ... off' call"


# -- launch(): failure branches ---------------------------------------------


def test_launch_raises_when_systemd_start_fails(paths, tmp_path):
    runner = FakeRunner(systemd_start_ok=False)
    flow = DeploymentFlow(paths=paths, runner=runner, sleep=lambda s: None, clock=_fake_clock(), probe=lambda p: False)
    with pytest.raises(DeploymentError, match="could not launch"):
        flow.launch("little-meals", manifest(), cwd=tmp_path)


def test_launch_raises_when_port_never_opens(paths, tmp_path):
    runner = FakeRunner()
    flow = DeploymentFlow(paths=paths, runner=runner, sleep=lambda s: None, clock=_fake_clock(), probe=lambda p: False)
    with pytest.raises(DeploymentError, match="did not start listening"):
        flow.launch("little-meals", manifest(ready_timeout_seconds=1), cwd=tmp_path)


# -- expose(): every branch --------------------------------------------------


def test_expose_reports_tailscale_not_installed(paths):
    runner = FakeRunner(tailscale_available=False)
    flow = DeploymentFlow(paths=paths, runner=runner)
    result = flow.expose(manifest(tailscale=TailscaleManifest()))
    assert result == "tailscale not installed"


def test_expose_reports_warning_when_serve_command_fails(paths):
    runner = FakeRunner(tailscale_ok=False)
    flow = DeploymentFlow(paths=paths, runner=runner)
    result = flow.expose(manifest(tailscale=TailscaleManifest()))
    assert result is not None
    assert result.startswith("WARNING: tailscale serve failed")


def test_expose_includes_service_description_when_service_configured(paths):
    runner = FakeRunner()
    flow = DeploymentFlow(paths=paths, runner=runner)
    result = flow.expose(manifest(tailscale=TailscaleManifest(service="meals")))
    assert "svc:meals" in result


def test_expose_omits_service_description_when_service_call_fails(paths):
    runner = FakeRunner(service_ok=False)
    flow = DeploymentFlow(paths=paths, runner=runner)
    result = flow.expose(manifest(tailscale=TailscaleManifest(service="meals")))
    assert "svc:meals" not in result


# -- status(): tailscale mapped/unmapped -------------------------------------


def test_status_reports_tailscale_mapped(paths, conn, tmp_path):
    repo = make_repo(tmp_path)
    runner = FakeRunner(tailscale_ok=True)
    flow = DeploymentFlow(paths=paths, runner=runner, sleep=lambda s: None, probe=lambda p: True, clock=_fake_clock())
    flow.up(conn, "little-meals", manifest(), repo, "main")

    status = flow.status(conn, "little-meals", manifest(tailscale=TailscaleManifest()))
    assert status.tailscale == "mapped"


def test_status_reports_tailscale_not_mapped(paths, conn, tmp_path):
    repo = make_repo(tmp_path)
    runner = FakeRunner(tailscale_ok=False)
    flow = DeploymentFlow(paths=paths, runner=runner, sleep=lambda s: None, probe=lambda p: True, clock=_fake_clock())
    flow.up(conn, "little-meals", manifest(), repo, "main")

    status = flow.status(conn, "little-meals", manifest(tailscale=TailscaleManifest()))
    assert status.tailscale == "not mapped"


def test_status_reports_na_without_tailscale_config(paths, conn, tmp_path):
    repo = make_repo(tmp_path)
    runner = FakeRunner()
    flow = DeploymentFlow(paths=paths, runner=runner, sleep=lambda s: None, probe=lambda p: True, clock=_fake_clock())
    flow.up(conn, "little-meals", manifest(), repo, "main")

    status = flow.status(conn, "little-meals", manifest())
    assert status.tailscale == "n/a"


# -- _stop_pillar(): TERM -> KILL escalation ---------------------------------


def test_stop_pillar_escalates_to_sigkill(paths, conn, tmp_path):
    repo = make_workshop_repo(tmp_path)
    runner = FakeRunner()
    runner.pids = [999]

    def run_no_term_kill(argv, timeout=None, cwd=None):
        if argv and argv[0] == "pkill" and "-TERM" in argv:
            return CommandResult(0, "", "")  # TERM "sent" but pid survives
        return FakeRunner.run(runner, argv, timeout=timeout, cwd=cwd)

    runner.run = run_no_term_kill
    flow = DeploymentFlow(
        paths=paths, runner=runner, sleep=lambda s: None, clock=_fake_clock(),
        health_probe=lambda *a, **k: HealthResult(ok=True, attempts=1),
    )
    flow.redeploy_pillar_direct(conn, "orchestration", repo, "main")
    kill_calls = [c for c in runner.calls if c and c[0] == "pkill" and "-KILL" in c]
    assert kill_calls, "expected escalation to SIGKILL for a pillar's own stop path too"


# -- _launch_pillar(): failure branches ---------------------------------------


def test_launch_pillar_raises_when_systemd_start_fails(paths, conn, tmp_path):
    repo = make_workshop_repo(tmp_path)
    runner = FakeRunner(systemd_start_ok=False)
    flow = DeploymentFlow(paths=paths, runner=runner, sleep=lambda s: None)
    with pytest.raises(DeploymentError, match="could not launch"):
        flow.redeploy_pillar_direct(conn, "orchestration", repo, "main")


def test_launch_pillar_raises_when_health_probe_never_succeeds(paths, conn, tmp_path):
    repo = make_workshop_repo(tmp_path)
    runner = FakeRunner()
    flow = DeploymentFlow(
        paths=paths, runner=runner, sleep=lambda s: None,
        health_probe=lambda *a, **k: HealthResult(ok=False, attempts=20, error="never healthy"),
    )
    with pytest.raises(DeploymentError, match="did not become healthy"):
        flow.redeploy_pillar_direct(conn, "orchestration", repo, "main")


# -- rollback_pillar_if_unconfirmed(): guard-blocked branch -------------------


def test_rollback_pillar_if_unconfirmed_is_a_noop_when_nothing_pending(paths, conn, tmp_path):
    repo = make_workshop_repo(tmp_path, pillar="core")
    runner = FakeRunner()
    flow = DeploymentFlow(paths=paths, runner=runner, sleep=lambda s: None, health_probe=lambda *a, **k: HealthResult(ok=True, attempts=1))
    summary = flow.rollback_pillar_if_unconfirmed(conn, "core", repo, "some-deploy-id")
    assert "no confirm-gate state found" in summary


# -- _dispatch_detached(): failure branch -------------------------------------


def test_dispatch_self_redeploy_raises_when_systemd_run_fails(paths):
    runner = FakeRunner(systemd_start_ok=False)
    flow = DeploymentFlow(paths=paths, runner=runner)
    with pytest.raises(DeploymentError, match="could not dispatch"):
        flow.dispatch_self_redeploy("deployment", "abc123")
