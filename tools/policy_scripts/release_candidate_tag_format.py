"""Policy `release-candidate-tag-format` (version pillar; gates release): when a release
candidate is cut, it is an annotated tag v<major>.<minor>.<patch>-rc.<N> on the release branch,
where N is 1 for the first candidate of a base version and one more than the previous
candidate's N after that."""

import os
import re
import subprocess

import pytest

PROJECT = os.environ.get("POLICY_PROJECT_DIR", "/project")
FIX = (
    "Fix: release candidates are cut by workshop_version_release_candidate_cut, which writes the "
    "annotated tag on the release branch with the next N; never tag by hand."
)

STRICT_RC = re.compile(r"^v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)-rc\.([1-9]\d*)$")
LOOSE_RC = re.compile(r"rc", re.IGNORECASE)


def git(*args, check=True):
    proc = subprocess.run(
        ["git", "-c", "safe.directory=*", "-C", PROJECT, *args], capture_output=True, text=True,
    )
    if check and proc.returncode != 0:
        pytest.fail(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc


def _tags():
    out = git("for-each-ref", "--format=%(refname:strip=2)|%(objecttype)|%(*objecttype)|%(*objectname)|%(objectname)", "refs/tags").stdout
    tags = []
    for line in out.splitlines():
        if not line.strip():
            continue
        name, otype, target_type, deref, obj = line.split("|")
        tags.append({"name": name, "type": otype, "target_type": target_type, "commit": deref or obj})
    return tags


def _branches_containing(commit):
    out = git("branch", "-a", "--contains", commit, "--format=%(refname:short)", check=False).stdout
    return [b.strip() for b in out.splitlines() if b.strip()]


def _release_branches_exist():
    out = git("for-each-ref", "--format=%(refname:short)", "refs/heads", "refs/remotes").stdout
    return [b for b in out.splitlines() if re.search(r"(^|/)release", b)]


def test_newest_release_candidate_tag():
    """Only the newest RC tag of the current (highest base version) release line is judged
    — ranked by the version numbers its own name encodes, never by tag creation time, which
    is too coarse to order tags cut within the same second — so one old mistake elsewhere in
    a project's tag history never fails this forever (the same "judge the newest transition
    only" rule this project's own commit-bumps-version policy already established)."""
    check = git("rev-parse", "--git-dir", check=False)
    if check.returncode != 0:
        pytest.fail(f"{PROJECT} is not a readable git repository")

    rc_like = [t for t in _tags() if LOOSE_RC.search(t["name"]) and "-rc" in t["name"].lower()]
    if not rc_like:
        pytest.skip("no release candidate has been cut yet (no -rc tags)")

    parsed = []
    for t in rc_like:
        match = STRICT_RC.match(t["name"])
        if match:
            base = tuple(int(x) for x in match.groups()[:3])
            parsed.append((base, int(match.group(4)), t))
    if not parsed:
        pytest.fail(f"No RC-like tag matches v<major>.<minor>.<patch>-rc.<N>: {[t['name'] for t in rc_like]}. {FIX}")

    current_base = max(base for base, _, _ in parsed)
    group = [(n, t) for base, n, t in parsed if base == current_base]
    n, newest = max(group, key=lambda nt: nt[0])

    assert newest["type"] == "tag" and newest["target_type"] == "commit", (
        f"{newest['name']!r} is not an annotated tag (object type {newest['type']!r}). {FIX}"
    )

    release_branches = _release_branches_exist()
    containing = _branches_containing(newest["commit"])
    if release_branches:
        on_release = [b for b in containing if re.search(r"(^|/)release", b)]
        assert on_release, f"{newest['name']!r} is not on any release branch (contained in: {containing or 'no branch'}). {FIX}"
    else:
        assert containing, f"{newest['name']!r} is not reachable from any branch. {FIX}"

    predecessors = [m for m, _ in group if m != n]
    if predecessors:
        predecessor_n = max(predecessors)
        assert n == predecessor_n + 1, (
            f"v{'.'.join(map(str, current_base))}-rc.{n} should follow rc.{predecessor_n} as rc.{predecessor_n + 1}. {FIX}"
        )
    else:
        assert n == 1, f"v{'.'.join(map(str, current_base))}-rc.{n} is the first candidate for its base version and should be rc.1. {FIX}"
