"""`project.connect_github` (REQ-000000279..282): give a registered
project its GitHub setup after the fact. Real git repos throughout; the
GitHub boundary (repo creation, labels) and the network push are mocked."""

import json
import subprocess

import pytest
from fastapi.testclient import TestClient

from common.paths import Paths
from orchestration import dispatch
from registry import connect_github as connect_module
from registry import db, github
from registry.app import build_app
from registry.connect_github import ConnectGitHubError, connect_github
from registry.models import ConnectGitHubParams, Project


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "registry.db")
    yield connection
    connection.close()


@pytest.fixture
def paths(tmp_path):
    p = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    p.ensure_layout()
    return p


def _git(args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def project(conn, tmp_path):
    repo = tmp_path / "ethereal-lands"
    repo.mkdir()
    _git(["init", "-b", "master"], repo)
    _git(["config", "user.email", "t@example.com"], repo)
    _git(["config", "user.name", "T"], repo)
    (repo / "README.md").write_text("# x\n")
    _git(["add", "-A"], repo)
    _git(["commit", "-m", "first"], repo)
    db.insert_project(conn, Project(name="ethereal-lands", path=str(repo)))
    return repo


@pytest.fixture
def fake_github(monkeypatch):
    """Records GitHub calls; `add_remote_and_push` only adds the remote
    (no network), so a second run sees what the first one created."""
    calls = {"create": [], "labels": [], "push": []}

    def create_repository(name, token, *, private, description):
        calls["create"].append((name, token, private, description))
        return {"html_url": f"https://github.com/dfluxcbf/{name}"}

    def add_remote_and_push(path, remote_url):
        _git(["remote", "add", "origin", remote_url], path)
        calls["push"].append(remote_url)

    monkeypatch.setattr(github, "create_repository", create_repository)
    monkeypatch.setattr(github, "create_repository_with_gh", lambda *a, **k: pytest.fail("gh used despite a token"))
    monkeypatch.setattr(github, "ensure_label", lambda repo, token, name, *_: calls["labels"].append((repo, name)))
    monkeypatch.setattr(github, "ensure_label_with_gh", lambda repo, name, *_: calls["labels"].append((repo, name)))
    monkeypatch.setattr(connect_module.gitops, "add_remote_and_push", add_remote_and_push)
    return calls


# --- REQ-000000279: no origin -> create, push, record ------------------------

def test_creates_the_repo_pushes_and_records_repo_url(conn, paths, project, fake_github):
    result = connect_github(conn, paths, ConnectGitHubParams(name="ethereal-lands", github_token="tok", description="d"))

    assert fake_github["create"] == [("ethereal-lands", "tok", True, "d")]
    assert fake_github["push"] == ["git@github.com:dfluxcbf/ethereal-lands.git"]
    assert result.github_created and result.pushed and not result.adopted_remote
    assert result.repo_url == "https://github.com/dfluxcbf/ethereal-lands"
    assert db.get_project(conn, "ethereal-lands").repo_url == result.repo_url
    info = json.loads((paths.project_dir("ethereal-lands") / "info.registry.json").read_text())
    assert info == {"name": "ethereal-lands", "path": str(project), "repo_url": result.repo_url}


def test_public_flag_creates_a_public_repo(conn, paths, project, fake_github):
    connect_github(conn, paths, ConnectGitHubParams(name="ethereal-lands", github_token="tok", public=True))
    assert fake_github["create"][0][2] is False


def test_without_token_or_gh_fails_and_changes_nothing(conn, paths, project, fake_github, monkeypatch):
    monkeypatch.setattr(github, "gh_available", lambda: False)
    with pytest.raises(ConnectGitHubError, match="no way to create"):
        connect_github(conn, paths, ConnectGitHubParams(name="ethereal-lands"))
    assert _git(["remote"], project) == ""
    assert db.get_project(conn, "ethereal-lands").repo_url is None


def test_uses_gh_when_no_token_is_given(conn, paths, project, fake_github, monkeypatch):
    monkeypatch.setattr(github, "gh_available", lambda: True)
    monkeypatch.setattr(github, "create_repository_with_gh",
                        lambda name, owner, *, private, description: {"html_url": f"https://github.com/{owner}/{name}"})
    result = connect_github(conn, paths, ConnectGitHubParams(name="ethereal-lands"))
    assert result.github_created
    assert len(fake_github["labels"]) == len(github.BOOTSTRAP_LABELS)


# --- REQ-000000280: existing origin -> adopt ---------------------------------

def test_adopts_an_existing_ssh_remote_without_creating_or_pushing(conn, paths, project, fake_github, monkeypatch):
    monkeypatch.setattr(github, "gh_available", lambda: True)
    _git(["remote", "add", "origin", "git@github.com:dfluxcbf/ethereal-lands.git"], project)

    result = connect_github(conn, paths, ConnectGitHubParams(name="ethereal-lands"))

    assert fake_github["create"] == [] and fake_github["push"] == []
    assert result.adopted_remote and not result.github_created and not result.pushed
    assert result.repo_url == "https://github.com/dfluxcbf/ethereal-lands"
    assert db.get_project(conn, "ethereal-lands").repo_url == result.repo_url


@pytest.mark.parametrize("remote, expected", [
    ("git@github.com:dfluxcbf/ethereal-lands.git", "https://github.com/dfluxcbf/ethereal-lands"),
    ("ssh://git@github.com/dfluxcbf/ethereal-lands.git", "https://github.com/dfluxcbf/ethereal-lands"),
    ("https://github.com/dfluxcbf/ethereal-lands.git", "https://github.com/dfluxcbf/ethereal-lands"),
    ("https://github.com/dfluxcbf/ethereal-lands/", "https://github.com/dfluxcbf/ethereal-lands"),
    ("https://github.com/dfluxcbf/ethereal-lands", "https://github.com/dfluxcbf/ethereal-lands"),
    ("/srv/git/ethereal-lands.git", "/srv/git/ethereal-lands.git"),
])
def test_web_url_normalises_remote_forms(remote, expected):
    assert github.web_url(remote) == expected


def test_full_name_from_url():
    assert github.full_name_from_url("https://github.com/dfluxcbf/ethereal-lands") == "dfluxcbf/ethereal-lands"
    assert github.full_name_from_url("/srv/git/x.git") is None
    assert github.full_name_from_url("https://github.com/dfluxcbf") is None


def test_adopting_a_non_github_remote_records_it_and_skips_labels(conn, paths, project, fake_github):
    _git(["remote", "add", "origin", "/srv/git/ethereal-lands.git"], project)
    result = connect_github(conn, paths, ConnectGitHubParams(name="ethereal-lands"))
    assert result.repo_url == "/srv/git/ethereal-lands.git"
    assert result.labels_created == [] and fake_github["labels"] == []


# --- REQ-000000281: labels, repeatable ---------------------------------------

def test_ensures_the_bootstrap_labels_on_the_repo(conn, paths, project, fake_github):
    result = connect_github(conn, paths, ConnectGitHubParams(name="ethereal-lands", github_token="tok"))
    assert result.labels_created == [name for name, _, _ in github.BOOTSTRAP_LABELS]
    assert {repo for repo, _ in fake_github["labels"]} == {"dfluxcbf/ethereal-lands"}


def test_ensure_labels_false_skips_labels(conn, paths, project, fake_github):
    result = connect_github(conn, paths, ConnectGitHubParams(name="ethereal-lands", github_token="tok", ensure_labels=False))
    assert result.labels_created == [] and fake_github["labels"] == []


def test_running_twice_is_idempotent(conn, paths, project, fake_github):
    params = ConnectGitHubParams(name="ethereal-lands", github_token="tok")
    first = connect_github(conn, paths, params)
    second = connect_github(conn, paths, params)
    assert len(fake_github["create"]) == 1 and len(fake_github["push"]) == 1
    assert second.adopted_remote and second.repo_url == first.repo_url
    assert db.get_project(conn, "ethereal-lands").repo_url == first.repo_url


def test_label_failure_is_reported_after_connecting(conn, paths, project, fake_github, monkeypatch):
    def boom(*a, **k):
        raise github.GitHubError("rate limited")

    monkeypatch.setattr(github, "ensure_label", boom)
    with pytest.raises(ConnectGitHubError, match="connected https://github.com/dfluxcbf/ethereal-lands.*rate limited"):
        connect_github(conn, paths, ConnectGitHubParams(name="ethereal-lands", github_token="tok"))


# --- REQ-000000282: refusals, Job endpoint, MCP, CLI, dispatch ----------------

def test_unregistered_project_is_refused(conn, paths):
    with pytest.raises(ConnectGitHubError, match="not registered"):
        connect_github(conn, paths, ConnectGitHubParams(name="nope"))


def test_project_path_that_is_not_a_git_repo_is_refused(conn, paths, tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    db.insert_project(conn, Project(name="plain", path=str(plain)))
    with pytest.raises(ConnectGitHubError, match="not a git repository"):
        connect_github(conn, paths, ConnectGitHubParams(name="plain"))
    assert db.get_project(conn, "plain").repo_url is None


def test_job_endpoint_adopts_and_reports_failures(conn, paths, project):
    app_client = TestClient(build_app(conn, paths))
    _git(["remote", "add", "origin", "git@github.com:dfluxcbf/ethereal-lands.git"], project)

    ok = app_client.post("/v1/jobs/project.connect_github", json={
        "job_id": "j1", "project_id": "ethereal-lands", "params": {"ensure_labels": False},
    }).json()
    assert ok["outcome"] == "succeeded"
    assert ok["result"]["repo_url"] == "https://github.com/dfluxcbf/ethereal-lands"

    missing = app_client.post("/v1/jobs/project.connect_github", json={
        "job_id": "j2", "params": {"name": "nope"},
    }).json()
    assert missing["outcome"] == "failed" and "not registered" in missing["error"]


def test_cli_queues_a_registry_job(paths, monkeypatch):
    from cli import main

    captured = {}
    monkeypatch.setattr(main.client, "create_job", lambda p, **kw: captured.update(kw) or {"status": "succeeded"})
    assert main.main(["project", "connect-github", "ethereal-lands", "--no-labels", "--owner", "me"]) == 0
    assert captured["type"] == "project.connect_github"
    assert captured["params"]["ensure_labels"] is False and captured["params"]["owner"] == "me"


def test_dispatch_gives_it_a_network_sized_timeout_and_never_interrupts_it():
    assert dispatch.JOB_TYPE_TIMEOUTS["project.connect_github"] >= 120
    assert "project.connect_github" in dispatch.DECLARED_UNSTOPPABLE_JOB_TYPES
