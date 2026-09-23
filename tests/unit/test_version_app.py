import pytest
from fastapi.testclient import TestClient
from version_repo_helper import make_repo

from common.paths import Paths
from version.app import build_app
from version.resolve import ProjectResolutionError


@pytest.fixture
def paths(tmp_path):
    return Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")


def make_client(paths, repo):
    def fake_resolve(_paths, project_id):
        if project_id != "little-widget":
            raise ProjectResolutionError(f"project {project_id!r} is not registered")
        return repo

    return TestClient(build_app(paths, resolve=fake_resolve))


def test_health(paths, tmp_path):
    repo = make_repo(tmp_path)
    client = make_client(paths, repo)
    assert client.get("/v1/health").json()["ok"] is True


def test_unknown_project_id_fails_cleanly(paths, tmp_path):
    repo = make_repo(tmp_path)
    client = make_client(paths, repo)
    response = client.post(
        "/v1/jobs/version.feature_start",
        json={"job_id": "j1", "project_id": "ghost", "params": {"name": "widgets"}},
    )
    assert response.status_code == 200
    assert response.json()["outcome"] == "failed"


def test_unknown_job_type_404s(paths, tmp_path):
    repo = make_repo(tmp_path)
    client = make_client(paths, repo)
    response = client.post(
        "/v1/jobs/version.nonexistent", json={"job_id": "j1", "project_id": "little-widget", "params": {}}
    )
    assert response.status_code == 404


def test_feature_lifecycle_over_the_pillar_interface(paths, tmp_path):
    repo = make_repo(tmp_path)
    client = make_client(paths, repo)

    import subprocess

    subprocess.run(["git", "checkout", "dev"], cwd=repo, check=True, capture_output=True)

    start = client.post(
        "/v1/jobs/version.feature_start",
        json={"job_id": "j1", "project_id": "little-widget", "params": {"name": "widgets"}},
    )
    assert start.json()["outcome"] == "succeeded"

    (repo / "widgets.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "wip"], cwd=repo, check=True, capture_output=True)

    status = client.get("/v1/status/feature", params={"project_id": "little-widget"})
    assert status.json()["data"]["branch"] == "feature/widgets"

    finish = client.post(
        "/v1/jobs/version.feature_finish", json={"job_id": "j2", "project_id": "little-widget", "params": {}}
    )
    assert finish.json()["outcome"] == "succeeded", finish.json()


def test_dirty_tree_error_surfaces_as_failed_outcome_not_500(paths, tmp_path):
    repo = make_repo(tmp_path)
    client = make_client(paths, repo)
    (repo / "dirty.txt").write_text("x", encoding="utf-8")

    response = client.post(
        "/v1/jobs/version.feature_start",
        json={"job_id": "j1", "project_id": "little-widget", "params": {"name": "widgets"}},
    )
    assert response.status_code == 200
    assert response.json()["outcome"] == "failed"
    assert "dirty" in response.json()["error"].lower()


def test_config_and_release_candidates_endpoints(paths, tmp_path):
    repo = make_repo(tmp_path)
    client = make_client(paths, repo)

    config = client.get("/v1/config", params={"project_id": "little-widget"})
    assert config.status_code == 200
    assert config.json()["production_branch"] == "master"

    import subprocess

    subprocess.run(["git", "checkout", "dev"], cwd=repo, check=True, capture_output=True)
    client.post("/v1/jobs/version.release_start", json={"job_id": "j1", "project_id": "little-widget", "params": {}})
    client.post(
        "/v1/jobs/version.release_candidate_cut", json={"job_id": "j2", "project_id": "little-widget", "params": {}}
    )

    candidates = client.get("/v1/release/candidates", params={"project_id": "little-widget"})
    assert candidates.json()["tags"] == ["v0.1.0-rc.1"]
