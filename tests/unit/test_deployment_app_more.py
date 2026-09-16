"""Additional deployment/app.py endpoint-routing coverage: missing-field
guards, 404 branches, and the previously entirely-untested
`/v1/rollback-timeout/{project_id}` endpoint (every branch: guard-blocked,
non-pillar success, pillar success, pillar-dispatch, and the 500 path).

A stub Flow is used for the rollback-timeout tests specifically -- real
DeploymentFlow's own behavior for rollback/redeploy is already exercised
for real (with real git worktrees) in test_deployment_flow.py and
test_deployment_self_redeploy.py; here the point is app.py's own request
routing and error translation, which a duck-typed stub isolates cleanly
without re-deriving a full real worktree/systemd fixture per branch.
"""

import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from common.paths import Paths
from common.projectfiles import ProjectFilesClient
from deployment import db
from deployment.app import build_app
from deployment.flow import DeploymentError, DeploymentFlow, SelfRedeployRequiresDispatch


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


MANIFEST_TOML = """
[server]
name = "little-meals"
command = "run.sh"
port = 8765
match = "run.sh"
"""

MANIFEST_WITH_TAILSCALE_TOML = """
[server]
name = "little-meals"
command = "run.sh"
port = 8765
match = "run.sh"

[server.tailscale]
https_port = 443
"""


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


def fake_resolve_factory(repo, only="little-meals"):
    def fake_resolve(_paths, project_id):
        from common.registry_client import ProjectResolutionError

        if project_id != only:
            raise ProjectResolutionError(f"unknown project {project_id!r}")
        return repo

    return fake_resolve


class FakeRunner:
    def __init__(self):
        self.active: dict[str, bool] = {}
        self.calls: list[list[str]] = []

    def run(self, argv, timeout=None, cwd=None):
        self.calls.append(argv)
        if argv[:3] == ["systemctl", "--user", "is-active"]:
            return self._result(0, "active\n" if self.active.get(argv[3]) else "inactive\n")
        if argv and argv[0] == "systemd-run":
            unit_arg = next(a for a in argv if a.startswith("--unit="))
            self.active[unit_arg.removeprefix("--unit=") + ".service"] = True
            return self._result(0, "")
        if argv[:3] == ["systemctl", "--user", "stop"]:
            self.active[argv[3]] = False
            return self._result(0, "")
        if argv[:3] == ["systemctl", "--user", "reset-failed"]:
            return self._result(0, "")
        if argv[:2] == ["pgrep", "-A"]:
            return self._result(1, "")
        if argv and argv[0] in ("pkill", "which"):
            return self._result(0, "")
        if argv and argv[0] == "tailscale":
            return self._result(0, "")
        if argv and argv[0] == "bazel":
            return self._result(0, "")
        return self._result(0, "")

    @staticmethod
    def _result(code, out):
        from common.shell import CommandResult

        return CommandResult(code, out, "")


def make_real_client(paths, conn, repo, manifest_toml=MANIFEST_TOML, project="little-meals"):
    ProjectFilesClient(paths, caller_pillar="deployment").write(project, "manifest.deployment.toml", manifest_toml)
    runner = FakeRunner()
    launched = {"v": False}

    def probe(port):
        return launched["v"]

    real_run = runner.run

    def run_and_mark(argv, timeout=None, cwd=None):
        result = real_run(argv, timeout=timeout, cwd=cwd)
        if argv and argv[0] == "systemd-run":
            launched["v"] = True
        if argv[:3] == ["systemctl", "--user", "stop"]:
            launched["v"] = False
        return result

    runner.run = run_and_mark
    flow = DeploymentFlow(paths=paths, runner=runner, sleep=lambda s: None, probe=probe)
    app = build_app(paths, conn, resolve=fake_resolve_factory(repo, project), flow=flow)
    return TestClient(app), runner, flow


# -- missing-field guards on every Job endpoint --------------------------


def test_job_deploy_missing_project_id(paths, conn):
    app = build_app(paths, conn)
    client = TestClient(app)
    response = client.post("/v1/jobs/deployment.deploy", json={"job_id": "j1", "params": {}})
    assert response.json() == {"outcome": "failed", "error": "missing project_id"}


