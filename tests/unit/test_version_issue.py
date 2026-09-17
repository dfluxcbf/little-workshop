"""version.commands.issue — M16's little-versions parity gap: standalone
issue<->branch linking (`lvx issue set/unset/list`'s real behavior),
closed 2026-09-17. Functional parity, not command-surface parity: reuses
branches.py's own `issue_automation` flag rather than a second list."""

import subprocess

from version_repo_helper import make_repo

from version.commands.issue import cmd_issue_list, cmd_issue_set, cmd_issue_unset
from version.configstore import get_issue_link


def _git(args: list[str], cwd) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def test_issue_set_links_an_explicit_branch(tmp_path):
    repo = make_repo(tmp_path)
    result = cmd_issue_set(repo, 42, branch="feature/widgets")
    assert result.success, result.message
    assert result.data == {"branch": "feature/widgets", "issue": 42}
    assert get_issue_link(repo, "feature/widgets") == 42


def test_issue_set_defaults_to_the_current_branch(tmp_path):
    repo = make_repo(tmp_path)
    _git(["checkout", "-b", "feature/widgets"], repo)
    result = cmd_issue_set(repo, 7)
    assert result.success, result.message
    assert result.data["branch"] == "feature/widgets"
    assert get_issue_link(repo, "feature/widgets") == 7


def test_issue_set_rejects_a_non_linkable_branch_type(tmp_path):
    repo = make_repo(tmp_path)
    result = cmd_issue_set(repo, 1, branch="support/1.0")
    assert result.success is False
    assert "does not support issue linkage" in result.message
    assert get_issue_link(repo, "support/1.0") is None


def test_issue_set_rejects_an_unparseable_branch_name(tmp_path):
    repo = make_repo(tmp_path)
    result = cmd_issue_set(repo, 1, branch="not-a-real-branch-type/x")
    assert result.success is False


def test_issue_set_allows_every_issue_automation_branch_type(tmp_path):
    repo = make_repo(tmp_path)
    for branch in ("feature/a", "improvement/b", "hotfix/c"):
        result = cmd_issue_set(repo, 1, branch=branch)
        assert result.success, (branch, result.message)


def test_issue_unset_removes_an_existing_link(tmp_path):
    repo = make_repo(tmp_path)
    cmd_issue_set(repo, 42, branch="feature/widgets")
    result = cmd_issue_unset(repo, branch="feature/widgets")
    assert result.success, result.message
    assert result.data == {"branch": "feature/widgets", "removed_issue": 42}
    assert get_issue_link(repo, "feature/widgets") is None


def test_issue_unset_defaults_to_the_current_branch(tmp_path):
    repo = make_repo(tmp_path)
    _git(["checkout", "-b", "feature/widgets"], repo)
    cmd_issue_set(repo, 42)
    result = cmd_issue_unset(repo)
    assert result.success, result.message
    assert get_issue_link(repo, "feature/widgets") is None


def test_issue_unset_fails_cleanly_when_nothing_is_linked(tmp_path):
    repo = make_repo(tmp_path)
    result = cmd_issue_unset(repo, branch="feature/never-linked")
    assert result.success is False
    assert "no issue linked" in result.message


def test_issue_list_is_empty_by_default(tmp_path):
    repo = make_repo(tmp_path)
    result = cmd_issue_list(repo)
    assert result.success
    assert result.data == {"links": {}}


def test_issue_list_reflects_every_link(tmp_path):
    repo = make_repo(tmp_path)
    cmd_issue_set(repo, 42, branch="feature/widgets")
    cmd_issue_set(repo, 7, branch="hotfix/urgent")
    result = cmd_issue_list(repo)
    assert result.data == {"links": {"feature/widgets": 42, "hotfix/urgent": 7}}
