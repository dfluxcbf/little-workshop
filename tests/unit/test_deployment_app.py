import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from common.paths import Paths
from common.projectfiles import ProjectFilesClient
from deployment import db
from deployment.app import build_app
from deployment.flow import DeploymentFlow


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
        if argv and argv[0] in ("pkill", "which", "tailscale"):
            return self._result(0, "")
        if argv and argv[0] == "bazel":
            return self._result(0, "")
        return self._result(0, "")

    @staticmethod
    def _result(code, out):
        from common.shell import CommandResult

        return CommandResult(code, out, "")


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


def make_client(paths, conn, repo):
    ProjectFilesClient(paths, caller_pillar="deployment").write("little-meals", "manifest.deployment.toml", MANIFEST_TOML)

    def fake_resolve(_paths, project_id):
        from common.registry_client import ProjectResolutionError

        if project_id != "little-meals":
            raise ProjectResolutionError(f"unknown project {project_id!r}")
        return repo

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
    app = build_app(paths, conn, resolve=fake_resolve, flow=flow)
    return TestClient(app), runner


def test_health(paths, conn, tmp_path):
    repo = make_repo(tmp_path)
    client, _ = make_client(paths, conn, repo)
    assert client.get("/v1/health").json()["ok"] is True


def test_deploy_and_status(paths, conn, tmp_path):
    repo = make_repo(tmp_path)
    client, _ = make_client(paths, conn, repo)

    response = client.post(
        "/v1/jobs/deployment.deploy", json={"job_id": "j1", "project_id": "little-meals", "params": {"branch": "main"}}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] == "succeeded", body
    assert body["result"]["version"] == "1.0.0"

    status = client.get("/v1/status/little-meals").json()
    assert status["branch"] == "main"
    assert status["unit"] == "active"


def test_deploy_unknown_project_fails_cleanly(paths, conn, tmp_path):
    repo = make_repo(tmp_path)
    client, _ = make_client(paths, conn, repo)
    response = client.post(
        "/v1/jobs/deployment.deploy", json={"job_id": "j1", "project_id": "ghost", "params": {}}
    )
    assert response.json()["outcome"] == "failed"


def test_deploy_missing_manifest_fails_cleanly(paths, conn, tmp_path):
    repo = tmp_path / "no-manifest"
    repo.mkdir()

    def fake_resolve(_paths, project_id):
        return repo

    app = build_app(paths, conn, resolve=fake_resolve)
    client = TestClient(app)
    response = client.post(
        "/v1/jobs/deployment.deploy", json={"job_id": "j1", "project_id": "no-manifest", "params": {}}
    )
    assert response.json()["outcome"] == "failed"
    assert "manifest.deployment.toml" in response.json()["error"]


def test_stop_endpoint(paths, conn, tmp_path):
    repo = make_repo(tmp_path)
    client, _ = make_client(paths, conn, repo)
    client.post("/v1/jobs/deployment.deploy", json={"job_id": "j1", "project_id": "little-meals", "params": {"branch": "main"}})

    response = client.post("/v1/jobs/deployment.stop", json={"job_id": "j2", "project_id": "little-meals", "params": {}})
    assert response.json()["outcome"] == "succeeded"
    assert response.json()["result"]["unit_stopped"] is True


def test_seed_and_confirm_flow(paths, conn, tmp_path):
    repo = make_repo(tmp_path)
    client, _ = make_client(paths, conn, repo)
    client.post("/v1/jobs/deployment.deploy", json={"job_id": "j1", "project_id": "little-meals", "params": {"branch": "main"}})

    seed = client.post("/v1/jobs/deployment.seed_confirmation", json={"job_id": "j2", "project_id": "little-meals", "params": {}})
    assert seed.json()["outcome"] == "succeeded"

    confirmation = client.get("/v1/confirmations/little-meals").json()
    assert confirmation["status"] == "confirmed"


