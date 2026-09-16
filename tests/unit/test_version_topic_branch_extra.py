"""version.commands.topic_branch's remaining gaps: abort/continue with
nothing in progress, a *second* rebase conflict surfacing during
--continue (only a single-conflict scenario was covered before), and the
wrong-branch guards on finish/status."""

import subprocess

import pytest
from version_repo_helper import make_repo

from version.commands.topic_branch import cmd_topic_finish, cmd_topic_start, cmd_topic_status
from version.gitops import current_branch, is_rebase_in_progress


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _write_commit(repo, filename, content, message):
    (repo / filename).write_text(content, encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-m", message], repo)


def test_topic_finish_abort_with_nothing_in_progress_fails_cleanly(tmp_path):
    repo = make_repo(tmp_path)
    _git(["checkout", "dev"], repo)
    cmd_topic_start(repo, prefix="feature", name="widgets")

    result = cmd_topic_finish(repo, prefix="feature", commit_label="feat", action="abort")
    assert result.success is False
    assert "No finish in progress to abort" in result.message


def test_topic_finish_continue_with_nothing_in_progress_fails_cleanly(tmp_path):
    repo = make_repo(tmp_path)
    _git(["checkout", "dev"], repo)
    cmd_topic_start(repo, prefix="feature", name="widgets")

    result = cmd_topic_finish(repo, prefix="feature", commit_label="feat", action="continue")
    assert result.success is False
    assert "No finish in progress" in result.message


def test_topic_finish_rejects_starting_from_a_non_prefixed_branch(tmp_path):
    repo = make_repo(tmp_path)
    _git(["checkout", "dev"], repo)
    result = cmd_topic_finish(repo, prefix="feature", commit_label="feat")
    assert result.success is False
    assert "Must run from a feature/* branch" in result.message


def test_topic_status_rejects_a_non_prefixed_branch(tmp_path):
    repo = make_repo(tmp_path)
    result = cmd_topic_status(repo, prefix="feature")
    assert result.success is False
    assert "Not on a feature/* branch" in result.message


def test_topic_finish_continue_raises_on_a_second_rebase_conflict(tmp_path):
    repo = make_repo(tmp_path)
    _git(["checkout", "dev"], repo)
    _write_commit(repo, "shared.py", "base\n", "dev baseline")

    cmd_topic_start(repo, prefix="feature", name="widgets")
    _write_commit(repo, "shared.py", "feature-1\n", "feature change 1")
    _write_commit(repo, "shared.py", "feature-2\n", "feature change 2")

    _git(["checkout", "dev"], repo)
    _write_commit(repo, "shared.py", "dev-conflict\n", "conflicting dev change")
    _git(["checkout", "feature/widgets"], repo)

    started = cmd_topic_finish(repo, prefix="feature", commit_label="feat")
    assert started.success is False
    assert is_rebase_in_progress(repo)

    # Resolve the first conflict to a value that does *not* match commit 2's
    # expected base ("feature-1") — so commit 2's diff fails to apply
    # cleanly and conflicts again immediately on `rebase --continue`.
    (repo / "shared.py").write_text("resolved-1\n", encoding="utf-8")
    _git(["add", "shared.py"], repo)

    continued = cmd_topic_finish(repo, prefix="feature", commit_label="feat", action="continue")
    assert continued.success is False
    assert "Further rebase conflicts remain" in continued.message
    assert is_rebase_in_progress(repo)
