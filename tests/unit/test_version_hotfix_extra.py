"""version.commands.hotfix's still-uncovered paths: the whole action=
"continue" flow (both resuming from the production branch and, after a
*second* conflict on the dev back-merge, resuming from the integration
branch too — lines 77-114, previously entirely untested), abort with
nothing in progress, and the wrong-starting-branch guards on start/
finish/status."""

import subprocess

from version_repo_helper import make_repo

from version.commands.hotfix import cmd_hotfix_finish, cmd_hotfix_start, cmd_hotfix_status
from version.gitops import current_branch, is_merge_in_progress, list_tags


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _write_commit(repo, filename, content, message):
    (repo / filename).write_text(content, encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-m", message], repo)


def test_hotfix_start_requires_being_on_the_production_branch(tmp_path):
    repo = make_repo(tmp_path)
    _git(["checkout", "dev"], repo)
    result = cmd_hotfix_start(repo, "urgent-fix")
    assert result.success is False
    assert "Must run from" in result.message


def test_hotfix_finish_abort_with_nothing_in_progress_fails_cleanly(tmp_path):
    repo = make_repo(tmp_path)
    result = cmd_hotfix_finish(repo, action="abort")
    assert result.success is False
    assert "No hotfix finish in progress" in result.message


def test_hotfix_finish_rejects_starting_from_a_non_hotfix_branch(tmp_path):
    repo = make_repo(tmp_path)
    _git(["checkout", "dev"], repo)
    result = cmd_hotfix_finish(repo)
    assert result.success is False
    assert "Must run from a hotfix/* branch" in result.message


def test_hotfix_status_rejects_a_non_hotfix_branch(tmp_path):
    repo = make_repo(tmp_path)
    result = cmd_hotfix_status(repo)
    assert result.success is False
    assert "Not on a hotfix/* branch" in result.message


def test_hotfix_finish_continue_resumes_prod_merge_then_hits_a_second_conflict_on_dev_backmerge(tmp_path):
    repo = make_repo(tmp_path)

    # A shared file with a common ancestor value on both master and dev.
    _write_commit(repo, "shared.py", "base\n", "master baseline shared")
    _git(["checkout", "dev"], repo)
    _git(["merge", "--ff-only", "master"], repo)
    _git(["checkout", "master"], repo)

    cmd_hotfix_start(repo, "urgent-fix")
    _write_commit(repo, "shared.py", "hotfix\n", "hotfix change")

    _git(["checkout", "master"], repo)
    _write_commit(repo, "shared.py", "master-conflict\n", "master change")

    _git(["checkout", "dev"], repo)
    _write_commit(repo, "shared.py", "dev-conflict\n", "dev change")

    _git(["checkout", "hotfix/urgent-fix"], repo)
    started = cmd_hotfix_finish(repo)
    assert started.success is False
    assert current_branch(repo) == "master"
    assert is_merge_in_progress(repo)

    (repo / "shared.py").write_text("resolved-prod\n", encoding="utf-8")
    _git(["add", "shared.py"], repo)

    continue1 = cmd_hotfix_finish(repo, action="continue")
    assert continue1.success is False, continue1.message
    assert "hotfix finish --continue" in continue1.message
    assert current_branch(repo) == "dev"
    assert is_merge_in_progress(repo)
    assert list_tags(repo, "v0.1.1") == ["v0.1.1"]  # tagged during the prod-merge completion

    (repo / "shared.py").write_text("resolved-dev\n", encoding="utf-8")
    _git(["add", "shared.py"], repo)

    continue2 = cmd_hotfix_finish(repo, action="continue")
    assert continue2.success, continue2.message
    assert continue2.data["tag"] == "v0.1.1"
    assert current_branch(repo) == "dev"
    assert not is_merge_in_progress(repo)

    branches = subprocess.run(["git", "branch"], cwd=repo, capture_output=True, text=True).stdout
    assert "hotfix/urgent-fix" not in branches