def test_confirm_with_nothing_pending_fails(paths, conn, tmp_path):
    repo = make_repo(tmp_path)
    client, _ = make_client(paths, conn, repo)
    response = client.post("/v1/jobs/deployment.confirm", json={"job_id": "j1", "project_id": "little-meals", "params": {}})
    assert response.json()["outcome"] == "failed"


def test_branches_endpoint(paths, conn, tmp_path):
    repo = make_repo(tmp_path)
    _git(["branch", "dev"], repo)
    client, _ = make_client(paths, conn, repo)
    branches = client.get("/v1/branches/little-meals").json()
    assert set(branches) >= {"main", "dev"}


def test_worktree_provision_and_release(paths, conn, tmp_path):
    repo = make_repo(tmp_path)
    client, _ = make_client(paths, conn, repo)

    provisioned = client.post("/v1/worktrees", json={"project_id": "little-meals", "commitish": "main"})
    assert provisioned.status_code == 200
    body = provisioned.json()
    assert Path(body["path"]).exists()

    released = client.delete(f"/v1/worktrees/{body['worktree_id']}", params={"project_id": "little-meals"})
    assert released.json()["released"] is True
    assert not Path(body["path"]).exists()


def test_deployments_history_endpoint(paths, conn, tmp_path):
    repo = make_repo(tmp_path)
    client, _ = make_client(paths, conn, repo)
    client.post("/v1/jobs/deployment.deploy", json={"job_id": "j1", "project_id": "little-meals", "params": {"branch": "main"}})

    history = client.get("/v1/deployments/little-meals").json()
    assert len(history) == 1
    assert history[0]["outcome"] == "succeeded"


