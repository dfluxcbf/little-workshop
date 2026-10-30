"""Policy `commit-policy-hook-installed` (version pillar; gates branch_finish): the project has
the commit-msg git hook that runs its commit-gated policies on every commit installed:
.githooks/commit-msg calls `lws policy evaluate --gate-point commit`, and git's core.hooksPath
points at .githooks.

The hook is what turns a commit-level rule (commit-bumps-version, commit-cites-requirement,
...) into a refused commit with the reason on screen, for a person or for Claude, instead of
a branch finish held much later. Current repository state only."""

import os
import subprocess

import pytest

PROJECT = os.environ.get("POLICY_PROJECT_DIR", "/project")
FIX = (
    "Fix: run `lws version install-hooks --project-id <this project>` in the checkout (it writes "
    ".githooks/commit-msg next to the pre-commit hook and sets core.hooksPath), then commit .githooks/."
)


def git(*args):
    return subprocess.run(["git", "-c", "safe.directory=*", "-C", PROJECT, *args], capture_output=True, text=True)


def test_commit_msg_policy_hook_installed():
    if git("rev-parse", "--git-dir").returncode != 0:
        pytest.fail(f"{PROJECT} is not a git checkout")
    hook = os.path.join(PROJECT, ".githooks", "commit-msg")
    assert os.path.isfile(hook), f".githooks/commit-msg is missing, so no commit-time policy runs. {FIX}"
    with open(hook, encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    assert "lws policy evaluate" in text and "--gate-point commit" in text, (
        f".githooks/commit-msg exists but does not run `lws policy evaluate --gate-point commit`. {FIX}"
    )
    hooks_path = git("config", "--get", "core.hooksPath").stdout.strip()
    assert hooks_path.rstrip("/").endswith(".githooks"), (
        f"core.hooksPath is {hooks_path or 'unset'!r}, so git never runs .githooks/commit-msg. {FIX}"
    )
