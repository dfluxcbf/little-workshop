import subprocess
from pathlib import Path

import pytest

from common.paths import Paths
from common.shell import CommandResult
from core.health import HealthResult
from deployment import db
from deployment.flow import (
    DEFAULT_SELF_REDEPLOY_CONFIRM_SECONDS,
    DeploymentError,
    DeploymentFlow,
    SelfRedeployRequiresDispatch,
)


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def make_workshop_repo(tmp_path: Path, *, pillar: str = "orchestration", version: str = "1.0.0") -> Path:
    """A throwaway repo standing in for little-workshop's own checkout:
    a real `src/<pillar>/__init__.py` a smoke-check can actually import."""
    repo = tmp_path / "workshop"
    (repo / "src" / pillar).mkdir(parents=True)
    (repo / "src" / pillar / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
    (repo / "VERSION").write_text(f"{version}\n", encoding="utf-8")
    _git(["init", "-b", "main"], repo)
    _git(["config", "user.email", "t@example.com"], repo)
    _git(["config", "user.name", "T"], repo)
    _git(["add", "-A"], repo)
    _git(["commit", "-m", "first"], repo)
    return repo


def break_the_pillar(repo: Path, pillar: str, version: str = "1.0.1") -> None:
    (repo / "src" / pillar / "__init__.py").write_text("this is not valid python (((\n", encoding="utf-8")
    (repo / "VERSION").write_text(f"{version}\n", encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-m", "oops"], repo)


def fix_the_pillar(repo: Path, pillar: str, version: str = "1.0.2") -> None:
    (repo / "src" / pillar / "__init__.py").write_text("VALUE = 2\n", encoding="utf-8")
    (repo / "VERSION").write_text(f"{version}\n", encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-m", "fixed"], repo)


class FakeRunner:
    """Real subprocess passthrough for git/python smoke-checks (so the
    import check is genuine), faked for systemctl/systemd-run/pgrep/pkill —
    mirrors the FakeRunner pattern used throughout M1-M4's own tests."""

    def __init__(self):
        self.active: dict[str, bool] = {}
        self.pids: list[int] = []
        self.calls: list[list[str]] = []

    def run(self, argv, timeout=None, cwd=None):
        self.calls.append(argv)
        if argv[:3] == ["systemctl", "--user", "is-active"]:
            return CommandResult(0, "active\n" if self.active.get(argv[3]) else "inactive\n", "")
        if argv and argv[0] == "systemd-run":
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
            self.pids = []
            return CommandResult(0, "", "")
        # git / python: real subprocess, so the smoke-check is genuine.
        import subprocess as sp

        result = sp.run(argv, cwd=str(cwd) if cwd else None, capture_output=True, text=True, timeout=timeout)
        return CommandResult(result.returncode, result.stdout, result.stderr)


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


def healthy_probe(_socket_path, timeout=5.0, max_retries=20, sleep=None):
    return HealthResult(ok=True, attempts=1)


def make_flow(paths, runner, *, own_cgroup=lambda: "") -> DeploymentFlow:
    return DeploymentFlow(paths=paths, runner=runner, sleep=lambda s: None, own_cgroup=own_cgroup, health_probe=healthy_probe)


def test_redeploy_pillar_direct_copies_worktree_into_stable_install_dir(paths, conn, tmp_path):
    repo = make_workshop_repo(tmp_path)
    runner = FakeRunner()
    flow = make_flow(paths, runner)

    result = flow.redeploy_pillar_direct(conn, "orchestration", repo, "main")

    assert result.version == "1.0.0"
    deployment = db.get_pillar_deployment(conn, "orchestration")
    install_dir = Path(deployment["install_dir"])
    assert (install_dir / "src" / "orchestration" / "__init__.py").exists()
    assert deployment["commit_sha"] != "main"  # resolved to a real SHA


def test_redeploy_pillar_direct_removes_the_scratch_worktree(paths, conn, tmp_path):
    repo = make_workshop_repo(tmp_path)
    runner = FakeRunner()
    flow = make_flow(paths, runner)
    flow.redeploy_pillar_direct(conn, "orchestration", repo, "main")
    leftover = list(paths.worktrees_dir.glob("redeploy-orchestration-*"))
    assert leftover == []


def test_redeploy_pillar_direct_never_stops_old_process_on_broken_code(paths, conn, tmp_path):
    repo = make_workshop_repo(tmp_path)
    break_the_pillar(repo, "orchestration")
    runner = FakeRunner()
    runner.active["little-workshop-orchestration.service"] = True  # already running
    flow = make_flow(paths, runner)

    with pytest.raises(DeploymentError, match="does not even import cleanly"):
        flow.redeploy_pillar_direct(conn, "orchestration", repo, "HEAD")

    assert not any(c[:3] == ["systemctl", "--user", "stop"] for c in runner.calls)
    assert runner.active["little-workshop-orchestration.service"] is True


def test_redeploy_pillar_direct_cleans_up_the_previous_install_dir(paths, conn, tmp_path):
    repo = make_workshop_repo(tmp_path)
    runner = FakeRunner()
    flow = make_flow(paths, runner)

    first = flow.redeploy_pillar_direct(conn, "orchestration", repo, "main")
    first_install_dir = Path(db.get_pillar_deployment(conn, "orchestration")["install_dir"])

    fix_the_pillar(repo, "orchestration")
    flow.redeploy_pillar_direct(conn, "orchestration", repo, "main")

    assert not first_install_dir.exists()


def test_redeploy_pillar_raises_dispatch_signal_when_inside_own_cgroup(paths, conn, tmp_path):
    repo = make_workshop_repo(tmp_path, pillar="deployment")
    runner = FakeRunner()
    flow = make_flow(paths, runner, own_cgroup=lambda: "0::/user.slice/little-workshop-deployment.service")

    with pytest.raises(SelfRedeployRequiresDispatch):
        flow.redeploy_pillar(conn, "deployment", repo, "main")


def test_redeploy_pillar_runs_inline_when_targeting_a_different_pillar(paths, conn, tmp_path):
    repo = make_workshop_repo(tmp_path, pillar="policy")
    runner = FakeRunner()
    # This process happens to be inside deployment's own cgroup, but the
    # redeploy TARGET is a different pillar -- safe to run inline.
    flow = make_flow(paths, runner, own_cgroup=lambda: "0::/user.slice/little-workshop-deployment.service")

    result = flow.redeploy_pillar(conn, "policy", repo, "main")
    assert result.version == "1.0.0"


def test_redeploy_pillar_arms_confirm_gate_by_default_for_core_and_deployment(paths, conn, tmp_path):
    repo = make_workshop_repo(tmp_path, pillar="core")
    runner = FakeRunner()
    flow = make_flow(paths, runner)

    result = flow.redeploy_pillar(conn, "core", repo, "main")
    assert result.confirm_message is not None
    state = db.get_confirmation(conn, "core")
    assert state["status"] == db.PENDING


def test_redeploy_pillar_does_not_gate_other_pillars_by_default(paths, conn, tmp_path):
    repo = make_workshop_repo(tmp_path, pillar="policy")
    runner = FakeRunner()
    flow = make_flow(paths, runner)

    result = flow.redeploy_pillar(conn, "policy", repo, "main")
    assert result.confirm_message is None
    assert db.get_confirmation(conn, "policy") is None


def test_rollback_pillar_if_unconfirmed_redeploys_confirmed_commit(paths, conn, tmp_path):
    repo = make_workshop_repo(tmp_path, pillar="core")
    runner = FakeRunner()
    flow = make_flow(paths, runner)

    first = flow.redeploy_pillar_direct(conn, "core", repo, "main")
    db.seed_confirmation(conn, "core", "main", first.commit, first.version)

    fix_the_pillar(repo, "core", version="2.0.0")
    pending = flow.redeploy_pillar(conn, "core", repo, "HEAD")
    assert pending.confirm_message is not None
    state = db.get_confirmation(conn, "core")
    deploy_id = state["deploy_id"]

    summary = flow.rollback_pillar_if_unconfirmed(conn, "core", repo, deploy_id)
    assert "rolled back" in summary
    settled = db.get_confirmation(conn, "core")
    assert settled["status"] == db.CONFIRMED
    assert settled["confirmed_version"] == "1.0.0"


def test_rollback_pillar_if_unconfirmed_raises_dispatch_signal_when_self_targeting(paths, conn, tmp_path):
    repo = make_workshop_repo(tmp_path, pillar="deployment")
    runner = FakeRunner()
    flow = make_flow(paths, runner)
    first = flow.redeploy_pillar_direct(conn, "deployment", repo, "main")
    db.seed_confirmation(conn, "deployment", "main", first.commit, first.version)
    pending = db.mark_pending(conn, "deployment", "main", "deadbeef", "2.0.0", timeout_seconds=600)

    self_aware_flow = make_flow(paths, runner, own_cgroup=lambda: "0::/user.slice/little-workshop-deployment.service")
    with pytest.raises(SelfRedeployRequiresDispatch):
        self_aware_flow.rollback_pillar_if_unconfirmed(conn, "deployment", repo, pending["deploy_id"])


def test_dispatch_self_redeploy_shells_out_to_worker(paths, tmp_path):
    runner = FakeRunner()
    flow = make_flow(paths, runner)
    message = flow.dispatch_self_redeploy("deployment", "abc123", confirm_timeout_seconds=600, job_id="job-1")
    assert "dispatched as systemd" in message
    dispatch_calls = [c for c in runner.calls if c and c[0] == "systemd-run"]
    assert len(dispatch_calls) == 1
    argv = dispatch_calls[0]
    assert "redeploy-pillar-worker" in argv
    assert "deployment" in argv
    assert "--commit" in argv and "abc123" in argv
    assert "--job-id" in argv and "job-1" in argv


def test_dispatch_self_rollback_shells_out_to_worker(paths, tmp_path):
    runner = FakeRunner()
    flow = make_flow(paths, runner)
    message = flow.dispatch_self_rollback("deployment", "deploy-1")
    assert "dispatched as systemd" in message
    dispatch_calls = [c for c in runner.calls if c and c[0] == "systemd-run"]
    argv = dispatch_calls[0]
    assert "rollback-pillar-worker" in argv
    assert "--deploy-id" in argv and "deploy-1" in argv


def test_launched_pillar_inherits_the_workshop_repo_override(paths, conn, tmp_path, monkeypatch):
    repo = make_workshop_repo(tmp_path)
    monkeypatch.setenv("LITTLE_WORKSHOP_REPO_DIR", str(repo))
    runner = FakeRunner()
    make_flow(paths, runner).redeploy_pillar_direct(conn, "orchestration", repo, "main")

    launch = next(c for c in runner.calls if c and c[0] == "systemd-run")
    assert f"--setenv=LITTLE_WORKSHOP_REPO_DIR={repo}" in launch


def test_launched_pillar_gets_no_repo_override_when_none_is_set(paths, conn, tmp_path, monkeypatch):
    repo = make_workshop_repo(tmp_path)
    monkeypatch.delenv("LITTLE_WORKSHOP_REPO_DIR", raising=False)
    runner = FakeRunner()
    make_flow(paths, runner).redeploy_pillar_direct(conn, "orchestration", repo, "main")

    launch = next(c for c in runner.calls if c and c[0] == "systemd-run")
    assert not any("LITTLE_WORKSHOP_REPO_DIR" in a for a in launch)


def test_redeploying_the_job_dispatcher_always_dispatches_even_outside_its_cgroup(paths, conn, tmp_path):
    repo = make_workshop_repo(tmp_path, pillar="orchestration")
    flow = make_flow(paths, FakeRunner(), own_cgroup=lambda: "0::/user.slice/little-workshop-deployment.service")
    with pytest.raises(SelfRedeployRequiresDispatch):
        flow.redeploy_pillar(conn, "orchestration", repo, "main")
    with pytest.raises(SelfRedeployRequiresDispatch):
        flow.rollback_pillar_if_unconfirmed(conn, "orchestration", repo, "deploy-id")


# --- REQ-000000351: Redeploying Core restarts the installed Core instead of starting a second one ---


def test_redeploying_an_installed_core_restarts_its_unit_and_arms_no_rollback(paths, conn, tmp_path):
    repo = make_workshop_repo(tmp_path, pillar="core")
    runner = FakeRunner()
    runner.active["little-workshop.service"] = True  # Core runs as the installed unit, as on a real install
    restarts = []
    real_run = runner.run

    def run(argv, timeout=None, cwd=None):
        if argv[:3] == ["systemctl", "--user", "restart"]:
            restarts.append(argv[3])
            return CommandResult(0, "", "")
        return real_run(argv, timeout=timeout, cwd=cwd)

    runner.run = run
    flow = make_flow(paths, runner)
    result = flow.redeploy_pillar(conn, "core", repo, "main")
    assert restarts == ["little-workshop.service"]
    assert result.how == "restarted the installed unit little-workshop.service" and result.confirm_message is None
    assert not any(call[0] == "systemd-run" for call in runner.calls)  # no second, transient Core
    assert not any(call[0] == "pkill" for call in runner.calls)
    assert db.get_confirmation(conn, "core") is None  # nothing a rollback timer could act on


def test_a_core_that_is_not_installed_as_a_unit_is_redeployed_like_any_pillar(paths, conn, tmp_path):
    repo = make_workshop_repo(tmp_path, pillar="core")
    runner = FakeRunner()
    result = make_flow(paths, runner).redeploy_pillar(conn, "core", repo, "main")
    assert "little-workshop-core.service" in result.how and result.confirm_message is not None
