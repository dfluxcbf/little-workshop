"""Policy `commit-cites-requirement` (version pillar; gates commit, branch_finish): a commit
that changes the project's source -- a file under src/ or, in a Unity project (one with
ProjectSettings/ProjectVersion.txt), under Assets/, Packages/, ProjectSettings/ or tools/ --
cites the requirements it affects (REQ-nnnnnnnnn) or, if it affects none, REQ-NONE (any letter
case) -- never both.

Judges the commit being made when the commit-msg hook runs it (POLICY_COMMIT_DIR holds its
message and changed paths), otherwise HEAD. One commit is judged, never the whole history,
so an old commit never fails this forever. REQ-NONE declares that no requirement is affected
(a refactor, a typo fix, a version bump a hook wrote into src/); little-workshop's own
bookkeeping commits cite it."""

import os
import re
import subprocess

import pytest

PROJECT = os.environ.get("POLICY_PROJECT_DIR", "/project")
COMMIT_DIR = os.environ.get("POLICY_COMMIT_DIR")
UNITY_ROOTS = ("Assets/", "Packages/", "ProjectSettings/", "tools/")
REQ_RE = re.compile(r"(?<![A-Za-z0-9-])REQ-\d{9}(?!\d)")
NONE_RE = re.compile(r"(?<![A-Za-z0-9-])REQ-NONE(?![A-Za-z0-9])", re.IGNORECASE)
FIX = (
    "Fix: add to the commit message the id of every requirement the change implements "
    "(REQ-nnnnnnnnn; `lws requirements list --project-id <project>` lists them), or REQ-NONE "
    "when it affects no requirement."
)


def git(*args):
    return subprocess.run(["git", "-c", "safe.directory=*", "-C", PROJECT, *args], capture_output=True, text=True)


def _pending(name):
    path = os.path.join(COMMIT_DIR, name)
    if not os.path.isfile(path):
        return ""
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _commit():
    """(message, changed paths) of the commit under judgment."""
    if COMMIT_DIR:
        return _pending("message"), [line for line in _pending("files").splitlines() if line.strip()]
    probe = git("rev-parse", "--git-dir")
    if probe.returncode != 0:
        pytest.fail(f"{PROJECT} is not a readable git repository: {probe.stderr.strip()}")
    msg = git("show", "-s", "--format=%B", "HEAD")
    assert msg.returncode == 0, f"git show failed for HEAD: {msg.stderr.strip()}"
    touched = git("diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD")
    if touched.returncode != 0:
        # No parent (a repository's very first commit): its whole tree.
        touched = git("show", "--no-commit-id", "--name-only", "--format=", "-r", "HEAD")
    return msg.stdout, [line for line in touched.stdout.splitlines() if line.strip()]


def source_roots():
    """Where the project's source lives: src/, plus a Unity project's own folders."""
    if os.path.isfile(os.path.join(PROJECT, "ProjectSettings", "ProjectVersion.txt")):
        return ("src/",) + UNITY_ROOTS
    return ("src/",)


def test_commit_touching_src_cites_requirement_id():
    message, files = _commit()
    cites_req = bool(REQ_RE.search(message))
    cites_none = bool(NONE_RE.search(message))
    assert not (cites_req and cites_none), (
        "The commit message cites REQ-NONE next to a requirement id, but REQ-NONE means no "
        "requirement is affected. Fix: keep the requirement ids and drop REQ-NONE (or the reverse)."
        f"\nMessage: {message.strip()!r}"
    )
    roots = source_roots()
    src = [f for f in files if f.startswith(roots)]
    if not src:
        pytest.skip(f"the commit changes nothing under {', '.join(roots)}")
    assert cites_req or cites_none, (
        f"The commit changes {len(src)} source file(s) ({', '.join(src[:3])}"
        f"{', ...' if len(src) > 3 else ''}) but its message cites no requirement. {FIX}"
        f"\nMessage: {message.strip()!r}"
    )
