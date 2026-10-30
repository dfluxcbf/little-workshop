"""A finished working branch lands on the patch its commits were already
working toward, never one past it. REQ-000000269; the installed hook
never re-bumps it, REQ-000000270."""

import subprocess

import pytest
from version_repo_helper import make_repo

from version.commands.topic_branch import cmd_topic_finish, cmd_topic_start
from version.stages import GitflowVersion
from version.workflows import finished_version

V = GitflowVersion.parse


@pytest.mark.parametrize(
    ("merged", "target_before", "expected"),
    [
        ("0.2.7-dev.3", "0.2.6", "0.2.7"),  # the branch already on the next patch lands on it
        ("0.2.6", "0.2.6", "0.2.7"),  # a branch that never bumped gets the next patch
        ("0.2.7", "0.2.7", "0.2.8"),  # a version conflict resolved to upstream's own version
        ("0.2.7-dev.1", "0.2.8", "0.2.9"),  # a stale -dev line never lands at or below the target
    ],
)
def test_finished_version(merged, target_before, expected):
    assert str(finished_version(V(merged), V(target_before))) == expected


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def test_a_finish_after_dev_suffixed_commits_lands_on_their_patch(tmp_path):
    repo = make_repo(tmp_path)  # VERSION 0.1.0
    _git(["checkout", "dev"], repo)
    cmd_topic_start(repo, prefix="feature", name="widgets")
    for n, content in ((1, "a = 1\n"), (2, "a = 2\n")):
        (repo / "widgets.py").write_text(content, encoding="utf-8")
        (repo / "VERSION").write_text(f"0.1.1-dev.{n}\n", encoding="utf-8")
        _git(["add", "-A"], repo)
        _git(["commit", "-m", f"wip {n}"], repo)

    result = cmd_topic_finish(repo, prefix="feature", commit_label="feat")

    assert result.success, result.message
    assert result.data["new_version"] == "0.1.1"
    assert (repo / "VERSION").read_text().strip() == "0.1.1"


def test_the_installed_pre_commit_hook_does_not_rebump_a_finish(tmp_path, monkeypatch):
    """The finish's own squash commit sits on the integration tip after its
    `reset --soft`, which the hook took for a branch's first commit and
    bumped again. The pillar's git calls skip the repository's hooks."""
    import sys
    from pathlib import Path

    from version.commands.sync_version_files import install_pre_commit_hook

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
        f"#!{sys.executable}\nimport sys\nsys.path.insert(0, {str(src_dir)!r})\nfrom cli.main import main\nsys.exit(main())\n",
        encoding="utf-8",
    )
    (bin_dir / "lws").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "no-daemon"))

    (repo / "widgets.py").write_text("a = 1\n", encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-m", "feat: widgets"], repo)  # through the hook: 0.1.1-dev.1
    assert (repo / "VERSION").read_text().strip() == "0.1.1-dev.1"

    result = cmd_topic_finish(repo, prefix="feature", commit_label="feat")

    assert result.success, result.message
    assert result.data["new_version"] == "0.1.1"