def test_job_stop_missing_project_id(paths, conn):
    app = build_app(paths, conn)
    client = TestClient(app)
    response = client.post("/v1/jobs/deployment.stop", json={"job_id": "j1", "params": {}})
    assert response.json() == {"outcome": "failed", "error": "missing project_id"}


def test_job_stop_deployment_error_is_reported_cleanly(paths, conn):
    app = build_app(paths, conn)
    client = TestClient(app)
    response = client.post("/v1/jobs/deployment.stop", json={"job_id": "j1", "project_id": "no-manifest", "params": {}})
    body = response.json()
    assert body["outcome"] == "failed"
    assert "manifest.deployment.toml" in body["error"]


def test_job_seed_confirmation_missing_project_id(paths, conn):
    app = build_app(paths, conn)
    client = TestClient(app)
    response = client.post("/v1/jobs/deployment.seed_confirmation", json={"job_id": "j1", "params": {}})
    assert response.json() == {"outcome": "failed", "error": "missing project_id"}


def test_job_seed_confirmation_fails_without_a_prior_deploy(paths, conn, tmp_path):
    repo = make_repo(tmp_path)
    client, _runner, _flow = make_real_client(paths, conn, repo)
    response = client.post(
        "/v1/jobs/deployment.seed_confirmation", json={"job_id": "j1", "project_id": "little-meals", "params": {}}
    )
    body = response.json()
    assert body["outcome"] == "failed"
    assert "nothing has been deployed yet" in body["error"]


def test_job_redeploy_pillar_missing_pillar_param(paths, conn):
    app = build_app(paths, conn)
    client = TestClient(app)
    response = client.post("/v1/jobs/deployment.redeploy_pillar", json={"job_id": "j1", "params": {}})
    assert response.json() == {"outcome": "failed", "error": "missing params.pillar"}


def test_job_redeploy_pillar_deployment_error_records_a_failed_deployment(paths, conn, tmp_path, monkeypatch):
    workshop_repo = tmp_path / "workshop"
    (workshop_repo / "src" / "orchestration").mkdir(parents=True)
    (workshop_repo / "src" / "orchestration" / "__init__.py").write_text("this is not valid python (((\n", encoding="utf-8")
    (workshop_repo / "VERSION").write_text("1.0.0\n", encoding="utf-8")
    _git(["init", "-b", "main"], workshop_repo)
    _git(["config", "user.email", "t@example.com"], workshop_repo)
    _git(["config", "user.name", "T"], workshop_repo)
    _git(["add", "-A"], workshop_repo)
    _git(["commit", "-m", "first"], workshop_repo)
    monkeypatch.setenv("LITTLE_WORKSHOP_REPO_DIR", str(workshop_repo))

    class PassthroughRunner(FakeRunner):
        def run(self, argv, timeout=None, cwd=None):
            if argv and argv[0] in ("git", "python3"):
                result = subprocess.run(argv, cwd=str(cwd) if cwd else None, capture_output=True, text=True, timeout=timeout)
                from common.shell import CommandResult

                return CommandResult(result.returncode, result.stdout, result.stderr)
            return super().run(argv, timeout=timeout, cwd=cwd)

    flow = DeploymentFlow(paths=paths, runner=PassthroughRunner(), sleep=lambda s: None)
    app = build_app(paths, conn, flow=flow)
    client = TestClient(app)

    response = client.post(
        "/v1/jobs/deployment.redeploy_pillar", json={"job_id": "j1", "params": {"pillar": "orchestration", "commit": "HEAD"}}
    )
    body = response.json()
    assert body["outcome"] == "failed"
    history = db.list_deployments(conn, "orchestration")
    assert history[0]["outcome"] == "failed"


def test_job_confirm_missing_project_id(paths, conn):
    app = build_app(paths, conn)
    client = TestClient(app)
    response = client.post("/v1/jobs/deployment.confirm", json={"job_id": "j1", "params": {}})
    assert response.json() == {"outcome": "failed", "error": "missing project_id"}


def test_load_manifest_reports_invalid_content_cleanly(paths, conn, tmp_path):
    repo = make_repo(tmp_path)
    client, _runner, _flow = make_real_client(paths, conn, repo, manifest_toml="not even toml [[[")
    response = client.post(
        "/v1/jobs/deployment.deploy", json={"job_id": "j1", "project_id": "little-meals", "params": {"branch": "main"}}
    )
    body = response.json()
    assert body["outcome"] == "failed"
    assert "invalid manifest.deployment.toml" in body["error"]


