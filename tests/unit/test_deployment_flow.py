import subprocess
from pathlib import Path

import pytest

from common.paths import Paths
from common.shell import CommandResult
from deployment import db
from deployment.flow import DeploymentError, DeploymentFlow
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
    _git(["branch", "dev"], repo)
    return repo


class FakeRunner:
    def __init__(self, *, install_ok: bool = True, tailscale_available: bool = True):
        self.active: dict[str, bool] = {}
        self.pids: list[int] = []
        self.install_ok = install_ok
        self.tailscale_available = tailscale_available
        self.calls: list[list[str]] = []

    def run(self, argv, timeout=None, cwd=None):
        self.calls.append(argv)

        if argv[:3] == ["systemctl", "--user", "is-active"]:
            unit = argv[3]
            return CommandResult(0, "active\n" if self.active.get(unit) else "inactive\n", "")
        if argv and argv[0] == "systemd-run":
            unit_arg = next(a for a in argv if a.startswith("--unit="))
            unit = unit_arg.removeprefix("--unit=") + ".service"
            self.active[unit] = True
            return CommandResult(0, "", "")
        if argv[:3] == ["systemctl", "--user", "stop"]:
            self.active[argv[3]] = False
            return CommandResult(0, "", "")
        if argv[:3] == ["systemctl", "--user", "reset-failed"]:
            return CommandResult(0, "", "")
        if argv[:2] == ["pgrep", "-A"]:
            return CommandResult(0 if self.pids else 1, "\n".join(map(str, self.pids)), "")
        if argv and argv[0] == "pkill":
            self.pids = []
            return CommandResult(0, "", "")
        if argv == ["which", "tailscale"]:
            return CommandResult(0 if self.tailscale_available else 1, "", "")
        if argv and argv[0] == "tailscale":
            return CommandResult(0, "", "")
        if argv and argv[0] == "bazel":
            return CommandResult(0 if self.install_ok else 1, "", "" if self.install_ok else "build failed")
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


def make_flow(paths, runner, *, listening_after_launch: bool = True) -> DeploymentFlow:
    launched = {"value": False}

    def probe(port):
        return launched["value"] and listening_after_launch

    real_run = runner.run

    def run_and_mark(argv, timeout=None, cwd=None):
        result = real_run(argv, timeout=timeout, cwd=cwd)
        if argv and argv[0] == "systemd-run":
            launched["value"] = True
        if argv[:3] == ["systemctl", "--user", "stop"]:
            launched["value"] = False
        return result

    runner.run = run_and_mark
    return DeploymentFlow(paths=paths, runner=runner, sleep=lambda s: None, probe=probe, clock=_fake_clock())


def _fake_clock():
    state = {"t": 0.0}

    def clock():
        state["t"] += 0.1
        return state["t"]

    return clock


def test_up_deploys_from_worktree_and_records_state(paths, conn, tmp_path):
    repo = make_repo(tmp_path)
    runner = FakeRunner()
    flow = make_flow(paths, runner)

    result = flow.up(conn, "little-meals", manifest(), repo, "main")

    assert result.branch == "main"
    assert result.version == "1.0.0"
    assert result.confirm_message is None
    state = db.get_server_state(conn, "little-meals")
    assert state["version"] == "1.0.0"
    deployments = db.list_deployments(conn, "little-meals")
    assert deployments[0]["outcome"] == "succeeded"


def test_up_rejects_unknown_branch_without_stopping_anything(paths, conn, tmp_path):
    repo = make_repo(tmp_path)
    runner = FakeRunner()
    flow = make_flow(paths, runner)

    with pytest.raises(DeploymentError):
        flow.up(conn, "little-meals", manifest(), repo, "ghost-branch")

    assert not any(c[:3] == ["systemctl", "--user", "stop"] for c in runner.calls)


def test_up_never_stops_running_server_when_install_fails(paths, conn, tmp_path):
    repo = make_repo(tmp_path)
    runner = FakeRunner(install_ok=False)
    flow = make_flow(paths, runner)

    with pytest.raises(DeploymentError):
        flow.up(conn, "little-meals", manifest(), repo, "main")

    assert not any(c[:3] == ["systemctl", "--user", "stop"] for c in runner.calls)


