"""Additional gitutil.py coverage: real origin remotes, remote-only
branches, and the check=True error path -- gaps test_deployment_gitutil.py
didn't reach yet."""

import subprocess
from pathlib import Path

import pytest

from deployment import gitutil


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def make_repo(tmp_path: Path, name: str = "repo") -> Path:
    repo = tmp_path / name
    repo.mkdir()
    _git(["init", "-b", "main"], repo)
    _git(["config", "user.email", "t@example.com"], repo)
    _git(["config", "user.name", "T"], repo)
    (repo / "VERSION").write_text("1.0.0\n", encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-m", "first"], repo)
    return repo


def make_clone_with_origin(tmp_path: Path) -> Path:
    """A real bare origin plus a real clone -- the only way to exercise
    branch_location's REMOTE paths for real, since they depend on an
    actual `origin` remote existing."""
    upstream = make_repo(tmp_path, "upstream")
    bare = tmp_path / "upstream.git"
    subprocess.run(["git", "clone", "--bare", str(upstream), str(bare)], check=True, capture_output=True)
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", str(bare), str(clone)], check=True, capture_output=True, text=True)
    _git(["config", "user.email", "t@example.com"], clone)
    _git(["config", "user.name", "T"], clone)
    return clone


def test_run_with_check_raises_on_failure(tmp_path):
    repo = make_repo(tmp_path)
    with pytest.raises(RuntimeError, match="git command failed"):
        gitutil.run(["git", "not-a-real-subcommand"], repo, check=True)


def test_run_without_check_does_not_raise_on_failure(tmp_path):
    repo = make_repo(tmp_path)
    result = gitutil.run(["git", "not-a-real-subcommand"], repo)
    assert result.returncode != 0


def test_branch_location_remote_when_tracking_ref_already_exists(tmp_path):
    clone = make_clone_with_origin(tmp_path)
    # main is already fetched as both a local branch (checked out) and
    # available under refs/remotes/origin/main from the clone itself.
    result = gitutil.run(["git", "rev-parse", "--verify", "--quiet", "refs/remotes/origin/main"], clone)
    assert result.returncode == 0
    # Delete the local branch's own ref info isn't possible while checked
    # out, so instead prove the REMOTE branch (never checked out locally)
    # resolves via the already-fetched tracking ref path.
    upstream_dir = tmp_path / "upstream"
    _git(["checkout", "-b", "feature"], upstream_dir)
    subprocess.run(
        ["git", "push", str(tmp_path / "upstream.git"), "feature"],
        cwd=upstream_dir, check=True, capture_output=True,
    )
    subprocess.run(["git", "fetch", "origin"], cwd=clone, check=True, capture_output=True)
    assert gitutil.branch_location(clone, "feature") == gitutil.REMOTE


def test_branch_location_fetches_a_remote_only_branch_not_yet_tracked(tmp_path):
    clone = make_clone_with_origin(tmp_path)
    upstream = tmp_path / "upstream"
    _git(["checkout", "-b", "topic"], upstream)
    (upstream / "extra.txt").write_text("x", encoding="utf-8")
    _git(["add", "-A"], upstream)
    _git(["commit", "-m", "topic work"], upstream)
    subprocess.run(["git", "push", str(tmp_path / "upstream.git"), "topic"], cwd=upstream, check=True, capture_output=True)

    # The clone has never fetched "topic" -- no local branch, no tracking
    # ref yet -- so branch_location must ls-remote and fetch it itself.
    assert gitutil.run(["git", "rev-parse", "--verify", "--quiet", "refs/remotes/origin/topic"], clone).returncode != 0
    assert gitutil.branch_location(clone, "topic") == gitutil.REMOTE
    assert gitutil.run(["git", "rev-parse", "--verify", "--quiet", "refs/remotes/origin/topic"], clone).returncode == 0


def test_list_branches_excludes_origin_head_symref(tmp_path):
    clone = make_clone_with_origin(tmp_path)
    result = subprocess.run(["git", "for-each-ref", "refs/remotes/origin"], cwd=clone, capture_output=True, text=True)
    assert "origin/HEAD" in result.stdout
    branches = gitutil.list_branches(clone)
    assert "HEAD" not in branches
    assert all(not b.startswith("origin/HEAD") for b in branches)


def test_remove_worktree_falls_back_to_rmtree_when_not_a_registered_worktree(tmp_path):
    repo = make_repo(tmp_path)
    stray = tmp_path / "stray-dir"
    stray.mkdir()
    (stray / "file.txt").write_text("hi", encoding="utf-8")

    # `git worktree remove --force` fails here (stray isn't a registered
    # worktree of repo), so the shutil.rmtree fallback must do the work.
    gitutil.remove_worktree(repo, stray)
    assert not stray.exists()


def test_current_commit_returns_placeholder_outside_a_repo(tmp_path):
    empty = tmp_path / "not-a-repo"
    empty.mkdir()
    assert gitutil.current_commit(empty) == "?"
