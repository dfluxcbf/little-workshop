"""Policy `hotfix-propagates-by-merge` (version pillar; gates branch_finish): a hotfix reaches
the integration branch by merge, never by cherry-pick.

Judged on history, not on source text (2026-10-02: the earlier source-scanning script matched
the very text that forbids cherry-picking and failed for that). A merge brings the production
branch's own commits into the integration branch; a cherry-pick copies them as new commits,
leaving the originals out. So the newest production tag's commit must be in the integration
branch's history, and no integration commit made since may say it was cherry-picked
(`git cherry-pick -x` writes "(cherry picked from commit ...)"). Only the newest production tag
is judged, so an old mistake never fails this forever."""

import json
import os
import re
import subprocess

import pytest

PROJECT = os.environ.get("POLICY_PROJECT_DIR", "/project")
TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
CHERRY_RE = re.compile(r"\(cherry picked from commit ([0-9a-f]{7,40})\)")
FIX = (
    "Fix: finish hotfixes with workshop_version_hotfix_finish, which merges them into the "
    "integration branch; to repair the current state, merge the production branch into the "
    "integration branch (--no-ff) instead of cherry-picking from it."
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


def test_hotfix_propagates_by_merge_not_cherry_pick():
    if git("rev-parse", "--git-dir").returncode != 0:
        pytest.fail(f"{PROJECT} is not a readable git repository")
    tag = newest_production_tag()
    if tag is None:
        pytest.skip("no release or hotfix has been finished yet (no v<major>.<minor>.<patch> tag)")
    integration, ref = configured_branch("integration_branch", ("dev", "develop"))
    if ref is None:
        pytest.skip("no integration branch (dev/develop) to propagate into")
    sha = git("rev-parse", f"{tag}^{{commit}}").stdout.strip()

    assert git("merge-base", "--is-ancestor", sha, ref).returncode == 0, (
        f"The newest production release {tag} ({sha[:10]}) is not in {integration}'s history: its "
        f"changes were never merged into {integration}. {FIX}"
    )
    picked = []
    for line in git("log", "--format=%h %B%x00", f"{sha}..{ref}").stdout.split("\0"):
        match = CHERRY_RE.search(line)
        if match:
            picked.append(f"{line.strip().split()[0]} (from {match.group(1)[:10]})")
    assert not picked, (
        f"Commits on {integration} since {tag} were cherry-picked: {', '.join(picked[:5])}. {FIX}"
    )