def test_up_worktree_is_removed_after_deploy(paths, conn, tmp_path):
    repo = make_repo(tmp_path)
    runner = FakeRunner()
    flow = make_flow(paths, runner)
    flow.up(conn, "little-meals", manifest(), repo, "main")
    assert not paths.worktree_path("deploy-little-meals").exists()


def test_up_arms_confirm_gate_when_configured(paths, conn, tmp_path):
    repo = make_repo(tmp_path)
    runner = FakeRunner()
    flow = make_flow(paths, runner)

    result = flow.up(conn, "little-meals", manifest(confirm_timeout_seconds=600), repo, "main")

    assert result.confirm_message is not None
    state = db.get_confirmation(conn, "little-meals")
    assert state["status"] == db.PENDING
    assert any(c[0] == "systemd-run" and "rollback-timeout" in c for c in runner.calls)


def test_down_reports_unit_stopped(paths, conn, tmp_path):
    repo = make_repo(tmp_path)
    runner = FakeRunner()
    flow = make_flow(paths, runner)
    flow.up(conn, "little-meals", manifest(), repo, "main")

    report = flow.down("little-meals", manifest())
    assert report.unit_stopped is True


def test_down_escalates_to_sigkill_when_term_does_not_stop_pids(paths, tmp_path):
    runner = FakeRunner()
    runner.pids = [12345]

    def run_no_kill(argv, timeout=None, cwd=None):
        if argv and argv[0] == "pkill" and "-TERM" in argv:
            return CommandResult(0, "", "")  # TERM sent, but pid "survives" (test controls pids)
        return FakeRunner.run(runner, argv, timeout=timeout, cwd=cwd)

    runner.run = run_no_kill
    flow = DeploymentFlow(paths=Paths(config_home=Path("/tmp/x"), state_home=Path("/tmp/y")), runner=runner, sleep=lambda s: None, clock=_fake_clock(), probe=lambda p: False)

    flow.down("little-meals", manifest(stop_timeout_seconds=1))
    kill_calls = [c for c in runner.calls if c and c[0] == "pkill" and "-KILL" in c]
    assert kill_calls, "expected escalation to SIGKILL when TERM didn't clear the pids"


def test_expose_calls_tailscale_serve(paths, tmp_path):
    runner = FakeRunner()
    flow = DeploymentFlow(paths=paths, runner=runner)
    exposure = flow.expose(manifest(tailscale=TailscaleManifest(https_port=443)))
    assert exposure is not None
    assert "tailscale serve" in exposure


def test_expose_returns_none_without_tailscale_config(paths):
    runner = FakeRunner()
    flow = DeploymentFlow(paths=paths, runner=runner)
    assert flow.expose(manifest()) is None


def test_seed_confirmation_requires_a_prior_deploy(paths, conn):
    runner = FakeRunner()
    flow = DeploymentFlow(paths=paths, runner=runner)
    with pytest.raises(DeploymentError):
        flow.seed_confirmation(conn, "little-meals")


