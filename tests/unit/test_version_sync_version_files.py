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


def test_an_improvement_branch_is_synced_like_a_feature_branch(tmp_path):
    repo = make_repo(tmp_path)
    _git(["checkout", "dev"], repo)
    cmd_topic_start(repo, prefix="improvement", name="faster")

    _write(repo, "src.py", "x = 1\n")
    result = cmd_sync_version_files(repo)

    assert result.success, result.message
    assert result.data["version"] == "0.1.1-dev.1"
    assert "VERSION" in _git(["diff", "--cached", "--name-only"], repo).stdout


def test_no_op_on_a_support_branch(tmp_path):
    repo = make_repo(tmp_path)
    _git(["checkout", "-b", "support/0.1"], repo)

    result = cmd_sync_version_files(repo)

    assert result.success and result.data == {"skipped": True}
    assert str(read_version(repo)) == "0.1.0"


def _add_version_hook(repo, script):
    import json
    (repo / "tools").mkdir()
    (repo / "tools" / "bump.py").write_text(script, encoding="utf-8")
    cfg_path = repo / ".lvx" / "config.json"
    cfg = json.loads(cfg_path.read_text())
    cfg["version_hooks"] = ["tools/bump.py"]
    cfg_path.write_text(json.dumps(cfg))
    _git(["add", "-A"], repo)
    _git(["commit", "-m", "chore: add version hook"], repo)
    _git(["checkout", "dev"], repo)
    _git(["merge", "--ff-only", "master"], repo)


def test_a_hook_edit_to_an_already_tracked_file_is_staged(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "pyproject.toml").write_text('version = "0.1.0"\n', encoding="utf-8")
    _add_version_hook(
        repo,
        "import sys, pathlib\n"
        "pathlib.Path('pyproject.toml').write_text(f'version = \"{sys.argv[1]}\"\\n')\n",
    )
    cmd_topic_start(repo, prefix="feature", name="widgets")

    _write(repo, "src.py", "x = 1\n")
    result = cmd_sync_version_files(repo)

    assert result.success, result.message
    assert (repo / "pyproject.toml").read_text() == 'version = "0.1.1-dev.1"\n'
    staged = _git(["diff", "--cached", "--name-only"], repo).stdout.split()
    assert {"VERSION", "pyproject.toml", "src.py"} <= set(staged)
    assert _git(["diff", "--name-only"], repo).stdout == ""  # nothing left unstaged


def test_a_real_commit_through_the_installed_pre_commit_hook_carries_the_synced_version(tmp_path):
    """End to end through git itself: the installed hook calls `lws`, a stand-in
    here that runs the real sync command in-process, and the commit that lands
    carries the new VERSION and the hook-propagated file."""
    import sys
    from pathlib import Path

    repo = make_repo(tmp_path)
    _add_version_hook(
        repo,
        "import sys, pathlib\n"
        "pathlib.Path('propagated.txt').write_text(sys.argv[1])\n",
    )
    install_pre_commit_hook(repo, "little-widget")
    _git(["add", "-A"], repo)
    _git(["commit", "--no-verify", "-m", "chore: install hook"], repo)
    cmd_topic_start(repo, prefix="feature", name="widgets")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    src_dir = Path(__file__).resolve().parents[2] / "src"
    lws = bin_dir / "lws"
    lws.write_text(
        f"#!{sys.executable}\n"
        "import sys, pathlib\n"
        f"sys.path.insert(0, {str(src_dir)!r})\n"
        "assert sys.argv[1:] == ['version', 'sync-version-files', '--project-id', 'little-widget'], sys.argv\n"
        "from version.commands.sync_version_files import cmd_sync_version_files\n"
        "sys.exit(0 if cmd_sync_version_files(pathlib.Path.cwd()).success else 1)\n",
        encoding="utf-8",
    )
    lws.chmod(0o755)
    env = dict(os.environ, PATH=f"{bin_dir}:/usr/bin:/bin")

    _write(repo, "src.py", "x = 1\n")
    result = subprocess.run(["git", "commit", "-m", "feat: x"], cwd=repo, env=env, capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    assert _git(["show", "HEAD:VERSION"], repo).stdout.strip() == "0.1.1-dev.1"
    assert _git(["show", "HEAD:propagated.txt"], repo).stdout == "0.1.1-dev.1"
    assert _git(["status", "--porcelain"], repo).stdout == ""


def test_the_hook_calls_lws_only_on_a_working_branch(tmp_path):
    """A commit on dev, master or release/* never asks for a sync: a sync
    queued behind a Release Job would otherwise run later, on whatever branch
    is checked out by then."""
    repo = make_repo(tmp_path)
    install_pre_commit_hook(repo, "little-widget")
    _git(["add", "-A"], repo)
    _git(["commit", "--no-verify", "-m", "chore: install hook"], repo)
    calls = tmp_path / "lws-calls"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "lws").write_text(f"#!/bin/sh\necho \"$(git rev-parse --abbrev-ref HEAD)\" >> {calls}\n", encoding="utf-8")
    (bin_dir / "lws").chmod(0o755)
    env = dict(os.environ, PATH=f"{bin_dir}:/usr/bin:/bin")

    for branch in ("dev", "release/9.9.9", "improvement/x", "feature/y"):
        _git(["checkout", "-B", branch, "master"], repo)
        (repo / f"{branch.replace('/', '-')}.txt").write_text("x\n", encoding="utf-8")
        _git(["add", "-A"], repo)
        subprocess.run(["git", "commit", "-m", f"on {branch}"], cwd=repo, env=env, check=True, capture_output=True)

    assert calls.read_text().split() == ["improvement/x", "feature/y"]
