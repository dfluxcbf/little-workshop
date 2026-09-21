"""Policy `commit-bumps-version`: HEAD's VERSION must outrank its parent's.

Only the newest commit is judged against its immediate parent, so an old
mistake somewhere in history never makes this fail forever."""

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


def test_head_commit_bumps_the_version_over_its_parent():
    head_text, parent_sha, parent_text = head_and_parent_versions()
    head = parse(head_text)
    assert head is not None, f"HEAD's VERSION {head_text!r} is not <major>.<minor>.<patch>[-dev.N|-rc.N]"
    parent = parse(parent_text)
    if parent is None:
        return  # the parent's malformed VERSION is a past mistake; HEAD being valid is enough
    assert precedence(head) > precedence(parent), (
        f"HEAD's VERSION {head_text.strip()} must be higher than its parent's {parent_text.strip()}"
    )