def test_rollback_if_unconfirmed_redeploys_confirmed_commit(paths, conn, tmp_path):
    repo = make_repo(tmp_path, version="1.0.0")
    runner = FakeRunner()
    flow = make_flow(paths, runner)

    # Establish a real confirmed baseline (an un-gated first deploy), then
    # seed it, before the first confirm-gated deploy — matching the
    # documented assumption that a baseline must already exist.
    flow.up(conn, "little-meals", manifest(), repo, "main")
    flow.seed_confirmation(conn, "little-meals")

    _git(["checkout", "dev"], repo)
    (repo / "VERSION").write_text("1.0.1\n", encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-m", "bump"], repo)

    flow.up(conn, "little-meals", manifest(confirm_timeout_seconds=600), repo, "dev")
    pending = db.get_confirmation(conn, "little-meals")
    assert pending["status"] == db.PENDING
    deploy_id = pending["deploy_id"]

    summary = flow.rollback_if_unconfirmed(conn, "little-meals", manifest(confirm_timeout_seconds=600), repo, deploy_id)
    assert "rolled back" in summary
    state = db.get_confirmation(conn, "little-meals")
    assert state["status"] == db.CONFIRMED
    assert state["confirmed_version"] == "1.0.0"  # rolled back to the seeded baseline, not the pending 1.0.1


def test_rollback_is_noop_for_stale_deploy_id(paths, conn, tmp_path):
    repo = make_repo(tmp_path)
    runner = FakeRunner()
    flow = make_flow(paths, runner)
    flow.up(conn, "little-meals", manifest(), repo, "main")
    flow.seed_confirmation(conn, "little-meals")
    flow.up(conn, "little-meals", manifest(confirm_timeout_seconds=600), repo, "main")

    summary = flow.rollback_if_unconfirmed(conn, "little-meals", manifest(confirm_timeout_seconds=600), repo, "stale-id")
    assert "stale watchdog" in summary


def test_status_reports_cached_state(paths, conn, tmp_path):
    repo = make_repo(tmp_path)
    runner = FakeRunner()
    flow = make_flow(paths, runner)
    flow.up(conn, "little-meals", manifest(), repo, "main")

    status = flow.status(conn, "little-meals", manifest())
    assert status.branch == "main"
    assert status.version == "1.0.0"
    assert status.unit == "active"


# -- externally managed servers ------------------------------------------------------------


def external_manifest(**overrides) -> ServerManifest:
    fields = dict(
        name="little-workshop", command="python3 bridge.py", port=8790, match="dashboard_bridge.py",
        external=True, unit="little-workshop-dashboard-bridge.service",
        tailscale=TailscaleManifest(https_port=443, service="little-workshop"),
    )
    fields.update(overrides)
    return ServerManifest(**fields)


def _touching_calls(runner):
    """Anything that would build, launch, stop, kill or (un)expose — none may happen to an external server."""
    forbidden = ("systemd-run", "pkill", "bazel")
    return [
        c for c in runner.calls
        if (c and c[0] in forbidden)
        or c[:3] == ["systemctl", "--user", "stop"] or c[:3] == ["systemctl", "--user", "reset-failed"]
        or (c[:2] == ["tailscale", "serve"] and c[2:3] != ["status"])
    ]


def test_stopping_an_external_server_is_refused_before_anything_is_touched(paths, conn):
    runner = FakeRunner()
    runner.pids = [4711]
    flow = make_flow(paths, runner)
    with pytest.raises(DeploymentError, match="managed outside Deployment.*dashboard-bridge.*will not stop"):
        flow.down("little-workshop", external_manifest())
    assert runner.calls == [] and runner.pids == [4711]


def test_deploying_an_external_server_is_refused_before_git_or_the_build_run(paths, conn, tmp_path):
    runner = FakeRunner()
    flow = make_flow(paths, runner)
    with pytest.raises(DeploymentError, match="will not deploy"):
        flow.up(conn, "little-workshop", external_manifest(), tmp_path / "no-such-checkout", "dev")
    assert runner.calls == []
    assert db.list_deployments(conn, "little-workshop") == []


def test_status_of_an_external_server_reports_its_own_unit_and_flags_it_external(paths, conn):
    runner = FakeRunner()
    runner.pids = [4711]
    runner.active["little-workshop-dashboard-bridge.service"] = True
    flow = make_flow(paths, runner)
    flow.probe = lambda port: port == 8790
    status = flow.status(conn, "little-workshop", external_manifest())
    assert status.external is True and status.unit == "active" and status.listening is True and status.pids == [4711]
    assert ["systemctl", "--user", "is-active", "little-workshop-dashboard-bridge.service"] in runner.calls
    assert _touching_calls(runner) == []


def test_status_reports_an_inactive_external_unit(paths, conn):
    flow = make_flow(paths, FakeRunner())
    status = flow.status(conn, "little-workshop", external_manifest())
    assert status.unit == "inactive" and status.external is True


def test_status_of_an_ordinary_server_is_unchanged_and_not_external(paths, conn):
    runner = FakeRunner()
    flow = make_flow(paths, runner)
    status = flow.status(conn, "little-meals", manifest())
    assert status.external is False
    assert ["systemctl", "--user", "is-active", "little-workshop-deploy-little-meals.service"] in runner.calls


def test_an_external_manifest_without_a_unit_falls_back_to_the_deploy_unit_name(paths, conn):
    runner = FakeRunner()
    make_flow(paths, runner).status(conn, "little-workshop", external_manifest(unit=None))
    assert ["systemctl", "--user", "is-active", "little-workshop-deploy-little-workshop.service"] in runner.calls
