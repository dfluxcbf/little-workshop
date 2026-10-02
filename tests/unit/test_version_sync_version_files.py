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
    """End to end through git itself: the installed hook calls `lws`, here the
    real CLI entry point (no daemon running), and the commit that lands
    carries the new VERSION and the hook-propagated file. The CLI must do the
    sync itself, synchronously: routed through a daemon Job it sat behind the
    project's queue (held by the agent task making the commit) and landed only
    after the commit, leaving a dirty tree that blocked the task's finish."""
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
        "from cli.main import main\n"
        "sys.exit(main())\n",
        encoding="utf-8",
    )
    lws.chmod(0o755)
    # An unreachable daemon: a sync that still went through a Job would fail here.
    env = dict(os.environ, PATH=f"{bin_dir}:/usr/bin:/bin", XDG_STATE_HOME=str(tmp_path / "no-daemon"))

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


# --- REQ pending (the pre-commit sync numbers an amend from HEAD's parent and stages only its own
# changes) -------------------------------------------------------------------------------------

def test_an_amend_is_numbered_from_heads_parent(tmp_path):
    repo = make_repo(tmp_path)
    _git(["checkout", "dev"], repo)
    cmd_topic_start(repo, prefix="feature", name="widgets")
    _write(repo, "a.py", "a = 1\n")
    cmd_sync_version_files(repo)
    _git(["commit", "-m", "feat: a"], repo)

    result = cmd_sync_version_files(repo, amend=True)  # amending the branch's first commit
    assert result.data["version"] == "0.1.1-dev.1" and result.data["amend"]
    _git(["commit", "--amend", "--no-verify", "-m", "feat: a, amended"], repo)

    _write(repo, "b.py", "b = 1\n")
    assert cmd_sync_version_files(repo).data["version"] == "0.1.1-dev.2"
    _git(["commit", "-m", "feat: b"], repo)
    assert cmd_sync_version_files(repo, amend=True).data["version"] == "0.1.1-dev.2"


def test_an_amend_with_nothing_of_the_branch_to_replace_counts_as_a_first_commit(tmp_path):
    repo = make_repo(tmp_path)
    _git(["checkout", "dev"], repo)
    cmd_topic_start(repo, prefix="feature", name="widgets")
    _write(repo, "a.py", "a = 1\n")
    result = cmd_sync_version_files(repo, amend=True)
    assert result.data["version"] == "0.1.1-dev.1" and not result.data["amend"]


def test_the_sync_stages_only_what_it_changed(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "notes.md").write_text("tracked\n", encoding="utf-8")
    _add_version_hook(
        repo,
        "import sys, pathlib\n"
        "pathlib.Path('propagated.txt').write_text(sys.argv[1])\n",
    )
    cmd_topic_start(repo, prefix="feature", name="widgets")

    _write(repo, "src.py", "x = 1\n")  # staged by the author
    (repo / "notes.md").write_text("an unrelated edit, not for this commit\n", encoding="utf-8")
    (repo / "scratch.txt").write_text("untracked work\n", encoding="utf-8")
    result = cmd_sync_version_files(repo)

    staged = set(_git(["diff", "--cached", "--name-only"], repo).stdout.split())
    assert staged == {"VERSION", "propagated.txt", "src.py"}
    assert result.data["files_touched"] == ["VERSION", "propagated.txt"]
    assert _git(["diff", "--name-only"], repo).stdout.split() == ["notes.md"]
    assert "scratch.txt" in _git(["ls-files", "--others", "--exclude-standard"], repo).stdout


def test_amending_commit_finds_the_git_process_among_the_ancestors(tmp_path):
    from version.commands import sync_version_files as sync

    table = {30: (20, ["/bin/sh", ".githooks/pre-commit"]), 20: (10, ["/usr/bin/git", "commit", "--amend", "-m", "x"]),
             10: (1, ["bash"])}
    original = sync._process
    try:
        sync._process = table.get
        assert sync.amending_commit(start_pid=30)
        table[20] = (10, ["git", "commit", "-m", "x"])
        assert not sync.amending_commit(start_pid=30)
        assert not sync.amending_commit(start_pid=10)  # no git above: not an amend
    finally:
        sync._process = original


def test_a_real_amend_through_the_installed_hook_keeps_the_branchs_number(tmp_path):
    """End to end through git: the hook runs as git's child, so the sync sees `--amend`."""
    import sys
    from pathlib import Path

    repo = make_repo(tmp_path)
    install_pre_commit_hook(repo, "little-widget")
    _git(["add", "-A"], repo)
    _git(["commit", "--no-verify", "-m", "chore: install hook"], repo)
    _git(["checkout", "dev"], repo)
    _git(["merge", "--ff-only", "master"], repo)
    cmd_topic_start(repo, prefix="feature", name="widgets")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    src_dir = Path(__file__).resolve().parents[2] / "src"
    (bin_dir / "lws").write_text(
        f"#!{sys.executable}\nimport sys\nsys.path.insert(0, {str(src_dir)!r})\n"
        "from cli.main import main\nsys.exit(main())\n", encoding="utf-8")
    (bin_dir / "lws").chmod(0o755)
    env = dict(os.environ, PATH=f"{bin_dir}:/usr/bin:/bin", XDG_STATE_HOME=str(tmp_path / "no-daemon"))

    _write(repo, "src.py", "x = 1\n")
    subprocess.run(["git", "commit", "-m", "feat: x"], cwd=repo, env=env, check=True, capture_output=True)
    assert _git(["show", "HEAD:VERSION"], repo).stdout.strip() == "0.1.1-dev.1"
    done = subprocess.run(["git", "commit", "--amend", "-m", "feat: x REQ-NONE"], cwd=repo, env=env,
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert _git(["show", "HEAD:VERSION"], repo).stdout.strip() == "0.1.1-dev.1"
    assert _git(["status", "--porcelain"], repo).stdout == ""
