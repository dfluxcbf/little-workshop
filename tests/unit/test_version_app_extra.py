"""version.app's remaining gaps: every Job handler the dispatch table
carries besides feature_start/finish and release_start/candidate_cut
(already covered in test_version_app.py) — version.init, improvement_*,
hotfix_*, release_candidate_promote, release_finish, support_*,
config_*_hook — plus every status kind, and the ProjectResolutionError/
failure-result branches on /v1/status, /v1/config, and
/v1/release/candidates."""

import subprocess

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


def _job(client, job_type, params=None):
    return client.post(
        f"/v1/jobs/{job_type}",
        json={"job_id": "j1", "project_id": "little-widget", "params": params or {}},
    )


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def test_version_init_job_over_the_pillar_interface(paths, tmp_path):
    repo = tmp_path / "raw"
    repo.mkdir()
    _git(["init", "-b", "master"], repo)
    _git(["config", "user.email", "t@example.com"], repo)
    _git(["config", "user.name", "T"], repo)
    (repo / "f").write_text("x")
    _git(["add", "-A"], repo)
    _git(["commit", "-m", "first"], repo)
    _git(["checkout", "-b", "dev"], repo)
    _git(["checkout", "master"], repo)

    client = make_client(paths, repo)
    response = _job(client, "version.init", {"production_branch": "master", "integration_branch": "dev"})
    assert response.json()["outcome"] == "succeeded"


def test_improvement_lifecycle_job_over_the_pillar_interface(paths, tmp_path):
    repo = make_repo(tmp_path)
    client = make_client(paths, repo)
    _git(["checkout", "dev"], repo)

    start = _job(client, "version.improvement_start", {"name": "cleanup"})
    assert start.json()["outcome"] == "succeeded"

    (repo / "cleanup.py").write_text("x = 1\n", encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-m", "wip"], repo)

    finish = _job(client, "version.improvement_finish")
    assert finish.json()["outcome"] == "succeeded", finish.json()


def test_hotfix_lifecycle_job_over_the_pillar_interface(paths, tmp_path):
    repo = make_repo(tmp_path)
    client = make_client(paths, repo)

    start = _job(client, "version.hotfix_start", {"name": "urgent"})
    assert start.json()["outcome"] == "succeeded"

    (repo / "fix.py").write_text("x = 1\n", encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-m", "the fix"], repo)

    finish = _job(client, "version.hotfix_finish")
    assert finish.json()["outcome"] == "succeeded", finish.json()


def test_release_candidate_promote_and_finish_jobs_over_the_pillar_interface(paths, tmp_path):
    repo = make_repo(tmp_path)
    client = make_client(paths, repo)
    _git(["checkout", "dev"], repo)

    _job(client, "version.release_start")
    _job(client, "version.release_candidate_cut")

    promote = _job(client, "version.release_candidate_promote", {"rc_number": 1})
    assert promote.json()["outcome"] == "succeeded"

    finish = _job(client, "version.release_finish")
    assert finish.json()["outcome"] == "succeeded", finish.json()


def test_support_lifecycle_job_over_the_pillar_interface(paths, tmp_path):
    repo = make_repo(tmp_path)
    client = make_client(paths, repo)
    _git(["checkout", "dev"], repo)
    _job(client, "version.release_start")
    _job(client, "version.release_finish")

    start = _job(client, "version.support_start", {"version": "0.1.0"})
    assert start.json()["outcome"] == "succeeded"

    # support_finish is always policy-refused — the CommandResult itself
    # carries success=False without an exception, exercising run_job's
    # own "outcome: failed" branch (not the except-clause one).
    finish = _job(client, "version.support_finish")
    assert finish.json()["outcome"] == "failed"
    assert "policy-driven" in finish.json()["error"]


def test_config_hook_jobs_over_the_pillar_interface(paths, tmp_path):
    repo = make_repo(tmp_path)
    client = make_client(paths, repo)
    (repo / "hooks").mkdir()
    (repo / "hooks" / "sync.sh").write_text("#!/bin/sh\n", encoding="utf-8")

    add = _job(client, "version.config_add_hook", {"hook_path": "hooks/sync.sh"})
    assert add.json()["outcome"] == "succeeded"

    remove = _job(client, "version.config_remove_hook", {"hook_path": "hooks/sync.sh"})
    assert remove.json()["outcome"] == "succeeded"


@pytest.mark.parametrize("kind", ["feature", "improvement", "hotfix", "release"])
def test_status_endpoint_covers_every_kind(paths, tmp_path, kind):
    repo = make_repo(tmp_path)
    client = make_client(paths, repo)
    response = client.get(f"/v1/status/{kind}", params={"project_id": "little-widget"})
    assert response.status_code == 200
    assert response.json()["success"] is False  # not on that branch type, but a clean 200


def test_status_endpoint_rejects_unknown_kind(paths, tmp_path):
    repo = make_repo(tmp_path)
    client = make_client(paths, repo)
    response = client.get("/v1/status/bogus", params={"project_id": "little-widget"})
    assert response.status_code == 404


def test_status_endpoint_404s_on_unresolvable_project(paths, tmp_path):
    repo = make_repo(tmp_path)
    client = make_client(paths, repo)
    response = client.get("/v1/status/feature", params={"project_id": "ghost"})
    assert response.status_code == 404


def test_config_endpoint_404s_on_unresolvable_project(paths, tmp_path):
    repo = make_repo(tmp_path)
    client = make_client(paths, repo)
    response = client.get("/v1/config", params={"project_id": "ghost"})
    assert response.status_code == 404


def test_release_candidates_endpoint_404s_on_unresolvable_project(paths, tmp_path):
    repo = make_repo(tmp_path)
    client = make_client(paths, repo)
    response = client.get("/v1/release/candidates", params={"project_id": "ghost"})
    assert response.status_code == 404


def test_release_candidates_endpoint_400s_when_not_on_a_release_branch(paths, tmp_path):
    repo = make_repo(tmp_path)
    client = make_client(paths, repo)
    response = client.get("/v1/release/candidates", params={"project_id": "little-widget"})
    assert response.status_code == 400
