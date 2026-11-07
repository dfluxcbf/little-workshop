"""A finished working branch lands on the next patch, written once by the finish; a branch whose
commits still carry -dev versions lands on their patch, never one past it."""

import subprocess

import pytest
from version_repo_helper import make_repo

from version.commands.topic_branch import cmd_topic_finish, cmd_topic_start
from version.stages import GitflowVersion
from version.workflows import finished_version

V = GitflowVersion.parse


# --- REQ-000000269: a finish lands on the branch's own patch ---------------------------------------

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


# --- REQ-000000485: working branches leave VERSION alone; the finish writes it once -------------

def test_a_branch_that_never_touched_version_lands_on_the_next_patch_in_one_bump(tmp_path):
    repo = make_repo(tmp_path)  # VERSION 0.1.0
    _git(["checkout", "dev"], repo)
    cmd_topic_start(repo, prefix="feature", name="widgets")
    for n in (1, 2):
        (repo / "widgets.py").write_text(f"a = {n}\n", encoding="utf-8")
        _git(["add", "-A"], repo)
        _git(["commit", "-m", f"wip {n}"], repo)
    assert (repo / "VERSION").read_text() == "0.1.0\n"
    before = subprocess.run(["git", "rev-parse", "dev"], cwd=repo, capture_output=True, text=True).stdout.strip()

    result = cmd_topic_finish(repo, prefix="feature", commit_label="feat")

    assert result.success, result.message
    assert result.data["new_version"] == "0.1.1" and (repo / "VERSION").read_text() == "0.1.1\n"
    log = subprocess.run(["git", "log", "--format=%s", f"{before}..dev"], cwd=repo, capture_output=True, text=True).stdout
    assert log.splitlines() == ["chore: bump version to 0.1.1", "feat (-1): widgets"]  # no state commits


# --- REQ-000000270: the pillar's own git calls skip the repository's hooks -----------------------

def test_a_refusing_commit_hook_does_not_stop_the_pillars_own_commits(tmp_path):
    repo = make_repo(tmp_path)
    hooks = repo / ".githooks"
    hooks.mkdir()
    (hooks / "commit-msg").write_text("#!/bin/sh\necho refused >&2\nexit 1\n", encoding="utf-8")
    (hooks / "commit-msg").chmod(0o755)
    _git(["add", "-A"], repo)
    _git(["commit", "--no-verify", "-m", "chore: a hook"], repo)
    _git(["config", "core.hooksPath", ".githooks"], repo)
    _git(["checkout", "dev"], repo)
    _git(["merge", "--ff-only", "master"], repo)
    cmd_topic_start(repo, prefix="feature", name="widgets")
    (repo / "widgets.py").write_text("a = 1\n", encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "--no-verify", "-m", "feat: widgets"], repo)

    result = cmd_topic_finish(repo, prefix="feature", commit_label="feat")

    assert result.success, result.message
    assert result.data["new_version"] == "0.1.1"
