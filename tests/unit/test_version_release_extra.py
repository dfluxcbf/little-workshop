"""version.commands.release's still-uncovered paths: wrong-branch guards
on candidate cut/list/promote/status, bad version strings, dirty-tree
guard on release start, the non-stable-version finalize path, and —
the biggest remaining gap — the whole action="continue" flow including
a second conflict on the dev back-merge (mirroring the hotfix version of
this same scenario, but release's continue path additionally branches on
`_release_finish_post_dev_backmerge`, a function no earlier test called
at all)."""

import subprocess

from version_repo_helper import make_repo

from version.commands.release import (
    cmd_release_candidate_cut,
    cmd_release_candidate_list,
    cmd_release_candidate_promote,
    cmd_release_finish,
    cmd_release_start,
    cmd_release_status,
)
from version.gitops import DirtyWorkingTreeError, current_branch, is_merge_in_progress, list_tags


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _write_commit(repo, filename, content, message):
    (repo / filename).write_text(content, encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-m", message], repo)


def test_release_start_requires_being_on_the_integration_branch(tmp_path):
    repo = make_repo(tmp_path)
    result = cmd_release_start(repo)  # still on master
    assert result.success is False
    assert "Must run from" in result.message


def test_release_start_rejects_a_dirty_tree(tmp_path):
    import pytest

    repo = make_repo(tmp_path)
    _git(["checkout", "dev"], repo)
    (repo / "dirty.txt").write_text("x", encoding="utf-8")
    with pytest.raises(DirtyWorkingTreeError):
        cmd_release_start(repo)


def test_release_start_refuses_when_branch_already_exists(tmp_path):
    repo = make_repo(tmp_path)
    _git(["checkout", "dev"], repo)
    first = cmd_release_start(repo)
    assert first.success
    _git(["checkout", "dev"], repo)

    second = cmd_release_start(repo)
    assert second.success is False
    assert "already exists" in second.message


def test_release_candidate_cut_rejects_non_release_branch(tmp_path):
    repo = make_repo(tmp_path)
    result = cmd_release_candidate_cut(repo)
    assert result.success is False
    assert "Must run from a release/* branch" in result.message


def test_release_candidate_cut_rejects_unparseable_branch_version(tmp_path):
    repo = make_repo(tmp_path)
    _git(["checkout", "-b", "release/not-a-version", "dev"], repo)
    result = cmd_release_candidate_cut(repo)
    assert result.success is False
    assert "Cannot parse Gitflow version" in result.message


def test_release_candidate_list_rejects_non_release_branch(tmp_path):
    repo = make_repo(tmp_path)
    result = cmd_release_candidate_list(repo)
    assert result.success is False
    assert "Must run from a release/* branch" in result.message


def test_release_candidate_promote_rejects_non_release_branch(tmp_path):
    repo = make_repo(tmp_path)
    result = cmd_release_candidate_promote(repo, 1)
    assert result.success is False
    assert "Must run from a release/* branch" in result.message


def test_release_candidate_promote_rejects_unparseable_branch_version(tmp_path):
    repo = make_repo(tmp_path)
    _git(["checkout", "-b", "release/not-a-version", "dev"], repo)
    result = cmd_release_candidate_promote(repo, 1)
    assert result.success is False
    assert "Cannot parse Gitflow version" in result.message


def test_release_status_rejects_non_release_branch(tmp_path):
    repo = make_repo(tmp_path)
    result = cmd_release_status(repo)
    assert result.success is False
    assert "Not on a release/* branch" in result.message


def test_release_finish_abort_with_nothing_in_progress_fails_cleanly(tmp_path):
    repo = make_repo(tmp_path)
    result = cmd_release_finish(repo, action="abort")
    assert result.success is False
    assert "No release finish in progress" in result.message


def test_release_finish_rejects_starting_from_a_non_release_branch(tmp_path):
    repo = make_repo(tmp_path)
    _git(["checkout", "dev"], repo)
    result = cmd_release_finish(repo)
    assert result.success is False
    assert "Must run from a release/* branch" in result.message


def test_release_finish_rejects_unparseable_branch_version(tmp_path):
    repo = make_repo(tmp_path)
    _git(["checkout", "-b", "release/not-a-version", "dev"], repo)
    result = cmd_release_finish(repo)
    assert result.success is False
    assert "Cannot parse Gitflow version" in result.message


def test_release_finish_finalizes_a_non_stable_version_before_merging(tmp_path):
    repo = make_repo(tmp_path)
    _git(["checkout", "dev"], repo)
    cmd_release_start(repo)
    # Simulate a release branch whose VERSION file still carries a
    # pre-release suffix at finish time (current_file_ver.is_stable() is
    # False) — the finish flow must finalize it to the stable form first.
    (repo / "VERSION").write_text("0.1.0-rc.1\n", encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-m", "simulate an rc-suffixed VERSION file"], repo)

    result = cmd_release_finish(repo)
    assert result.success, result.message
    assert result.data["stable_version"] == "0.1.0"


def test_release_finish_continue_resumes_prod_merge_then_hits_a_second_conflict_on_dev_backmerge(tmp_path):
    repo = make_repo(tmp_path)

    _write_commit(repo, "shared.py", "base\n", "master baseline shared")
    _git(["checkout", "dev"], repo)
    _git(["merge", "--ff-only", "master"], repo)

    cmd_release_start(repo)
    _write_commit(repo, "shared.py", "release\n", "release change")

    _git(["checkout", "master"], repo)
    _write_commit(repo, "shared.py", "master-conflict\n", "master change")

    _git(["checkout", "dev"], repo)
    _write_commit(repo, "shared.py", "dev-conflict\n", "dev change")

    _git(["checkout", "release/0.1.0"], repo)
    started = cmd_release_finish(repo)
    assert started.success is False
    assert current_branch(repo) == "master"
    assert is_merge_in_progress(repo)

    (repo / "shared.py").write_text("resolved-prod\n", encoding="utf-8")
    _git(["add", "shared.py"], repo)

    continue1 = cmd_release_finish(repo, action="continue")
    assert continue1.success is False, continue1.message
    assert "release finish --continue" in continue1.message
    assert current_branch(repo) == "dev"
    assert is_merge_in_progress(repo)
    assert list_tags(repo, "v0.1.0") == ["v0.1.0"]  # tagged during the prod-merge completion

    (repo / "shared.py").write_text("resolved-dev\n", encoding="utf-8")
    _git(["add", "shared.py"], repo)

    continue2 = cmd_release_finish(repo, action="continue")
    assert continue2.success, continue2.message
    assert continue2.data["stable_version"] == "0.1.0"
    assert continue2.data["next_version"] == "0.2.1"
    assert current_branch(repo) == "dev"
    assert not is_merge_in_progress(repo)

    branches = subprocess.run(["git", "branch"], cwd=repo, capture_output=True, text=True).stdout
    assert "release/0.1.0" not in branches


def test_release_finish_continue_with_no_merge_and_no_state_fails_cleanly(tmp_path):
    repo = make_repo(tmp_path)
    _git(["checkout", "dev"], repo)
    result = cmd_release_finish(repo, action="continue")
    assert result.success is False
    assert "No release finish in progress" in result.message
