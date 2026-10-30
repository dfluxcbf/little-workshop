"""Policy `back-merge-no-ff` (version pillar; gates branch_finish): when a release or hotfix
branch is finished, it is merged back into the integration branch with a non-fast-forward
merge.

Judged on history, not on source text (2026-10-02: the earlier source-scanning script matched
the very text that forbids `git merge` and failed for that). little-workshop tags a finished
release or hotfix on the production branch's merge commit and merges it back into the
integration branch with --no-ff, so the newest production tag's commit is in the integration
branch's history but never on its first-parent line; a fast-forward back-merge would put it
there. Only the newest production tag is judged, so an old mistake never fails this forever."""

import json
import os
import re
import subprocess

import pytest

PROJECT = os.environ.get("POLICY_PROJECT_DIR", "/project")
TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
FIX = (
    "Fix: finish releases and hotfixes with workshop_version_release_finish / "
    "workshop_version_hotfix_finish, which merge back with --no-ff; to repair the current state, "
    "merge the production branch into the integration branch with a --no-ff merge commit."
)


def git(*args):
    return subprocess.run(["git", "-c", "safe.directory=*", "-C", PROJECT, *args], capture_output=True, text=True)


def configured_branch(key, candidates):
    """The branch .lvx/config.json names for `key`, else the first candidate that exists."""
    path = os.path.join(PROJECT, ".lvx", "config.json")
    names = list(candidates)
    try:
        with open(path, encoding="utf-8") as fh:
            configured = json.load(fh).get(key)
        if configured:
            names.insert(0, configured)
    except (OSError, ValueError):
        pass
    for name in names:
        for ref in (f"refs/heads/{name}", f"refs/remotes/origin/{name}"):
            if git("rev-parse", "--verify", "-q", ref).returncode == 0:
                return name, ref
    return None, None


def newest_production_tag():
    tags = [t for t in git("tag", "--list").stdout.split() if TAG_RE.match(t)]
    if not tags:
        return None
    return max(tags, key=lambda t: tuple(int(x) for x in TAG_RE.match(t).groups()))


def test_newest_release_was_merged_back_without_fast_forward():
    if git("rev-parse", "--git-dir").returncode != 0:
        pytest.fail(f"{PROJECT} is not a readable git repository")
    tag = newest_production_tag()
    if tag is None:
        pytest.skip("no release or hotfix has been finished yet (no v<major>.<minor>.<patch> tag)")
    integration, ref = configured_branch("integration_branch", ("dev", "develop"))
    if ref is None:
        pytest.skip("no integration branch (dev/develop) to merge back into")
    sha = git("rev-parse", f"{tag}^{{commit}}").stdout.strip()

    reachable = git("merge-base", "--is-ancestor", sha, ref).returncode == 0
    assert reachable, (
        f"The newest release/hotfix {tag} ({sha[:10]}) was never merged back into {integration}. {FIX}"
    )
    first_parent_line = set(git("rev-list", "--first-parent", ref).stdout.split())
    assert sha not in first_parent_line, (
        f"{tag} ({sha[:10]}) is on {integration}'s first-parent line, so it reached {integration} by a "
        f"fast-forward instead of a merge commit. {FIX}"
    )
