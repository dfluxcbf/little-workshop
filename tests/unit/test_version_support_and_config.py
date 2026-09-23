import subprocess

from version_repo_helper import make_repo

from version.commands.config import cmd_config_add_hook, cmd_config_list, cmd_config_remove_hook
from version.commands.release import cmd_release_finish, cmd_release_start
from version.commands.support import cmd_support_finish, cmd_support_start
from version.gitops import current_branch


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def test_support_start_requires_existing_production_tag(tmp_path):
    repo = make_repo(tmp_path)
    result = cmd_support_start(repo, "0.1.0")
    assert result.success is False
    assert "does not exist" in result.message


def test_support_start_succeeds_from_a_real_tag(tmp_path):
    repo = make_repo(tmp_path)
    _git(["checkout", "dev"], repo)
    cmd_release_start(repo)
    cmd_release_finish(repo)

    result = cmd_support_start(repo, "0.1.0")
    assert result.success, result.message
    assert current_branch(repo) == "support/0.1.0"


def test_support_finish_is_always_policy_refused(tmp_path):
    repo = make_repo(tmp_path)
    _git(["checkout", "dev"], repo)
    cmd_release_start(repo)
    cmd_release_finish(repo)
    cmd_support_start(repo, "0.1.0")

    result = cmd_support_finish(repo)
    assert result.success is False
    assert "policy-driven" in result.message


def test_config_add_list_and_remove_hook(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "hooks").mkdir()
    (repo / "hooks" / "sync.sh").write_text("#!/bin/sh\n", encoding="utf-8")

    add = cmd_config_add_hook(repo, "hooks/sync.sh")
    assert add.success

    listed = cmd_config_list(repo)
    assert "hooks/sync.sh" in listed.data["config"]["version_hooks"]

    duplicate = cmd_config_add_hook(repo, "hooks/sync.sh")
    assert duplicate.success is False

    remove = cmd_config_remove_hook(repo, "hooks/sync.sh")
    assert remove.success

    missing = cmd_config_remove_hook(repo, "hooks/sync.sh")
    assert missing.success is False


def test_config_add_hook_rejects_unknown_extension(tmp_path):
    repo = make_repo(tmp_path)
    result = cmd_config_add_hook(repo, "hooks/thing.exe")
    assert result.success is False
