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


def _init_workshop_repo(tmp_path, pillar="policy"):
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

    workshop_repo = _init_workshop_repo(tmp_path, pillar="policy")
    monkeypatch.setenv("LITTLE_WORKSHOP_REPO_DIR", str(workshop_repo))

    runner = PassthroughFakeRunner()
    flow = DeploymentFlow(paths=paths, runner=runner, sleep=lambda s: None, health_probe=lambda *a, **k: __import__("core.health", fromlist=["HealthResult"]).HealthResult(ok=True, attempts=1))
    app = build_app(paths, conn, flow=flow)
    client = TestClient(app)

    response = client.post(
        "/v1/jobs/deployment.redeploy_pillar",
        json={"job_id": "j1", "params": {"pillar": "policy", "commit": "main"}},
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


def test_pillars_endpoint_reports_deployed_sha_and_repo_head(paths, conn, tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    monkeypatch.setenv("LITTLE_WORKSHOP_REPO_DIR", str(repo))
    client, _ = make_client(paths, conn, repo)
    assert client.get("/v1/pillars").json()["pillars"] == {}

    db.save_pillar_deployment(conn, "policy", str(tmp_path / "install"), "abc123")
    body = client.get("/v1/pillars").json()
    assert body["pillars"]["policy"]["commit_sha"] == "abc123"
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()
    assert body["repo_head"] == head


def test_pillars_endpoint_lists_recent_commits_with_their_versions(paths, conn, tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    (repo / "VERSION").write_text("1.1.0\n", encoding="utf-8")
    _git(["commit", "-am", "bump"], repo)
    (repo / "run.sh").write_text("#!/bin/sh\necho\n", encoding="utf-8")
    _git(["rm", "--cached", "VERSION"], repo)
    _git(["commit", "-am", "no version here"], repo)
    monkeypatch.setenv("LITTLE_WORKSHOP_REPO_DIR", str(repo))
    client, _ = make_client(paths, conn, repo)

    commits = client.get("/v1/pillars").json()["commits"]
    assert [c["subject"] for c in commits] == ["no version here", "bump", "first"]
    assert [c["version"] for c in commits] == [None, "1.1.0", "1.0.0"]
    assert all(len(c["sha"]) == 40 for c in commits)


def test_policy_script_locate_returns_a_github_url_for_a_committed_script(paths, conn, tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    (repo / "tools" / "policy_scripts").mkdir(parents=True)
    (repo / "tools" / "policy_scripts" / "x.py").write_text("print('x')\n", encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-m", "add script"], repo)
    _git(["remote", "add", "origin", "git@github.com:dfluxcbf/little-workshop.git"], repo)
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()
    monkeypatch.setenv("LITTLE_WORKSHOP_REPO_DIR", str(repo))
    client, _ = make_client(paths, conn, repo)

    hit = client.post("/v1/policy-scripts/locate", json={"script": "print('x')\n"}).json()
    assert hit["found"] is True and hit["version"] == "1.0.0"
    assert hit["url"] == f"https://github.com/dfluxcbf/little-workshop/blob/{commit}/tools/policy_scripts/x.py"

    assert client.post("/v1/policy-scripts/locate", json={"script": "something else\n"}).json() == {"found": False}


def test_policy_script_locate_is_not_found_without_a_github_remote(paths, conn, tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    (repo / "tools" / "policy_scripts").mkdir(parents=True)
    (repo / "tools" / "policy_scripts" / "x.py").write_text("x\n", encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-m", "add"], repo)
    monkeypatch.setenv("LITTLE_WORKSHOP_REPO_DIR", str(repo))
    client, _ = make_client(paths, conn, repo)
    assert client.post("/v1/policy-scripts/locate", json={"script": "x\n"}).json() == {"found": False}


# -- externally managed servers --------------------------------------------------------------

EXTERNAL_TOML = """
[server]
name = "little-workshop"
command = "python3 bridge.py"
port = 8790
match = "dashboard_bridge.py"
external = true
unit = "little-workshop-dashboard-bridge.service"
"""


def _external_client(paths, conn, tmp_path):
    ProjectFilesClient(paths, caller_pillar="deployment").write("little-workshop", "manifest.deployment.toml", EXTERNAL_TOML)
    runner = FakeRunner()
    runner.active_bridge = True

    def is_active_aware(argv, timeout=None, cwd=None):
        if argv[:3] == ["systemctl", "--user", "is-active"] and argv[3] == "little-workshop-dashboard-bridge.service":
            from common.shell import CommandResult
            return CommandResult(0, "active\n", "")
        return FakeRunner.run(runner, argv, timeout=timeout, cwd=cwd)

    runner.run = is_active_aware
    flow = DeploymentFlow(paths=paths, runner=runner, sleep=lambda s: None, probe=lambda port: port == 8790)
    return TestClient(build_app(paths, conn, resolve=lambda p, project_id: tmp_path, flow=flow)), runner


def test_status_of_an_external_server_says_so_and_names_its_unit(paths, conn, tmp_path):
    client, _ = _external_client(paths, conn, tmp_path)
    status = client.get("/v1/status/little-workshop").json()
    assert status["external"] is True and status["managed_unit"] == "little-workshop-dashboard-bridge.service"
    assert status["unit"] == "active" and status["listening"] is True


def test_status_of_an_ordinary_server_is_not_external(paths, conn, tmp_path):
    repo = make_repo(tmp_path)
    client, _ = make_client(paths, conn, repo)
    status = client.get("/v1/status/little-meals").json()
    assert status["external"] is False and status["managed_unit"] is None


def test_deploy_and_stop_jobs_for_an_external_server_fail_cleanly_and_touch_nothing(paths, conn, tmp_path):
    client, runner = _external_client(paths, conn, tmp_path)
    before = len(runner.calls)
    deploy = client.post("/v1/jobs/deployment.deploy", json={"job_id": "j1", "project_id": "little-workshop", "params": {"branch": "dev"}}).json()
    stop = client.post("/v1/jobs/deployment.stop", json={"job_id": "j2", "project_id": "little-workshop", "params": {}}).json()
    assert deploy["outcome"] == "failed" and "managed outside Deployment" in deploy["error"] and "will not deploy" in deploy["error"]
    assert stop["outcome"] == "failed" and "will not stop" in stop["error"]
    assert not any(c and (c[0] in ("pkill", "systemd-run", "bazel") or c[:3] == ["systemctl", "--user", "stop"]) for c in runner.calls[before:])


def test_status_and_history_answer_while_a_deploy_is_in_progress(paths, conn, tmp_path):
    """A deploy holds the pillar's lock for minutes; the Dashboard's server
    panel reads must not wait for it (they used to time out mid-launch)."""
    import threading
    import time

    repo = make_repo(tmp_path)
    client, runner = make_client(paths, conn, repo)
    client.post("/v1/jobs/deployment.deploy", json={"job_id": "j0", "project_id": "little-meals", "params": {"branch": "main"}})

    building, release = threading.Event(), threading.Event()
    inner = runner.run

    def slow_bazel(argv, timeout=None, cwd=None):
        if argv and argv[0] == "bazel":
            building.set()
            assert release.wait(timeout=10)
        return inner(argv, timeout=timeout, cwd=cwd)

    runner.run = slow_bazel
    out = {}
    deploying = threading.Thread(target=lambda: out.update(client.post(
        "/v1/jobs/deployment.deploy", json={"job_id": "j1", "project_id": "little-meals", "params": {"branch": "main"}},
    ).json()), daemon=True)
    deploying.start()
    assert building.wait(timeout=10)

    start = time.monotonic()
    status = client.get("/v1/status/little-meals")
    history = client.get("/v1/deployments/little-meals")
    assert time.monotonic() - start < 2
    assert status.status_code == 200 and status.json()["branch"] == "main"
    assert history.status_code == 200 and len(history.json()) >= 1

    release.set()
    deploying.join(timeout=10)
    assert out["outcome"] == "succeeded"