def test_job_deploy_reports_exposure_when_tailscale_configured(paths, conn, tmp_path):
    repo = make_repo(tmp_path)
    client, _runner, _flow = make_real_client(paths, conn, repo, manifest_toml=MANIFEST_WITH_TAILSCALE_TOML)
    response = client.post(
        "/v1/jobs/deployment.deploy", json={"job_id": "j1", "project_id": "little-meals", "params": {"branch": "main"}}
    )
    body = response.json()
    assert body["outcome"] == "succeeded", body
    assert "exposure" in body["result"]


def test_job_redeploy_pillar_reports_confirm_message_for_a_gated_pillar(paths, conn, tmp_path, monkeypatch):
    workshop_repo = tmp_path / "workshop"
    (workshop_repo / "src" / "core").mkdir(parents=True)
    (workshop_repo / "src" / "core" / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
    (workshop_repo / "VERSION").write_text("1.0.0\n", encoding="utf-8")
    _git(["init", "-b", "main"], workshop_repo)
    _git(["config", "user.email", "t@example.com"], workshop_repo)
    _git(["config", "user.name", "T"], workshop_repo)
    _git(["add", "-A"], workshop_repo)
    _git(["commit", "-m", "first"], workshop_repo)
    monkeypatch.setenv("LITTLE_WORKSHOP_REPO_DIR", str(workshop_repo))

    class PassthroughRunner(FakeRunner):
        def run(self, argv, timeout=None, cwd=None):
            if argv and argv[0] in ("git", "python3"):
                result = subprocess.run(argv, cwd=str(cwd) if cwd else None, capture_output=True, text=True, timeout=timeout)
                from common.shell import CommandResult

                return CommandResult(result.returncode, result.stdout, result.stderr)
            return super().run(argv, timeout=timeout, cwd=cwd)

    from core.health import HealthResult

    flow = DeploymentFlow(
        paths=paths, runner=PassthroughRunner(), sleep=lambda s: None,
        health_probe=lambda *a, **k: HealthResult(ok=True, attempts=1),
    )
    app = build_app(paths, conn, flow=flow)
    client = TestClient(app)

    response = client.post(
        "/v1/jobs/deployment.redeploy_pillar", json={"job_id": "j1", "params": {"pillar": "core", "commit": "main"}}
    )
    body = response.json()
    assert body["outcome"] == "succeeded", body
    assert "confirm_message" in body["result"]


# -- 404 branches ----------------------------------------------------------


def test_status_404s_when_manifest_missing(paths, conn):
    app = build_app(paths, conn)
    client = TestClient(app)
    response = client.get("/v1/status/no-manifest")
    assert response.status_code == 404


def test_confirmation_404s_when_no_state_recorded(paths, conn, tmp_path):
    repo = make_repo(tmp_path)
    client, _runner, _flow = make_real_client(paths, conn, repo)
    response = client.get("/v1/confirmations/little-meals")
    assert response.status_code == 404


def test_branches_404s_for_unresolved_project(paths, conn):
    app = build_app(paths, conn)
    client = TestClient(app)
    response = client.get("/v1/branches/ghost")
    assert response.status_code == 404


def test_worktree_provision_404s_for_unresolved_project(paths, conn):
    app = build_app(paths, conn)
    client = TestClient(app)
    response = client.post("/v1/worktrees", json={"project_id": "ghost", "commitish": "HEAD"})
    assert response.status_code == 404


def test_worktree_release_404s_for_unresolved_project(paths, conn):
    app = build_app(paths, conn)
    client = TestClient(app)
    response = client.delete("/v1/worktrees/some-id", params={"project_id": "ghost"})
    assert response.status_code == 404


# -- /v1/rollback-timeout/{project_id}: every branch ------------------------


class StubFlow:
    """Duck-typed stand-in for DeploymentFlow, isolating app.py's own
    routing/error-translation logic for the rollback-timeout endpoint."""

    def __init__(self, *, rollback_result=None, rollback_error=None, raise_dispatch=False, dispatch_message=""):
        self._rollback_result = rollback_result
        self._rollback_error = rollback_error
        self._raise_dispatch = raise_dispatch
        self._dispatch_message = dispatch_message
        self.calls: list[str] = []

    def rollback_if_unconfirmed(self, conn, project_id, manifest, project_dir, deploy_id):
        self.calls.append("rollback_if_unconfirmed")
        if self._rollback_error:
            raise self._rollback_error
        return self._rollback_result or "rolled back (non-pillar)"

    def rollback_pillar_if_unconfirmed(self, conn, pillar, workshop_repo_dir, deploy_id):
        self.calls.append("rollback_pillar_if_unconfirmed")
        if self._raise_dispatch:
            raise SelfRedeployRequiresDispatch(pillar)
        if self._rollback_error:
            raise self._rollback_error
        return self._rollback_result or "rolled back (pillar)"

    def dispatch_self_rollback(self, pillar, deploy_id):
        self.calls.append("dispatch_self_rollback")
        return self._dispatch_message or f"dispatched rollback for {pillar}"


def test_rollback_timeout_guard_blocks_when_no_state(paths, conn):
    app = build_app(paths, conn, flow=StubFlow())
    client = TestClient(app)
    response = client.post("/v1/rollback-timeout/little-meals", json={"deploy_id": "d1"})
    body = response.json()
    assert body["rolled_back"] is False
    assert "no confirm-gate state found" in body["message"]


def test_rollback_timeout_non_pillar_path_succeeds(paths, conn, tmp_path):
    repo = make_repo(tmp_path)
    ProjectFilesClient(paths, caller_pillar="deployment").write("little-meals", "manifest.deployment.toml", MANIFEST_TOML)
    db.seed_confirmation(conn, "little-meals", "dev", "abc", "1.0.0")
    pending = db.mark_pending(conn, "little-meals", "dev", "def", "1.0.1", timeout_seconds=600)
    app = build_app(paths, conn, resolve=fake_resolve_factory(repo), flow=StubFlow(rollback_result="rolled back to dev@abc"))
    client = TestClient(app)
    response = client.post("/v1/rollback-timeout/little-meals", json={"deploy_id": pending["deploy_id"]})
    body = response.json()
    assert body["rolled_back"] is True
    assert body["message"] == "rolled back to dev@abc"


def test_rollback_timeout_pillar_path_succeeds(paths, conn):
    db.save_pillar_deployment(conn, "core", "/tmp/core-abc", "abc")
    db.seed_confirmation(conn, "core", "main", "abc", "1.0.0")
    pending = db.mark_pending(conn, "core", "main", "def", "1.0.1", timeout_seconds=600)
    stub = StubFlow(rollback_result="core rolled back")
    app = build_app(paths, conn, flow=stub)
    client = TestClient(app)
    response = client.post("/v1/rollback-timeout/core", json={"deploy_id": pending["deploy_id"]})
    body = response.json()
    assert body["rolled_back"] is True
    assert body["message"] == "core rolled back"
    assert "rollback_pillar_if_unconfirmed" in stub.calls


def test_rollback_timeout_pillar_path_dispatches_when_self_targeting(paths, conn):
    db.save_pillar_deployment(conn, "deployment", "/tmp/deployment-abc", "abc")
    db.seed_confirmation(conn, "deployment", "main", "abc", "1.0.0")
    pending = db.mark_pending(conn, "deployment", "main", "def", "1.0.1", timeout_seconds=600)
    stub = StubFlow(raise_dispatch=True, dispatch_message="dispatched as systemd --user unit x")
    app = build_app(paths, conn, flow=stub)
    client = TestClient(app)
    response = client.post("/v1/rollback-timeout/deployment", json={"deploy_id": pending["deploy_id"]})
    body = response.json()
    assert body["rolled_back"] is False
    assert "dispatched: dispatched as systemd" in body["message"]


def test_rollback_timeout_deployment_error_is_a_500(paths, conn):
    db.seed_confirmation(conn, "little-meals", "dev", "abc", "1.0.0")
    pending = db.mark_pending(conn, "little-meals", "dev", "def", "1.0.1", timeout_seconds=600)
    stub = StubFlow(rollback_error=DeploymentError("boom"))
    app = build_app(paths, conn, flow=stub)
    client = TestClient(app)
    response = client.post("/v1/rollback-timeout/little-meals", json={"deploy_id": pending["deploy_id"]})
    assert response.status_code == 500