def _init_workshop_repo(tmp_path, pillar="orchestration"):
    repo = tmp_path / "workshop-repo"
    (repo / "src" / pillar).mkdir(parents=True)
    (repo / "src" / pillar / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
    (repo / "VERSION").write_text("1.0.0\n", encoding="utf-8")
    _git(["init", "-b", "main"], repo)
    _git(["config", "user.email", "t@example.com"], repo)
    _git(["config", "user.name", "T"], repo)
    _git(["add", "-A"], repo)
    _git(["commit", "-m", "first"], repo)
    return repo


class PassthroughFakeRunner(FakeRunner):
    """Extends the module's FakeRunner with real git/python passthrough,
    needed for a genuine self-redeploy smoke-check."""

    def run(self, argv, timeout=None, cwd=None):
        if argv and argv[0] in ("git", "python3"):
            result = subprocess.run(argv, cwd=str(cwd) if cwd else None, capture_output=True, text=True, timeout=timeout)
            from common.shell import CommandResult

            return CommandResult(result.returncode, result.stdout, result.stderr)
        return super().run(argv, timeout=timeout, cwd=cwd)


def test_redeploy_pillar_job_succeeds_inline_for_a_non_self_target(paths, conn, tmp_path, monkeypatch):
    from deployment.app import build_app
    from deployment.flow import DeploymentFlow

    workshop_repo = _init_workshop_repo(tmp_path, pillar="orchestration")
    monkeypatch.setenv("LITTLE_WORKSHOP_REPO_DIR", str(workshop_repo))

    runner = PassthroughFakeRunner()
    flow = DeploymentFlow(paths=paths, runner=runner, sleep=lambda s: None, health_probe=lambda *a, **k: __import__("core.health", fromlist=["HealthResult"]).HealthResult(ok=True, attempts=1))
    app = build_app(paths, conn, flow=flow)
    client = TestClient(app)

    response = client.post(
        "/v1/jobs/deployment.redeploy_pillar",
        json={"job_id": "j1", "params": {"pillar": "orchestration", "commit": "main"}},
    )
    body = response.json()
    assert body["outcome"] == "succeeded", body
    assert body["result"]["version"] == "1.0.0"


def test_redeploy_pillar_job_dispatches_when_self_targeting(paths, conn, tmp_path, monkeypatch):
    from deployment.app import build_app
    from deployment.flow import DeploymentFlow

    workshop_repo = _init_workshop_repo(tmp_path, pillar="deployment")
    monkeypatch.setenv("LITTLE_WORKSHOP_REPO_DIR", str(workshop_repo))

    runner = PassthroughFakeRunner()
    flow = DeploymentFlow(
        paths=paths, runner=runner, sleep=lambda s: None,
        own_cgroup=lambda: "0::/user.slice/little-workshop-deployment.service",
    )
    app = build_app(paths, conn, flow=flow)
    client = TestClient(app)

    response = client.post(
        "/v1/jobs/deployment.redeploy_pillar",
        json={"job_id": "j1", "params": {"pillar": "deployment", "commit": "main"}},
    )
    body = response.json()
    assert body["outcome"] == "accepted", body
    assert "dispatched" in body["result"]["dispatched"]
    dispatch_calls = [c for c in runner.calls if c and c[0] == "systemd-run"]
    assert any("redeploy-pillar-worker" in c for c in dispatch_calls)


# -- deployment.set_manifest (M17 ecosystem-cutover finding: nothing --------
# exposed a way to write a project's own manifest.deployment.toml before)


VALID_MANIFEST_TOML = """
[server]
name = "little-gadget"
command = "run.sh"
port = 9000
match = "run.sh"
"""


def test_set_manifest_writes_a_valid_manifest(paths, conn):
    app = build_app(paths, conn)
    client = TestClient(app)
    response = client.post(
        "/v1/jobs/deployment.set_manifest",
        json={"job_id": "j1", "project_id": "little-gadget", "params": {"toml": VALID_MANIFEST_TOML}},
    )
    body = response.json()
    assert body["outcome"] == "succeeded", body
    assert body["result"] == {"name": "little-gadget", "port": 9000}

    stored = ProjectFilesClient(paths, caller_pillar="deployment").read("little-gadget", "manifest.deployment.toml")
    assert stored == VALID_MANIFEST_TOML

    fetched = client.get("/v1/manifest/little-gadget")
    assert fetched.json()["name"] == "little-gadget"
    assert fetched.json()["port"] == 9000


def test_set_manifest_rejects_invalid_toml(paths, conn):
    app = build_app(paths, conn)
    client = TestClient(app)
    response = client.post(
        "/v1/jobs/deployment.set_manifest",
        json={"job_id": "j1", "project_id": "little-gadget", "params": {"toml": "not valid toml ["}},
    )
    body = response.json()
    assert body["outcome"] == "failed"
    assert "invalid manifest" in body["error"]


def test_set_manifest_rejects_missing_required_field(paths, conn):
    app = build_app(paths, conn)
    client = TestClient(app)
    response = client.post(
        "/v1/jobs/deployment.set_manifest",
        json={"job_id": "j1", "project_id": "little-gadget", "params": {"toml": "[server]\nname = \"x\"\n"}},
    )
    body = response.json()
    assert body["outcome"] == "failed"
    assert "invalid manifest" in body["error"]


def test_set_manifest_missing_project_id_fails_cleanly(paths, conn):
    app = build_app(paths, conn)
    client = TestClient(app)
    response = client.post("/v1/jobs/deployment.set_manifest", json={"job_id": "j1", "params": {"toml": VALID_MANIFEST_TOML}})
    assert response.json()["outcome"] == "failed"


def test_set_manifest_missing_toml_fails_cleanly(paths, conn):
    app = build_app(paths, conn)
    client = TestClient(app)
    response = client.post(
        "/v1/jobs/deployment.set_manifest", json={"job_id": "j1", "project_id": "little-gadget", "params": {}},
    )
    assert response.json()["outcome"] == "failed"


def test_get_manifest_404_when_not_set(paths, conn):
    app = build_app(paths, conn)
    client = TestClient(app)
    response = client.get("/v1/manifest/never-configured")
    assert response.status_code == 404
