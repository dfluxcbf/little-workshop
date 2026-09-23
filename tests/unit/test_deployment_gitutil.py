import subprocess
from pathlib import Path

import pytest

from deployment import gitutil


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-b", "main"], repo)
    _git(["config", "user.email", "t@example.com"], repo)
    _git(["config", "user.name", "T"], repo)
    (repo / "VERSION").write_text("1.0.0\n", encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-m", "first"], repo)
    return repo


def test_add_and_remove_worktree(tmp_path):
    repo = make_repo(tmp_path)
    worktree = tmp_path / "wt"
    gitutil.add_worktree(repo, "main", worktree)
    assert (worktree / "VERSION").exists()

    gitutil.remove_worktree(repo, worktree)
    assert not worktree.exists()


def test_add_worktree_cleans_up_a_stale_directory_first(tmp_path):
    repo = make_repo(tmp_path)
    worktree = tmp_path / "wt"
    gitutil.add_worktree(repo, "main", worktree)
    gitutil.add_worktree(repo, "main", worktree)  # must not fail on the second call
    assert (worktree / "VERSION").exists()
    gitutil.remove_worktree(repo, worktree)


def test_is_dirty(tmp_path):
    repo = make_repo(tmp_path)
    assert gitutil.is_dirty(repo) is False
    (repo / "new.txt").write_text("x", encoding="utf-8")
    assert gitutil.is_dirty(repo) is True


def test_current_commit_matches_head(tmp_path):
    repo = make_repo(tmp_path)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()
    assert gitutil.current_commit(repo) == head


def test_deploy_commitish_prefers_local_branch(tmp_path):
    repo = make_repo(tmp_path)
    _git(["checkout", "-b", "feature"], repo)
    assert gitutil.deploy_commitish(repo, "feature") == "feature"
    assert gitutil.deploy_commitish(repo, "ghost") == "origin/ghost"


def test_branch_location_local(tmp_path):
    repo = make_repo(tmp_path)
    _git(["checkout", "-b", "feature"], repo)
    _git(["checkout", "main"], repo)
    assert gitutil.branch_location(repo, "feature") == gitutil.LOCAL


def test_branch_location_unknown_returns_none(tmp_path):
    repo = make_repo(tmp_path)
    assert gitutil.branch_location(repo, "ghost") is None


def test_list_branches_includes_locals(tmp_path):
    repo = make_repo(tmp_path)
    _git(["branch", "dev"], repo)
    assert set(gitutil.list_branches(repo)) >= {"main", "dev"}


def test_list_worktrees_includes_main_and_linked(tmp_path):
    repo = make_repo(tmp_path)
    worktree = tmp_path / "wt"
    gitutil.add_worktree(repo, "main", worktree)
    entries = gitutil.list_worktrees(repo)
    paths = {e.path.resolve() for e in entries}
    assert repo.resolve() in paths
    assert worktree.resolve() in paths
    gitutil.remove_worktree(repo, worktree)


def test_recent_commits_reads_every_version_in_one_batch_call(tmp_path):
    # 2026-09-23 finding: this used to be one `git show <sha>:VERSION`
    # subprocess per commit — verifying the values here (not just that
    # something is returned) is what would have caught the batch-parsing
    # bug that first shipped (a wrong field count silently treated every
    # blob as missing).
    repo = make_repo(tmp_path)
    (repo / "VERSION").write_text("1.1.0\n", encoding="utf-8")
    _git(["commit", "-am", "bump"], repo)
    (repo / "VERSION").unlink()
    _git(["commit", "-am", "no version here"], repo)

    commits = gitutil.recent_commits(repo, 10)

    assert [c["subject"] for c in commits] == ["no version here", "bump", "first"]
    assert [c["version"] for c in commits] == [None, "1.1.0", "1.0.0"]
    assert all(len(c["sha"]) == 40 for c in commits)


def test_recent_commits_respects_the_limit(tmp_path):
    repo = make_repo(tmp_path)  # already 1 commit, VERSION=1.0.0
    for i in range(1, 6):
        (repo / "VERSION").write_text(f"1.0.{i}\n", encoding="utf-8")
        _git(["commit", "-am", f"bump {i}"], repo)

    assert len(gitutil.recent_commits(repo, 3)) == 3
    assert len(gitutil.recent_commits(repo, 100)) == 6  # fewer real commits than the limit — no padding, no error
