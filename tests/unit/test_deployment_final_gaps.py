"""The last handful of small, specific line-coverage gaps left after
test_deployment_app_more.py / test_deployment_flow_more.py: a couple of
single branches each, not worth their own themed file."""

import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from common.paths import Paths
from common.projectfiles import ProjectFilesClient
from common.shell import CommandResult
from core.health import HealthResult
from deployment import db
from deployment.app import build_app
from deployment.flow import DeploymentError, DeploymentFlow
from deployment.manifest import ServerManifest, TailscaleManifest, ScreenshotManifest, render_manifest


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "little-meals"
    repo.mkdir()
    _git(["init", "-b", "main"], repo)
    _git(["config", "user.email", "t@example.com"], repo)
    _git(["config", "user.name", "T"], repo)
    (repo / "VERSION").write_text("1.0.0\n", encoding="utf-8")
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


# -- deployment/db.py: list_server_states -------------------------------


def test_list_server_states_returns_every_cached_server(conn):
    db.save_server_state(conn, "little-meals", "dev", "1.0.0", "abc")
    db.save_server_state(conn, "little-widget", "main", "2.0.0", "def")
    states = db.list_server_states(conn)
    assert {s["project_id"] for s in states} == {"little-meals", "little-widget"}


# -- deployment/manifest.py: unit_name, env/screenshot-port rendering -----


def test_unit_name_property():
    m = ServerManifest(name="little-meals", command="run.sh", port=1, match="run.sh")
    assert m.unit_name == "little-workshop-deploy-little-meals"


def test_render_manifest_includes_env_block():
    m = ServerManifest(name="x", command="run.sh", port=1, match="run.sh", env={"FOO": "bar"})
    rendered = render_manifest(m)
    assert "[server.env]" in rendered
    assert 'FOO = "bar"' in rendered


def test_render_manifest_includes_screenshot_port_when_set():
    m = ServerManifest(
        name="x", command="run.sh", port=1, match="run.sh",
        screenshot=ScreenshotManifest(port=9999),
    )
    rendered = render_manifest(m)
    assert "port = 9999" in rendered


# -- deployment/app.py: job_deploy confirm_message + job_confirm nothing pending --


class FakeRunner:
    def __init__(self):
        self.active: dict[str, bool] = {}

    def run(self, argv, timeout=None, cwd=None):
        if argv[:3] == ["systemctl", "--user", "is-active"]:
            return CommandResult(0, "active\n" if self.active.get(argv[3]) else "inactive\n", "")
        if argv and argv[0] == "systemd-run":
            unit_arg = next(a for a in argv if a.startswith("--unit="))
            self.active[unit_arg.removeprefix("--unit=") + ".service"] = True
            return CommandResult(0, "", "")
        if argv[:3] == ["systemctl", "--user", "stop"]:
            self.active[argv[3]] = False
            return CommandResult(0, "", "")
        return CommandResult(0, "", "")


def test_job_deploy_reports_confirm_message_when_gated(paths, conn, tmp_path):
    repo = make_repo(tmp_path)
    manifest_toml = """
[server]
name = "little-meals"
command = "run.sh"
port = 8765
match = "run.sh"
confirm_timeout_seconds = 600
"""
    ProjectFilesClient(paths, caller_pillar="deployment").write("little-meals", "manifest.deployment.toml", manifest_toml)

    def fake_resolve(_paths, project_id):
        return repo

    runner = FakeRunner()
    launched = {"v": False}
    real_run = runner.run

    def run_and_mark(argv, timeout=None, cwd=None):
        result = real_run(argv, timeout=timeout, cwd=cwd)
        if argv and argv[0] == "systemd-run":
            launched["v"] = True
        return result

    runner.run = run_and_mark
    flow = DeploymentFlow(paths=paths, runner=runner, sleep=lambda s: None, probe=lambda p: launched["v"])
    app = build_app(paths, conn, resolve=fake_resolve, flow=flow)
    client = TestClient(app)

    response = client.post(
        "/v1/jobs/deployment.deploy", json={"job_id": "j1", "project_id": "little-meals", "params": {"branch": "main"}}
    )
    body = response.json()
    assert body["outcome"] == "succeeded", body
    assert "confirm_message" in body["result"]


def test_job_confirm_nothing_pending_via_http(paths, conn, tmp_path):
    repo = make_repo(tmp_path)

    def fake_resolve(_paths, project_id):
        return repo

    app = build_app(paths, conn, resolve=fake_resolve)
    client = TestClient(app)
    response = client.post("/v1/jobs/deployment.confirm", json={"job_id": "j1", "project_id": "little-meals", "params": {}})
    body = response.json()
    assert body == {"outcome": "failed", "error": "nothing pending to confirm"}


