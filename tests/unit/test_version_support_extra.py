"""version.commands.support's two remaining gaps: refusing to create a
support branch that already exists, and support_finish's "not on a
support branch" guard."""

import subprocess

from version_repo_helper import make_repo

from version.commands.release import cmd_release_finish, cmd_release_start
from version.commands.support import cmd_support_finish, cmd_support_start


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def test_support_start_refuses_when_branch_already_exists(tmp_path):
    repo = make_repo(tmp_path)
    _git(["checkout", "dev"], repo)
    cmd_release_start(repo)
    cmd_release_finish(repo)

    first = cmd_support_start(repo, "0.1.0")
    assert first.success
    _git(["checkout", "master"], repo)

    second = cmd_support_start(repo, "0.1.0")
    assert second.success is False
    assert "already exists" in second.message


def test_support_finish_rejects_non_support_branch(tmp_path):
    repo = make_repo(tmp_path)
    result = cmd_support_finish(repo)
    assert result.success is False
    assert "Not on a support/* branch" in result.message
