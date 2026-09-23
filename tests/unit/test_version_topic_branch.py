import subprocess

import pytest
from version_repo_helper import make_repo

from version.commands.topic_branch import cmd_topic_finish, cmd_topic_start, cmd_topic_status
from version.gitops import DirtyWorkingTreeError, current_branch, is_rebase_in_progress


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _write_commit(repo, filename, content, message):
    (repo / filename).write_text(content, encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-m", message], repo)


def test_feature_start_creates_branch_from_dev(tmp_path):
    repo = make_repo(tmp_path)
    _git(["checkout", "dev"], repo)

    result = cmd_topic_start(repo, prefix="feature", name="widgets")
    assert result.success
    assert current_branch(repo) == "feature/widgets"


def test_feature_start_requires_clean_tree(tmp_path):
    repo = make_repo(tmp_path)
    _git(["checkout", "dev"], repo)
    (repo / "dirty.txt").write_text("x", encoding="utf-8")

    with pytest.raises(DirtyWorkingTreeError):
        cmd_topic_start(repo, prefix="feature", name="widgets")


def test_feature_finish_squashes_merges_and_bumps_patch(tmp_path):
    repo = make_repo(tmp_path)
    _git(["checkout", "dev"], repo)
    cmd_topic_start(repo, prefix="feature", name="widgets")

    _write_commit(repo, "widgets.py", "a = 1\n", "wip 1")
    _write_commit(repo, "widgets.py", "a = 2\n", "wip 2")

    result = cmd_topic_finish(repo, prefix="feature", commit_label="feat")
    assert result.success, result.message
    assert current_branch(repo) == "dev"
    assert (repo / "VERSION").read_text().strip() == "0.1.1"
    assert (repo / "widgets.py").exists()
    branches = subprocess.run(["git", "branch"], cwd=repo, capture_output=True, text=True).stdout
    assert "feature/widgets" not in branches


def test_feature_finish_status_reports_branch(tmp_path):
    repo = make_repo(tmp_path)
    _git(["checkout", "dev"], repo)
    cmd_topic_start(repo, prefix="feature", name="widgets")

    result = cmd_topic_status(repo, prefix="feature")
    assert result.success
    assert result.data["branch"] == "feature/widgets"


def test_feature_finish_conflict_supports_abort(tmp_path):
    repo = make_repo(tmp_path)
    _git(["checkout", "dev"], repo)
    _write_commit(repo, "shared.py", "a = 1\n", "dev baseline")

    cmd_topic_start(repo, prefix="feature", name="widgets")
    _write_commit(repo, "shared.py", "a = 2\n", "feature change")

    _git(["checkout", "dev"], repo)
    _write_commit(repo, "shared.py", "a = 3\n", "conflicting dev change")
    _git(["checkout", "feature/widgets"], repo)

    result = cmd_topic_finish(repo, prefix="feature", commit_label="feat")
    assert result.success is False
    assert is_rebase_in_progress(repo)

    abort_result = cmd_topic_finish(repo, prefix="feature", commit_label="feat", action="abort")
    assert abort_result.success
    assert not is_rebase_in_progress(repo)
    assert current_branch(repo) == "feature/widgets"


def test_feature_finish_conflict_supports_continue(tmp_path):
    repo = make_repo(tmp_path)
    _git(["checkout", "dev"], repo)
    _write_commit(repo, "shared.py", "a = 1\n", "dev baseline")

    cmd_topic_start(repo, prefix="feature", name="widgets")
    _write_commit(repo, "shared.py", "a = 2\n", "feature change")

    _git(["checkout", "dev"], repo)
    _write_commit(repo, "shared.py", "a = 3\n", "conflicting dev change")
    _git(["checkout", "feature/widgets"], repo)

    result = cmd_topic_finish(repo, prefix="feature", commit_label="feat")
    assert result.success is False
    assert is_rebase_in_progress(repo)

    (repo / "shared.py").write_text("a = 2\n", encoding="utf-8")
    _git(["add", "shared.py"], repo)

    continue_result = cmd_topic_finish(repo, prefix="feature", commit_label="feat", action="continue")
    assert continue_result.success, continue_result.message
    assert current_branch(repo) == "dev"


def test_improvement_prefix_works_identically(tmp_path):
    repo = make_repo(tmp_path)
    _git(["checkout", "dev"], repo)
    start = cmd_topic_start(repo, prefix="improvement", name="cleanup")
    assert start.success
    _write_commit(repo, "cleanup.py", "x = 1\n", "wip")

    finish = cmd_topic_finish(repo, prefix="improvement", commit_label="impr")
    assert finish.success, finish.message
    assert current_branch(repo) == "dev"
