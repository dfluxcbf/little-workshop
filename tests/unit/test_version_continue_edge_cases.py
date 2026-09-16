"""The last few uncovered lines across the version engine: the "merge in
progress but no saved finish state" edge case for both hotfix and release
continue, resuming continue from neither the production nor the
integration branch, `read_version`'s no-VERSION-file default,
`start_integration_branch`'s wrong-branch guard, and `rebase_continue`
raising a plain `GitError` (not `RebaseConflictError`) when there's no
rebase in progress at all."""

import subprocess

import pytest
from version_repo_helper import make_repo

from version.commands.hotfix import cmd_hotfix_finish, cmd_hotfix_start
from version.commands.release import cmd_release_finish, cmd_release_start
from version.commands.topic_branch import cmd_topic_start
from version.gitops import GitError, rebase_continue
from version.workflows import read_version


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _write_commit(repo, filename, content, message):
    (repo / filename).write_text(content, encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-m", message], repo)


def test_hotfix_finish_continue_with_nothing_in_progress_fails_cleanly(tmp_path):
    repo = make_repo(tmp_path)
    result = cmd_hotfix_finish(repo, action="continue")
    assert result.success is False
    assert "No hotfix finish in progress" in result.message


def test_hotfix_finish_continue_with_a_bare_merge_and_no_saved_state(tmp_path):
    repo = make_repo(tmp_path)
    _write_commit(repo, "shared.py", "base\n", "master baseline")
    _git(["checkout", "dev"], repo)
    _git(["merge", "--ff-only", "master"], repo)
    _git(["checkout", "master"], repo)
    _write_commit(repo, "shared.py", "master-side\n", "master change")
    _git(["checkout", "-b", "side-branch", "dev"], repo)
    _write_commit(repo, "shared.py", "side-value\n", "side change")
    _git(["checkout", "master"], repo)
    subprocess.run(["git", "merge", "--no-ff", "side-branch"], cwd=repo, capture_output=True, text=True)

    result = cmd_hotfix_finish(repo, action="continue")
    assert result.success is False
    assert "no hotfix finish state was found" in result.message


def test_release_finish_continue_with_a_bare_merge_and_no_saved_state(tmp_path):
    repo = make_repo(tmp_path)
    _write_commit(repo, "shared.py", "base\n", "master baseline")
    _git(["checkout", "dev"], repo)
    _git(["merge", "--ff-only", "master"], repo)
    _git(["checkout", "master"], repo)
    _write_commit(repo, "shared.py", "master-side\n", "master change")
    _git(["checkout", "-b", "side-branch", "dev"], repo)
    _write_commit(repo, "shared.py", "side-value\n", "side change")
    _git(["checkout", "master"], repo)
    subprocess.run(["git", "merge", "--no-ff", "side-branch"], cwd=repo, capture_output=True, text=True)

    result = cmd_release_finish(repo, action="continue")
    assert result.success is False
    assert "no release finish state was found" in result.message


def test_release_finish_continue_from_neither_prod_nor_integ_fails_cleanly(tmp_path):
    repo = make_repo(tmp_path)
    _git(["checkout", "dev"], repo)
    cmd_release_start(repo)
    _write_commit(repo, "notes.md", "notes\n", "release content")

    started = cmd_release_finish(repo)
    assert started.success, started.message  # no conflict here — completes fully
    # Simulate resuming after the process was interrupted mid-way by
    # re-injecting state (the normal case is a still-open merge; here we
    # exercise the branch-mismatch guard directly).
    from version.configstore import save_release_finish_state

    save_release_finish_state(
        repo, release_branch="release/0.1.0", stable_version="0.1.0", production_branch="master",
        integration_branch="dev", merge_message="rel (0.1.0): merge release/0.1.0", phase="prod_merge",
    )
    _git(["checkout", "-b", "some-other-branch", "dev"], repo)

    result = cmd_release_finish(repo, action="continue")
    assert result.success is False
    assert "Cannot resume release finish from" in result.message


def test_read_version_defaults_when_no_version_file_exists(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "VERSION").unlink()
    _git(["add", "-A"], repo)
    _git(["commit", "-m", "remove VERSION file"], repo)

    version = read_version(repo)
    assert str(version) == "0.1.0"


def test_topic_start_rejects_wrong_starting_branch(tmp_path):
    repo = make_repo(tmp_path)  # left on master by make_repo, not dev
    result = cmd_topic_start(repo, prefix="feature", name="widgets")
    assert result.success is False
    assert "Must run from" in result.message


def test_rebase_continue_raises_plain_git_error_with_no_rebase_in_progress(tmp_path):
    repo = make_repo(tmp_path)
    with pytest.raises(GitError) as exc_info:
        rebase_continue(repo)
    assert "Further rebase conflicts remain" not in str(exc_info.value)


def test_hotfix_finish_continue_from_neither_prod_nor_integ_fails_cleanly(tmp_path):
    repo = make_repo(tmp_path)
    cmd_hotfix_start(repo, "urgent-fix")
    _write_commit(repo, "fix.py", "x = 1\n", "the fix")
    started = cmd_hotfix_finish(repo)
    assert started.success, started.message  # no conflict — completes fully

    from version.configstore import save_hotfix_finish_state

    save_hotfix_finish_state(
        repo, hotfix_branch="hotfix/urgent-fix", production_branch="master", integration_branch="dev",
        merge_message="fix (-1): merge urgent-fix", new_version="0.1.1", phase="prod_merge",
    )
    _git(["checkout", "-b", "some-other-branch", "dev"], repo)

    result = cmd_hotfix_finish(repo, action="continue")
    assert result.success is False
    assert "Cannot resume hotfix finish from" in result.message


def test_release_start_commits_when_the_version_file_actually_changes(tmp_path):
    repo = make_repo(tmp_path)
    _git(["checkout", "dev"], repo)
    (repo / "VERSION").write_text("0.1.0-dev.1\n", encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-m", "simulate a dev-suffixed VERSION file"], repo)

    result = cmd_release_start(repo)
    assert result.success, result.message
    assert (repo / "VERSION").read_text().strip() == "0.1.0"
    log = subprocess.run(["git", "log", "--oneline", "-1"], cwd=repo, capture_output=True, text=True).stdout
    assert "chore: set version to 0.1.0" in log
