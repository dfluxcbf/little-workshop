"""Policy `branch-commit-dev-suffix`: a commit on a working branch carries `<next patch>-dev.N`.

N is 1 for the branch's first commit and one more than its parent's N after
that. Only the newest commit is judged against its immediate parent, so an
old mistake somewhere in history never makes this fail forever."""

# --- shared helpers (identical in both version-policy scripts) ---
import os
import re
import subprocess
from collections import namedtuple

import pytest

PROJECT = os.environ.get("POLICY_PROJECT_DIR", "/project")
INTEGRATION_BRANCHES = ("dev", "develop", "master", "main")
LVX_DEFINED_PREFIXES = ("release/", "hotfix/", "support/")
VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-(dev|rc)\.(\d+))?$")
V = namedtuple("V", "major minor patch kind n")


def git(*args):
    return subprocess.run(
        ["git", "-c", "safe.directory=*", "-C", PROJECT, *args], capture_output=True, text=True,
    )


def parse(text):
    match = VERSION_RE.match((text or "").strip())
    if not match:
        return None
    major, minor, patch, kind, n = match.groups()
    return V(int(major), int(minor), int(patch), kind, int(n) if n else None)


def precedence(v):
    # A stable release outranks its own pre-releases; dev < rc; then by counter.
    return (v.major, v.minor, v.patch, 1 if v.kind is None else 0, v.kind or "", v.n or 0)


def version_at(sha):
    shown = git("show", f"{sha}:VERSION")
    return shown.stdout.strip() if shown.returncode == 0 else None


def head_and_parent_versions():
    """(head_version_text, parent_sha, parent_version_text); skips when there is nothing to compare."""
    listed = git("rev-list", "--parents", "-n", "1", "HEAD")
    # A repository git cannot read is a failure, never "nothing to compare".
    assert listed.returncode == 0, f"cannot read the repository's history: {listed.stderr.strip()}"
    line = listed.stdout.split()
    if len(line) < 2:
        pytest.skip("HEAD has no parent commit")
    head_version = version_at("HEAD")
    assert head_version is not None, "HEAD has no VERSION file at the repository root"
    parent_version = version_at(line[1])
    if parent_version is None:
        pytest.skip("the parent commit has no VERSION file to compare against")
    return head_version, line[1], parent_version
# --- end shared helpers ---


def current_branch():
    result = git("symbolic-ref", "--short", "-q", "HEAD")
    return result.stdout.strip() if result.returncode == 0 else None


def parent_is_on_integration_line(parent_sha):
    """True/False, or None when no integration branch ref exists to ask."""
    known = False
    for name in INTEGRATION_BRANCHES:
        for ref in (f"refs/heads/{name}", f"refs/remotes/origin/{name}"):
            if git("rev-parse", "--verify", "-q", ref).returncode != 0:
                continue
            known = True
            if git("merge-base", "--is-ancestor", parent_sha, ref).returncode == 0:
                return True
    return False if known else None


def test_working_branch_commit_carries_the_next_patch_dev_suffix():
    readable = git("rev-parse", "--git-dir")
    assert readable.returncode == 0, f"cannot read the repository: {readable.stderr.strip()}"
    branch = current_branch()
    if branch is None:
        pytest.skip("detached HEAD: no branch to judge")
    if branch in INTEGRATION_BRANCHES or branch.startswith(LVX_DEFINED_PREFIXES):
        pytest.skip(f"{branch!r} is governed by little-versions' own stage rules")

    head_text, parent_sha, parent_text = head_and_parent_versions()
    head = parse(head_text)
    assert head is not None and head.kind == "dev", (
        f"a commit on {branch!r} must have VERSION <major>.<minor>.<patch>-dev.<N>, got {head_text.strip()!r}"
    )
    parent = parse(parent_text)
    on_integration = parent_is_on_integration_line(parent_sha)

    if on_integration:
        if parent is not None:
            expected = V(parent.major, parent.minor, parent.patch + 1, "dev", 1)
            assert head == expected, (
                f"the first commit on {branch!r} must have VERSION "
                f"{expected.major}.{expected.minor}.{expected.patch}-dev.1, got {head_text.strip()}"
            )
        else:
            assert head.n == 1, f"the first commit on {branch!r} must end in -dev.1, got {head_text.strip()}"
    elif on_integration is False and parent is not None and parent.kind == "dev":
        expected = V(parent.major, parent.minor, parent.patch, "dev", parent.n + 1)
        assert head == expected, (
            f"the next commit after {parent_text.strip()} on {branch!r} must have VERSION "
            f"{expected.major}.{expected.minor}.{expected.patch}-dev.{expected.n}, got {head_text.strip()}"
        )
    # Otherwise the parent's own version was a past mistake (or no integration branch is
    # visible to compare against): HEAD being a well-formed -dev.N is all that is asked.
