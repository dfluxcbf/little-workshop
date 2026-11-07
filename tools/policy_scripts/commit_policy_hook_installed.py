"""Policy `commit-policy-hook-installed` (version pillar; gates branch_finish): the project has the
commit-msg git hook little-workshop installs: .githooks/commit-msg runs `lws policy scan-secrets`, a
local scan that refuses a commit adding a secret, and git's core.hooksPath points at .githooks.

A secret in git history stays there for good, so it is refused at the commit, with the reason on
screen, rather than at a branch finish much later. Current repository state only."""

import os
import subprocess

import pytest

PROJECT = os.environ.get("POLICY_PROJECT_DIR", "/project")
FIX = (
    "Fix: run `lws version install-hooks --project-id <this project>` in the checkout (it writes "
    ".githooks/commit-msg and sets core.hooksPath), then commit .githooks/."
)


def git(*args):
    return subprocess.run(["git", "-c", "safe.directory=*", "-C", PROJECT, *args], capture_output=True, text=True)


def test_commit_msg_policy_hook_installed():
    if git("rev-parse", "--git-dir").returncode != 0:
        pytest.fail(f"{PROJECT} is not a git checkout")
    hook = os.path.join(PROJECT, ".githooks", "commit-msg")
    assert os.path.isfile(hook), f".githooks/commit-msg is missing, so no commit is scanned for secrets. {FIX}"
    with open(hook, encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    assert "lws policy scan-secrets" in text, (
        f".githooks/commit-msg exists but does not run `lws policy scan-secrets`. {FIX}"
    )
    hooks_path = git("config", "--get", "core.hooksPath").stdout.strip()
    assert hooks_path.rstrip("/").endswith(".githooks"), (
        f"core.hooksPath is {hooks_path or 'unset'!r}, so git never runs .githooks/commit-msg. {FIX}"
    )
