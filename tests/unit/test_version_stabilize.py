"""version.stabilize — ported from the ecosystem's own root
`stabilize_branches.py`. Every test runs against a real git repo with a
real bare `origin` remote — no mocked git calls, matching this pillar's
existing test style (`test_version_branch_audit.py`'s own sibling
module)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from version.commands.init import cmd_init
from version.stabilize import stabilize
from version_repo_helper import make_repo as make_repo_no_remote


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def make_repo_with_remote(tmp_path: Path) -> Path:
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-b", "master", str(bare)], check=True, capture_output=True)

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-b", "master"], repo)
    _git(["config", "user.email", "t@example.com"], repo)
    _git(["config", "user.name", "T"], repo)
    _git(["remote", "add", "origin", str(bare)], repo)
    (repo / "VERSION").write_text("0.1.0\n", encoding="utf-8")
    (repo / "README.md").write_text("# repo\n", encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-m", "first"], repo)
    _git(["checkout", "-b", "dev"], repo)
    _git(["checkout", "master"], repo)

    result = cmd_init(repo, production_branch="master", integration_branch="dev")
    assert result.success, result.message
    _git(["add", "-A"], repo)
    _git(["commit", "-m", "chore: version init"], repo)
    _git(["push", "-u", "origin", "master"], repo)
    _git(["checkout", "dev"], repo)
    _git(["merge", "--ff-only", "master"], repo)
    _git(["push", "-u", "origin", "dev"], repo)
    _git(["checkout", "master"], repo)
    return repo


def test_clean_dev_dry_run_reports_stabilized_without_mutating(tmp_path):
    repo = make_repo_with_remote(tmp_path)
    _git(["checkout", "dev"], repo)
    before_head = _git(["rev-parse", "HEAD"], repo).stdout.strip()

    result = stabilize(repo, execute=False)

    assert result.status == "stabilized"
    assert result.branch_before == "dev"
    assert result.branch_after == "dev"
    assert result.was_dirty is False
    assert result.pushed == ["dev"]
    assert _git(["rev-parse", "HEAD"], repo).stdout.strip() == before_head  # nothing mutated


def test_dirty_dev_execute_commits_and_pushes(tmp_path):
    repo = make_repo_with_remote(tmp_path)
    _git(["checkout", "dev"], repo)
    (repo / "new.txt").write_text("x", encoding="utf-8")

    result = stabilize(repo, execute=True)

    assert result.status == "stabilized"
    assert result.was_dirty is True
    assert result.committed is True
    assert result.pushed == ["dev"]
    assert not subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True).stdout.strip()

    bare_log = subprocess.run(
        ["git", "log", "--oneline", "dev"], cwd=repo.parent / "origin.git", capture_output=True, text=True,
    ).stdout
    assert "chore(stabilize)" in bare_log


def test_feature_branch_dry_run_reports_would_finish_without_mutating(tmp_path):
    repo = make_repo_with_remote(tmp_path)
    _git(["checkout", "dev"], repo)
    _git(["checkout", "-b", "feature/widgets"], repo)
    (repo / "widget.txt").write_text("widget", encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-m", "add widget"], repo)

    result = stabilize(repo, execute=False)

    assert result.status == "stabilized"
    assert result.finished is True
    assert result.branch_after == "dev"
    assert result.pushed == ["dev"]
    # dry run: still on the feature branch, nothing merged
    assert _git(["rev-parse", "--abbrev-ref", "HEAD"], repo).stdout.strip() == "feature/widgets"


def test_feature_branch_execute_really_finishes_and_pushes(tmp_path):
    repo = make_repo_with_remote(tmp_path)
    _git(["checkout", "dev"], repo)
    _git(["checkout", "-b", "feature/widgets"], repo)
    (repo / "widget.txt").write_text("widget", encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-m", "add widget"], repo)

    result = stabilize(repo, execute=True)

    assert result.status == "stabilized"
    assert result.finished is True
    assert result.branch_after == "dev"
    assert result.pushed == ["dev"]
    assert _git(["rev-parse", "--abbrev-ref", "HEAD"], repo).stdout.strip() == "dev"
    assert (repo / "widget.txt").exists()
    branches = _git(["branch"], repo).stdout
    assert "feature/widgets" not in branches

    bare_log = subprocess.run(
        ["git", "log", "--oneline", "dev"], cwd=repo.parent / "origin.git", capture_output=True, text=True,
    ).stdout
    assert "widgets" in bare_log  # the finish flow's own squash-merge commit


def test_already_merged_remote_branch_is_deleted_on_execute(tmp_path):
    repo = make_repo_with_remote(tmp_path)
    _git(["checkout", "dev"], repo)
    _git(["checkout", "-b", "feature/old"], repo)
    (repo / "old.txt").write_text("old", encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-m", "old work"], repo)
    _git(["push", "-u", "origin", "feature/old"], repo)
    _git(["checkout", "dev"], repo)
    _git(["merge", "--no-ff", "feature/old"], repo)
    _git(["push", "origin", "dev"], repo)
    # feature/old is now merged into dev and pushed, but not deleted --
    # exactly the leftover stabilize is supposed to clean up.

    result = stabilize(repo, execute=True)

    assert "feature/old" in result.deleted_remote_branches
    assert "feature/old" in result.deleted_local_branches
    remote_branches = _git(["branch", "-r"], repo).stdout
    assert "origin/feature/old" not in remote_branches


def test_already_merged_remote_branch_reported_but_not_deleted_on_dry_run(tmp_path):
    repo = make_repo_with_remote(tmp_path)
    _git(["checkout", "dev"], repo)
    _git(["checkout", "-b", "feature/old"], repo)
    (repo / "old.txt").write_text("old", encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-m", "old work"], repo)
    _git(["push", "-u", "origin", "feature/old"], repo)
    _git(["checkout", "dev"], repo)
    _git(["merge", "--no-ff", "feature/old"], repo)
    _git(["push", "origin", "dev"], repo)

    result = stabilize(repo, execute=False)

    assert "feature/old" in result.deleted_remote_branches
    remote_branches = _git(["branch", "-r"], repo).stdout
    assert "origin/feature/old" in remote_branches  # dry run: still there


def test_release_branch_is_skipped_not_finished(tmp_path):
    repo = make_repo_with_remote(tmp_path)
    _git(["checkout", "dev"], repo)
    _git(["checkout", "-b", "release/1.0.0"], repo)

    result = stabilize(repo, execute=True)

    assert result.status == "skipped"
    assert "long-lived" in result.message
    assert _git(["rev-parse", "--abbrev-ref", "HEAD"], repo).stdout.strip() == "release/1.0.0"


def test_unknown_branch_name_is_skipped_not_finished(tmp_path):
    repo = make_repo_with_remote(tmp_path)
    _git(["checkout", "dev"], repo)
    _git(["checkout", "-b", "some-random-branch"], repo)

    result = stabilize(repo, execute=True)

    assert result.status == "skipped"
    assert "does not match a recognized branch-type prefix" in result.message


def test_mid_rebase_needs_manual_resolution_and_touches_nothing(tmp_path):
    repo = make_repo_with_remote(tmp_path)
    _git(["checkout", "dev"], repo)
    _git(["checkout", "-b", "feature/conflict-a"], repo)
    (repo / "shared.txt").write_text("a\n", encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-m", "a"], repo)
    _git(["checkout", "dev"], repo)
    _git(["checkout", "-b", "feature/conflict-b"], repo)
    (repo / "shared.txt").write_text("b\n", encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-m", "b"], repo)
    subprocess.run(["git", "rebase", "feature/conflict-a"], cwd=repo, capture_output=True, text=True)

    result = stabilize(repo, execute=True)

    assert result.status == "needs_manual_resolution"


def test_no_remote_is_skipped_not_failed(tmp_path):
    repo = make_repo_no_remote(tmp_path)
    result = stabilize(repo, execute=True)
    assert result.status == "skipped"
    assert "fetch failed" in result.message


def test_diverged_integration_needs_manual_resolution(tmp_path):
    repo = make_repo_with_remote(tmp_path)
    bare = repo.parent / "origin.git"

    # A second clone pushes a divergent commit to origin/dev.
    other = repo.parent / "other-clone"
    subprocess.run(["git", "clone", str(bare), str(other)], check=True, capture_output=True)
    _git(["checkout", "dev"], other)
    _git(["config", "user.email", "o@example.com"], other)
    _git(["config", "user.name", "O"], other)
    (other / "other.txt").write_text("other", encoding="utf-8")
    _git(["add", "-A"], other)
    _git(["commit", "-m", "diverging commit"], other)
    _git(["push", "origin", "dev"], other)

    # Meanwhile the original checkout also has an un-pushed local commit on dev.
    _git(["checkout", "dev"], repo)
    (repo / "mine.txt").write_text("mine", encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-m", "my own commit"], repo)

    result = stabilize(repo, execute=True)

    assert result.status == "needs_manual_resolution"
    assert "not a fast-forward" in result.message
    assert _git(["rev-parse", "--abbrev-ref", "HEAD"], repo).stdout.strip() == "dev"
