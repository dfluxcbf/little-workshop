import subprocess

from version_repo_helper import make_repo

from version.commands.hotfix import cmd_hotfix_finish, cmd_hotfix_start, cmd_hotfix_status
from version.gitops import current_branch, list_tags


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _write_commit(repo, filename, content, message):
    (repo / filename).write_text(content, encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-m", message], repo)


def test_hotfix_start_creates_branch_from_master(tmp_path):
    repo = make_repo(tmp_path)
    result = cmd_hotfix_start(repo, "urgent-fix")
    assert result.success
    assert current_branch(repo) == "hotfix/urgent-fix"


def test_hotfix_finish_merges_both_branches_and_tags(tmp_path):
    repo = make_repo(tmp_path)
    cmd_hotfix_start(repo, "urgent-fix")
    _write_commit(repo, "fix.py", "x = 1\n", "the fix")

    result = cmd_hotfix_finish(repo)
    assert result.success, result.message
    assert current_branch(repo) == "dev"

    assert list_tags(repo, "v0.1.1") == ["v0.1.1"]
    _git(["checkout", "master"], repo)
    assert (repo / "VERSION").read_text().strip() == "0.1.1"
    assert (repo / "fix.py").exists()

    _git(["checkout", "dev"], repo)
    assert (repo / "fix.py").exists()

    branches = _git_branches(repo)
    assert "hotfix/urgent-fix" not in branches


def _git_branches(repo):
    return subprocess.run(["git", "branch"], cwd=repo, capture_output=True, text=True).stdout


def test_hotfix_status_reports_branch(tmp_path):
    repo = make_repo(tmp_path)
    cmd_hotfix_start(repo, "urgent-fix")
    result = cmd_hotfix_status(repo)
    assert result.success
    assert result.data["branch"] == "hotfix/urgent-fix"


def test_hotfix_finish_conflict_supports_abort(tmp_path):
    repo = make_repo(tmp_path)
    _write_commit(repo, "shared.py", "a = 1\n", "master baseline")
    _git(["checkout", "dev"], repo)
    _git(["merge", "--ff-only", "master"], repo)
    _git(["checkout", "master"], repo)

    cmd_hotfix_start(repo, "urgent-fix")
    _write_commit(repo, "shared.py", "a = 2\n", "hotfix change")

    _git(["checkout", "master"], repo)
    _write_commit(repo, "shared.py", "a = 3\n", "conflicting master change")
    _git(["checkout", "hotfix/urgent-fix"], repo)

    result = cmd_hotfix_finish(repo)
    assert result.success is False

    abort_result = cmd_hotfix_finish(repo, action="abort")
    assert abort_result.success
    assert current_branch(repo) == "hotfix/urgent-fix"
