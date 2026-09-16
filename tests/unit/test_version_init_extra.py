"""version.commands.init's remaining error paths — the happy path and the
"not a git repo"/"no production branch" cases are already covered in
test_version_init_and_gitops.py; this covers the rest: no commits,
explicit-but-invalid/missing branch names for both production and
integration."""

import subprocess

from version.commands.init import cmd_init


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def test_init_rejects_repo_with_no_commits(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-b", "master"], repo)
    result = cmd_init(repo)
    assert result.success is False
    assert "no commits" in result.message.lower()


def _repo_on_master(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-b", "master"], repo)
    _git(["config", "user.email", "t@example.com"], repo)
    _git(["config", "user.name", "T"], repo)
    (repo / "f").write_text("x")
    _git(["add", "-A"], repo)
    _git(["commit", "-m", "first"], repo)
    return repo


def test_init_rejects_explicit_invalid_production_branch(tmp_path):
    repo = _repo_on_master(tmp_path)
    result = cmd_init(repo, production_branch="trunk")
    assert result.success is False
    assert "Invalid production branch" in result.message


def test_init_rejects_explicit_production_branch_that_does_not_exist(tmp_path):
    repo = _repo_on_master(tmp_path)
    result = cmd_init(repo, production_branch="main")  # valid name, but no "main" branch exists
    assert result.success is False
    assert "does not exist" in result.message


def test_init_rejects_missing_integration_branch(tmp_path):
    repo = _repo_on_master(tmp_path)
    result = cmd_init(repo, production_branch="master")
    assert result.success is False
    assert "No integration branch found" in result.message


def test_init_rejects_explicit_invalid_integration_branch(tmp_path):
    repo = _repo_on_master(tmp_path)
    result = cmd_init(repo, production_branch="master", integration_branch="trunk")
    assert result.success is False
    assert "Invalid integration branch" in result.message


def test_init_rejects_explicit_integration_branch_that_does_not_exist(tmp_path):
    repo = _repo_on_master(tmp_path)
    result = cmd_init(repo, production_branch="master", integration_branch="dev")
    assert result.success is False
    assert "does not exist" in result.message


def test_init_succeeds_with_explicit_valid_branches(tmp_path):
    repo = _repo_on_master(tmp_path)
    _git(["checkout", "-b", "dev"], repo)
    _git(["checkout", "master"], repo)

    result = cmd_init(repo, production_branch="master", integration_branch="dev")
    assert result.success, result.message
    assert result.data == {"production_branch": "master", "integration_branch": "dev"}
