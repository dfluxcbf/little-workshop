import pytest
from fastapi.testclient import TestClient

from common.paths import Paths
from registry import db
from registry.app import build_app


@pytest.fixture
def paths(tmp_path):
    p = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    p.ensure_layout()
    return p


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "registry.db")
    yield connection
    connection.close()


@pytest.fixture
def client(conn, paths, tmp_path):
    (tmp_path / "workdir").mkdir()
    return TestClient(build_app(conn, paths)), tmp_path / "workdir"


def test_health_endpoint(client):
    app_client, _ = client
    response = app_client.get("/v1/health")
    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_job_bootstrap_creates_project_and_registers(client):
    app_client, workdir = client
    response = app_client.post(
        "/v1/jobs/project.bootstrap",
        json={"job_id": "j1", "params": {"name": "little-widget", "dest_dir": str(workdir)}},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] == "succeeded"
    assert body["result"]["name"] == "little-widget"

    listed = app_client.get("/v1/projects").json()
    assert [p["name"] for p in listed] == ["little-widget"]


def test_job_bootstrap_unity_template_scaffolds_the_unity_layout(client):
    """REQ-000000274."""
    app_client, workdir = client
    body = app_client.post(
        "/v1/jobs/project.bootstrap",
        json={"job_id": "j1", "params": {"name": "little-game", "dest_dir": str(workdir), "template": "unity"}},
    ).json()
    assert body["outcome"] == "succeeded"
    assert (workdir / "little-game" / "CLAUDE.md").exists()
    assert not (workdir / "little-game" / "MODULE.bazel").exists()


def test_job_bootstrap_unknown_template_fails_without_writing(client):
    """REQ-000000274."""
    app_client, workdir = client
    body = app_client.post(
        "/v1/jobs/project.bootstrap",
        json={"job_id": "j1", "params": {"name": "little-game", "dest_dir": str(workdir), "template": "godot"}},
    ).json()
    assert body["outcome"] == "failed"
    assert "template" in body["error"]
    assert not (workdir / "little-game").exists()


def test_job_bootstrap_version_initialized_false_when_version_pillar_unreachable(client):
    """No real Version Control & Release unit is running in this test's
    own paths — `version_initialized` must degrade to False, never fail
    the bootstrap itself (M16's own documented simplification)."""
    app_client, workdir = client
    response = app_client.post(
        "/v1/jobs/project.bootstrap",
        json={"job_id": "j1", "params": {"name": "little-widget", "dest_dir": str(workdir)}},
    )
    assert response.json()["result"]["version_initialized"] is False


def test_job_bootstrap_runs_the_real_git_dance_when_version_pillar_reachable(client, monkeypatch):
    """Mocks only the cross-pillar boundary (version_client itself) --
    the git add/commit/checkout/merge sequence that follows is real, run
    against the real scaffolded repo `bootstrap_project` just created."""
    import registry.app as app_module

    def fake_init(paths, project_id):
        # The real version.init Job would have written this file via a
        # genuine cross-pillar call; write it directly here so the git
        # add/commit that follows has something real to commit.
        (workdir / project_id / ".lvx").mkdir(exist_ok=True)
        (workdir / project_id / ".lvx" / "config.json").write_text("{}\n", encoding="utf-8")
        return True

    monkeypatch.setattr(app_module.version_client, "init_version_tracking", fake_init)

    app_client, workdir = client
    response = app_client.post(
        "/v1/jobs/project.bootstrap",
        json={"job_id": "j1", "params": {"name": "little-widget", "dest_dir": str(workdir)}},
    )
    result = response.json()["result"]
    assert result["version_initialized"] is True

    import subprocess

    project_dir = workdir / "little-widget"
    log = subprocess.run(
        ["git", "log", "--all", "--oneline"], cwd=project_dir, capture_output=True, text=True, check=True,
    ).stdout
    assert "chore: version init" in log
    branch = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=project_dir, capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert branch == "dev"  # ends on dev, the ready-to-work gitflow state


def test_job_bootstrap_duplicate_fails_with_outcome_not_http_error(client):
    app_client, workdir = client
    params = {"name": "little-widget", "dest_dir": str(workdir)}
    app_client.post("/v1/jobs/project.bootstrap", json={"job_id": "j1", "params": params})
    response = app_client.post("/v1/jobs/project.bootstrap", json={"job_id": "j2", "params": params})

    assert response.status_code == 200
    assert response.json()["outcome"] == "failed"


