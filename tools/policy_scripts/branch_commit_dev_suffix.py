"""Policy `branch-commit-dev-suffix` (version pillar; gates commit, branch_finish): a commit
on a working branch (not master, dev, release/*, hotfix/* or support/*) carries the next
patch version of the commit the branch was cut from, with the suffix -dev.N.

N is 1 for the branch's first commit and one more than its parent's N after that. Judges
the commit being made when the commit-msg hook runs it (POLICY_COMMIT_DIR holds its staged
VERSION; its parent is the checkout's HEAD), otherwise HEAD against its first parent. Only
that one step is judged, so an old mistake somewhere in history never makes this fail
forever."""

# --- shared helpers (identical in both version-policy scripts) ---
import os
import re
import subprocess
from collections import namedtuple

import pytest

PROJECT = os.environ.get("POLICY_PROJECT_DIR", "/project")
COMMIT_DIR = os.environ.get("POLICY_COMMIT_DIR")
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


def candidate_and_parent_versions():
    """(judged_version_text, parent_sha, parent_version_text) for the commit under judgment:
    the pending commit (parent: HEAD) under the commit-msg hook, else HEAD (parent: HEAD^).
    Skips when there is nothing to compare."""
    if COMMIT_DIR:
        head = git("rev-parse", "--verify", "-q", "HEAD")
        if head.returncode != 0:
            pytest.skip("the repository's first commit has no parent to compare against")
        path = os.path.join(COMMIT_DIR, "VERSION")
        assert os.path.isfile(path), (
            "The commit removes VERSION from the repository root. Fix: keep VERSION, with a version "
            "higher than HEAD's."
        )
        with open(path, encoding="utf-8") as fh:
            judged = fh.read().strip()
        parent_sha = head.stdout.strip()
    else:
        listed = git("rev-list", "--parents", "-n", "1", "HEAD")
        # A repository git cannot read is a failure, never "nothing to compare".
        assert listed.returncode == 0, f"cannot read the repository's history: {listed.stderr.strip()}"
        line = listed.stdout.split()
        if len(line) < 2:
            pytest.skip("HEAD has no parent commit")
        judged = version_at("HEAD")
        assert judged is not None, "HEAD has no VERSION file at the repository root. Fix: restore VERSION."
        parent_sha = line[1]
    parent_version = version_at(parent_sha)
    if parent_version is None:
        pytest.skip("the parent commit has no VERSION file to compare against")
    return judged, parent_sha, parent_version


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
# --- end shared helpers ---

FIX = (
    "Fix: let the pre-commit hook write VERSION (`lws version sync-version-files` in this "
    "checkout, then stage VERSION); it computes exactly this value."
)


def current_branch():
    result = git("symbolic-ref", "--short", "-q", "HEAD")
    return result.stdout.strip() if result.returncode == 0 else None


def test_working_branch_commit_carries_the_next_patch_dev_suffix():
    readable = git("rev-parse", "--git-dir")
    assert readable.returncode == 0, f"cannot read the repository: {readable.stderr.strip()}"
    branch = current_branch()
    if branch is None:
        pytest.skip("detached HEAD: no branch to judge")
    if branch in INTEGRATION_BRANCHES or branch.startswith(LVX_DEFINED_PREFIXES):
        pytest.skip(f"{branch!r} is not a working branch: its finish commands write VERSION")
    if not COMMIT_DIR and parent_is_on_integration_line("HEAD"):
        pytest.skip(f"{branch!r} has no commit of its own yet: HEAD is still the integration branch's")

    judged_text, parent_sha, parent_text = candidate_and_parent_versions()
    judged = parse(judged_text)
    assert judged is not None and judged.kind == "dev", (
        f"A commit on {branch!r} must have VERSION <major>.<minor>.<patch>-dev.<N>, got "
        f"{judged_text.strip()!r}. {FIX}"
    )
    parent = parse(parent_text)
    on_integration = parent_is_on_integration_line(parent_sha)

    if on_integration:
        if parent is not None:
            expected = V(parent.major, parent.minor, parent.patch + 1, "dev", 1)
            assert judged == expected, (
                f"The first commit on {branch!r} must have VERSION "
                f"{expected.major}.{expected.minor}.{expected.patch}-dev.1 (the next patch of "
                f"{parent_text.strip()}, where the branch was cut), got {judged_text.strip()}. {FIX}"
            )
        else:
            assert judged.n == 1, (
                f"The first commit on {branch!r} must end in -dev.1, got {judged_text.strip()}. {FIX}"
            )
    elif on_integration is False and parent is not None and parent.kind == "dev":
        expected = V(parent.major, parent.minor, parent.patch, "dev", parent.n + 1)
        assert judged == expected, (
            f"The commit after {parent_text.strip()} on {branch!r} must have VERSION "
            f"{expected.major}.{expected.minor}.{expected.patch}-dev.{expected.n}, got "
            f"{judged_text.strip()}. {FIX}"
        )
    # Otherwise the parent's own version was a past mistake (or no integration branch is
    # visible to compare against): a well-formed -dev.N is all that is asked.