# -- deployment/flow.py: arm_confirm_gate failure, rollback exposure, idempotent redeploy, real multi-poll wait --


def test_arm_confirm_gate_raises_when_watchdog_dispatch_fails(paths, conn, tmp_path):
    repo = make_repo(tmp_path)

    class FailingWatchdogRunner(FakeRunner):
        def run(self, argv, timeout=None, cwd=None):
            if argv and argv[0] == "systemd-run" and "rollback-timeout" in argv:
                return CommandResult(1, "", "no dbus")
            return super().run(argv, timeout=timeout, cwd=cwd)

    runner = FailingWatchdogRunner()
    launched = {"v": False}
    real_run = runner.run

    def run_and_mark(argv, timeout=None, cwd=None):
        result = real_run(argv, timeout=timeout, cwd=cwd)
        if argv and argv[0] == "systemd-run" and "rollback-timeout" not in argv:
            launched["v"] = True
        return result

    runner.run = run_and_mark
    flow = DeploymentFlow(paths=paths, runner=runner, sleep=lambda s: None, probe=lambda p: launched["v"])
    manifest = ServerManifest(name="little-meals", command="run.sh", port=8765, match="run.sh", confirm_timeout_seconds=600)

    with pytest.raises(DeploymentError, match="could not arm the confirm-gate watchdog"):
        flow.up(conn, "little-meals", manifest, repo, "main")


def test_rollback_if_unconfirmed_includes_exposure_in_summary(paths, conn, tmp_path):
    repo = make_repo(tmp_path)
    runner = FakeRunner()
    launched = {"v": False}
    real_run = runner.run

    def run_and_mark(argv, timeout=None, cwd=None):
        result = real_run(argv, timeout=timeout, cwd=cwd)
        if argv and argv[0] == "systemd-run":
            launched["v"] = True
        if argv[:3] == ["systemctl", "--user", "stop"]:
            launched["v"] = False
        return result

    runner.run = run_and_mark
    flow = DeploymentFlow(paths=paths, runner=runner, sleep=lambda s: None, probe=lambda p: launched["v"])
    manifest = ServerManifest(
        name="little-meals", command="run.sh", port=8765, match="run.sh",
        tailscale=TailscaleManifest(https_port=443),
    )

    flow.up(conn, "little-meals", manifest, repo, "main")
    flow.seed_confirmation(conn, "little-meals")
    flow.up(conn, "little-meals", ServerManifest(
        name="little-meals", command="run.sh", port=8765, match="run.sh", confirm_timeout_seconds=600,
        tailscale=TailscaleManifest(https_port=443),
    ), repo, "main")
    pending = db.get_confirmation(conn, "little-meals")

    summary = flow.rollback_if_unconfirmed(conn, "little-meals", manifest, repo, pending["deploy_id"])
    assert "tailscale serve" in summary


def test_redeploy_pillar_direct_is_idempotent_for_the_same_commit(paths, conn, tmp_path):
    repo = make_workshop_repo(tmp_path)
    runner = FakeRunner()

    def run_passthrough(argv, timeout=None, cwd=None):
        if argv and argv[0] in ("git", "python3"):
            result = subprocess.run(argv, cwd=str(cwd) if cwd else None, capture_output=True, text=True, timeout=timeout)
            return CommandResult(result.returncode, result.stdout, result.stderr)
        return FakeRunner.run(runner, argv, timeout=timeout, cwd=cwd)

    runner.run = run_passthrough
    flow = DeploymentFlow(paths=paths, runner=runner, sleep=lambda s: None, health_probe=lambda *a, **k: HealthResult(ok=True, attempts=1))

    first = flow.redeploy_pillar_direct(conn, "orchestration", repo, "main")
    # Redeploying the exact same resolved commit a second time must not
    # choke on an install_dir that already exists from the first pass.
    second = flow.redeploy_pillar_direct(conn, "orchestration", repo, "main")
    assert first.commit == second.commit


def test_wait_polls_multiple_times_before_succeeding(paths):
    sleeps = {"n": 0}
    calls = {"n": 0}

    def predicate():
        calls["n"] += 1
        return calls["n"] >= 3

    flow = DeploymentFlow(paths=paths, sleep=lambda s: sleeps.__setitem__("n", sleeps["n"] + 1))
    assert flow._wait(predicate, timeout=10, interval=0.01) is True
    assert sleeps["n"] >= 2