def test_job_remove_deletes_project(client):
    app_client, workdir = client
    params = {"name": "little-widget", "dest_dir": str(workdir)}
    app_client.post("/v1/jobs/project.bootstrap", json={"job_id": "j1", "params": params})

    response = app_client.post(
        "/v1/jobs/project.remove", json={"job_id": "j2", "params": {"name": "little-widget"}}
    )
    assert response.json()["outcome"] == "succeeded"
    assert app_client.get("/v1/projects/little-widget").status_code == 404


def test_job_remove_unknown_project_fails_gracefully(client):
    app_client, _ = client
    response = app_client.post(
        "/v1/jobs/project.remove", json={"job_id": "j1", "params": {"name": "ghost"}}
    )
    assert response.json()["outcome"] == "failed"


def test_project_set_lifecycle(client):
    app_client, workdir = client
    app_client.post(
        "/v1/jobs/project.bootstrap",
        json={"job_id": "j1", "params": {"name": "a", "dest_dir": str(workdir)}},
    )
    app_client.post(
        "/v1/jobs/project.bootstrap",
        json={"job_id": "j2", "params": {"name": "b", "dest_dir": str(workdir)}},
    )

    create = app_client.post("/v1/project_sets", json={"name": "household", "project_names": ["a"]})
    assert create.status_code == 200

    added = app_client.post("/v1/project_sets/household/members/b")
    assert set(added.json()["project_names"]) == {"a", "b"}

    removed = app_client.delete("/v1/project_sets/household/members/a")
    assert removed.json()["project_names"] == ["b"]

    listed = app_client.get("/v1/project_sets").json()
    assert [s["name"] for s in listed] == ["household"]


def test_ticket_lifecycle(client):
    app_client, workdir = client
    app_client.post(
        "/v1/jobs/project.bootstrap",
        json={"job_id": "j1", "params": {"name": "a", "dest_dir": str(workdir)}},
    )
    created = app_client.post("/v1/tickets", json={"project_name": "a", "title": "fix the thing"})
    assert created.status_code == 200

    listed = app_client.get("/v1/tickets", params={"project_name": "a"}).json()
    assert len(listed) == 1
    assert listed[0]["title"] == "fix the thing"


def test_get_project_success_for_existing_project(client):
    app_client, workdir = client
    app_client.post(
        "/v1/jobs/project.bootstrap",
        json={"job_id": "j1", "params": {"name": "little-widget", "dest_dir": str(workdir)}},
    )
    response = app_client.get("/v1/projects/little-widget")
    assert response.status_code == 200
    assert response.json()["name"] == "little-widget"


def test_job_remove_missing_name_and_no_project_id_fails_gracefully(client):
    app_client, _ = client
    response = app_client.post("/v1/jobs/project.remove", json={"job_id": "j1", "params": {}})
    assert response.status_code == 200
    assert response.json()["outcome"] == "failed"
    assert "missing project name" in response.json()["error"]


def test_job_remove_falls_back_to_project_id_when_params_name_absent(client):
    app_client, workdir = client
    app_client.post(
        "/v1/jobs/project.bootstrap",
        json={"job_id": "j1", "params": {"name": "little-widget", "dest_dir": str(workdir)}},
    )
    response = app_client.post(
        "/v1/jobs/project.remove", json={"job_id": "j2", "project_id": "little-widget", "params": {}}
    )
    assert response.json()["outcome"] == "succeeded"


def test_get_project_set_success(client):
    app_client, workdir = client
    app_client.post(
        "/v1/jobs/project.bootstrap",
        json={"job_id": "j1", "params": {"name": "a", "dest_dir": str(workdir)}},
    )
    app_client.post("/v1/project_sets", json={"name": "household", "project_names": ["a"]})

    response = app_client.get("/v1/project_sets/household")
    assert response.status_code == 200
    assert response.json()["project_names"] == ["a"]


def test_get_project_set_404_for_unknown_set(client):
    app_client, _ = client
    response = app_client.get("/v1/project_sets/ghost")
    assert response.status_code == 404


def test_add_project_set_member_404_for_unknown_set(client):
    app_client, workdir = client
    app_client.post(
        "/v1/jobs/project.bootstrap",
        json={"job_id": "j1", "params": {"name": "a", "dest_dir": str(workdir)}},
    )
    response = app_client.post("/v1/project_sets/ghost/members/a")
    assert response.status_code == 404


def test_remove_project_set_member_404_for_unknown_set(client):
    app_client, _ = client
    response = app_client.delete("/v1/project_sets/ghost/members/a")
    assert response.status_code == 404


