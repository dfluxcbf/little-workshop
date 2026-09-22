import os
import subprocess

from version_repo_helper import make_repo

from version.commands.sync_version_files import cmd_sync_version_files, install_pre_commit_hook
from version.commands.topic_branch import cmd_topic_start
from version.gitops import current_branch
from version.workflows import read_version


def _git(args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _write(repo, filename, content):
    (repo / filename).write_text(content, encoding="utf-8")
    _git(["add", "-A"], repo)


def test_first_commit_on_a_working_branch_gets_next_patch_dev_1(tmp_path):
    repo = make_repo(tmp_path)  # VERSION 0.1.0 on master/dev
    _git(["checkout", "dev"], repo)
    cmd_topic_start(repo, prefix="feature", name="widgets")

    _write(repo, "src.py", "x = 1\n")
    result = cmd_sync_version_files(repo)

    assert result.success, result.message
    assert result.data["version"] == "0.1.1-dev.1"
    assert read_version(repo).__str__() == "0.1.1-dev.1"
    # VERSION was staged by the sync itself.
    assert "VERSION" in _git(["diff", "--cached", "--name-only"], repo).stdout


def test_second_commit_on_the_same_branch_bumps_the_dev_counter(tmp_path):
    repo = make_repo(tmp_path)
    _git(["checkout", "dev"], repo)
    cmd_topic_start(repo, prefix="feature", name="widgets")

    _write(repo, "a.py", "a = 1\n")
    cmd_sync_version_files(repo)
    _git(["commit", "-m", "feat: a"], repo)

    _write(repo, "b.py", "b = 1\n")
    result = cmd_sync_version_files(repo)

    assert result.data["version"] == "0.1.1-dev.2"


def test_no_op_on_dev_master_and_lvx_managed_prefixes(tmp_path):
    repo = make_repo(tmp_path)

    def to_hotfix():
        _git(["checkout", "master"], repo)
        _git(["checkout", "-b", "hotfix/x"], repo)

    for branch, setup in (
        ("dev", lambda: _git(["checkout", "dev"], repo)),
        ("master", lambda: _git(["checkout", "master"], repo)),
        ("release/1.0.0", lambda: _git(["checkout", "-b", "release/1.0.0"], repo)),
        ("hotfix/x", to_hotfix),
    ):
        setup()
        assert current_branch(repo) == branch
        result = cmd_sync_version_files(repo)
        assert result.success and result.data == {"skipped": True}


def test_version_hooks_run_and_their_changes_get_staged(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "tools").mkdir()
    hook_script = repo / "tools" / "bump.py"
    hook_script.write_text(
        "import sys, pathlib\n"
        "pathlib.Path('propagated.txt').write_text(sys.argv[1])\n",
        encoding="utf-8",
    )
    import json
    cfg_path = repo / ".lvx" / "config.json"
    cfg = json.loads(cfg_path.read_text())
    cfg["version_hooks"] = ["tools/bump.py"]
    cfg_path.write_text(json.dumps(cfg))
    _git(["add", "-A"], repo)
    _git(["commit", "-m", "chore: add version hook"], repo)
    _git(["checkout", "dev"], repo)
    _git(["merge", "--ff-only", "master"], repo)
    cmd_topic_start(repo, prefix="feature", name="widgets")

    _write(repo, "src.py", "x = 1\n")
    result = cmd_sync_version_files(repo)

    assert result.success, result.message
    assert (repo / "propagated.txt").read_text() == "0.1.1-dev.1"
    staged = _git(["diff", "--cached", "--name-only"], repo).stdout
    assert "propagated.txt" in staged
    assert not result.data["hook_errors"]


def test_install_pre_commit_hook_writes_executable_and_sets_hooks_path(tmp_path):
    repo = make_repo(tmp_path)

    path = install_pre_commit_hook(repo, "little-widget")

    assert path == repo / ".githooks" / "pre-commit"
    assert path.is_file()
    assert os.access(path, os.X_OK)
    assert "little-widget" in path.read_text()
    hooks_path = _git(["config", "core.hooksPath"], repo).stdout.strip()
    assert hooks_path == ".githooks"


def test_pre_commit_hook_never_blocks_a_commit_when_lws_is_unavailable(tmp_path):
    repo = make_repo(tmp_path)
    install_pre_commit_hook(repo, "little-widget")
    # No `lws` on PATH in this test env -- the hook's own guard must no-op,
    # not fail the commit.
    env = dict(os.environ)
    env["PATH"] = "/usr/bin:/bin"  # a plain PATH almost certainly missing `lws`

    _write(repo, "c.py", "c = 1\n")
    result = subprocess.run(["git", "commit", "-m", "feat: c"], cwd=repo, env=env, capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
