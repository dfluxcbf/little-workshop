"""Policy `commit-bumps-version` (version pillar; gates commit, branch_finish): a commit's
VERSION has a higher version precedence than its parent commit's VERSION.

Judges the commit being made when the commit-msg hook runs it (POLICY_COMMIT_DIR holds its
staged VERSION; its parent is the checkout's HEAD), otherwise HEAD against its first parent.
Only that one step is judged, so an old mistake somewhere in history never makes this fail
forever -- including a parent whose VERSION parses fine but is missing the working-branch
-dev.N suffix `branch-commit-dev-suffix` requires (a parent that forgot to bump at all looks,
to plain semver precedence, like a *higher*-ranked stable release than any correctly
-dev-suffixed child ever could be, which would otherwise make this policy permanently
unsatisfiable from that point in history on -- 2026-09-22's little-mindcheck/little-meals
finding)."""

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
    "Fix: on a feature/ or improvement/ branch the pre-commit hook raises VERSION for you (run "
    "`lws version sync-version-files` and stage VERSION if it did not; `git config core.hooksPath` "
    "must be .githooks). Elsewhere, start one (workshop_version_feature_start / _improvement_start) "
    "or raise VERSION yourself before committing."
)


def test_commit_bumps_the_version_over_its_parent():
    judged_text, parent_sha, parent_text = candidate_and_parent_versions()
    judged = parse(judged_text)
    assert judged is not None, (
        f"The commit's VERSION {judged_text!r} is not <major>.<minor>.<patch>[-dev.N|-rc.N]. {FIX}"
    )
    parent = parse(parent_text)
    if parent is None:
        return  # the parent's malformed VERSION is a past mistake; a valid one now is enough
    if parent.kind is None and parent_is_on_integration_line(parent_sha) is False:
        # The parent sits on a working branch (not dev/master/release/hotfix/support)
        # yet carries a bare stable-looking VERSION -- exactly the shape of a past
        # commit that forgot branch-commit-dev-suffix's own -dev.N requirement, not
        # a real stable release to outrank. Judging precedence against it would
        # demand an ever-higher major/minor/patch bump on every single commit from
        # here on, which no branch's own gitflow is asking for. Forgiven, same as an
        # unparseable parent above; a well-formed VERSION is enough.
        return
    assert precedence(judged) > precedence(parent), (
        f"The commit's VERSION {judged_text.strip()} is not higher than its parent's "
        f"{parent_text.strip()} ({parent_sha[:10]}): every commit raises VERSION. {FIX}"
    )