def test_list_tickets_without_project_name_filter_returns_every_ticket(client):
    app_client, workdir = client
    app_client.post(
        "/v1/jobs/project.bootstrap",
        json={"job_id": "j1", "params": {"name": "a", "dest_dir": str(workdir)}},
    )
    app_client.post(
        "/v1/jobs/project.bootstrap",
        json={"job_id": "j2", "params": {"name": "b", "dest_dir": str(workdir)}},
    )
    app_client.post("/v1/tickets", json={"project_name": "a", "title": "fix a"})
    app_client.post("/v1/tickets", json={"project_name": "b", "title": "fix b"})

    listed = app_client.get("/v1/tickets").json()
    assert {t["title"] for t in listed} == {"fix a", "fix b"}


def test_add_project_set_member_is_idempotent(client):
    app_client, workdir = client
    app_client.post(
        "/v1/jobs/project.bootstrap",
        json={"job_id": "j1", "params": {"name": "a", "dest_dir": str(workdir)}},
    )
    app_client.post("/v1/project_sets", json={"name": "household", "project_names": ["a"]})

    first = app_client.post("/v1/project_sets/household/members/a")
    second = app_client.post("/v1/project_sets/household/members/a")
    assert first.json()["project_names"] == ["a"]
    assert second.json()["project_names"] == ["a"]


def test_job_bootstrap_missing_required_params_fails_cleanly_not_a_crash(client):
    app_client, _ = client
    response = app_client.post(
        "/v1/jobs/project.bootstrap", json={"job_id": "j1", "params": {"name": "little-widget"}}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] == "failed"
    assert "dest_dir" in body["error"]


def _git(args, cwd) -> None:
    import subprocess

    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def test_job_import_registers_an_existing_repo(client):
    app_client, workdir = client
    repo = workdir / "little-widget"
    repo.mkdir()
    _git(["init", "-b", "master"], repo)
    _git(["config", "user.email", "t@example.com"], repo)
    _git(["config", "user.name", "T"], repo)
    (repo / "README.md").write_text("# widget\n", encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-m", "first"], repo)

    response = app_client.post(
        "/v1/jobs/project.import", json={"job_id": "j1", "params": {"name": "little-widget", "path": str(repo)}}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] == "succeeded"
    assert body["result"]["path"] == str(repo)

    project = app_client.get("/v1/projects/little-widget").json()
    assert project["path"] == str(repo)


def test_job_import_missing_required_params_fails_cleanly_not_a_crash(client):
    app_client, _ = client
    response = app_client.post(
        "/v1/jobs/project.import", json={"job_id": "j1", "params": {"name": "little-widget"}}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] == "failed"
    assert "path" in body["error"]


def test_job_import_nonexistent_path_fails_cleanly(client):
    app_client, workdir = client
    response = app_client.post(
        "/v1/jobs/project.import",
        json={"job_id": "j1", "params": {"name": "little-widget", "path": str(workdir / "nowhere")}},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] == "failed"
    assert "does not exist" in body["error"]


def test_secrets_reports_the_github_token_status(client, monkeypatch):
    app_client, _ = client
    from registry import github

    monkeypatch.setattr(github, "github_token_status", lambda: {"present": True, "valid": True, "expires_at": None, "detail": "no expiration set"})
    assert app_client.get("/v1/secrets").json() == [{"name": "github", "present": True, "valid": True, "expires_at": None, "detail": "no expiration set"}]


def test_secrets_are_cached_for_a_few_minutes_not_rechecked_every_call(client, monkeypatch):
    # 2026-09-23 finding: github_token_status() shells out to `gh` twice
    # per call, including a real network round trip — caching it is what
    # fixed the Dashboard's Pillars page.
    app_client, _ = client
    from registry import github

    calls = []
    monkeypatch.setattr(
        github, "github_token_status",
        lambda: calls.append(1) or {"present": True, "valid": True, "expires_at": None, "detail": ""},
    )

    first = app_client.get("/v1/secrets").json()
    second = app_client.get("/v1/secrets").json()

    assert len(calls) == 1  # the second request reused the cached value
    assert first == second


def test_secrets_cache_expires_after_its_ttl(client, monkeypatch):
    import time as time_module

    from registry import app as app_module, github

    app_client, _ = client
    calls = []
    monkeypatch.setattr(
        github, "github_token_status",
        lambda: calls.append(1) or {"present": True, "valid": True, "expires_at": None, "detail": ""},
    )
    fake_now = [1000.0]
    monkeypatch.setattr(time_module, "monotonic", lambda: fake_now[0])

    app_client.get("/v1/secrets")
    fake_now[0] += app_module.SECRETS_CACHE_TTL_SECONDS + 1
    app_client.get("/v1/secrets")

    assert len(calls) == 2  # a real refresh happened once the TTL elapsed
