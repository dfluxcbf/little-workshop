"""Policy `production-tags-on-production-history` (version pillar; gates release): the newest
production tag (v<major>.<minor>.<patch>, no -rc suffix) points at a commit reachable from the
production branch."""

import os
import re
import subprocess

import pytest

PROJECT = os.environ.get("POLICY_PROJECT_DIR", "/project")
TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
BRANCH_CANDIDATES = ["production", "main", "master"]
FIX = (
    "Fix: production tags are written on the production branch by workshop_version_release_finish "
    "/ workshop_version_hotfix_finish; a tag cut elsewhere has to be deleted and the release finished "
    "through little-workshop."
)


def git(*args, check=False):
    return subprocess.run(
        ["git", "-c", "safe.directory=*", "-C", PROJECT, *args],
        capture_output=True,
        text=True,
        check=check,
    )


def ref_exists(ref):
    return git("rev-parse", "--verify", "--quiet", ref + "^{commit}").returncode == 0


def find_production_branch():
    for name in BRANCH_CANDIDATES:
        for ref in (f"refs/heads/{name}", f"refs/remotes/origin/{name}"):
            if ref_exists(ref):
                return ref
    return None


def test_newest_production_tag_reachable_from_production_branch():
    if not os.path.isdir(PROJECT) or git("rev-parse", "--git-dir").returncode != 0:
        pytest.fail(f"{PROJECT} is not a git repository checkout")

    prod = []
    for t in git("tag", "--list").stdout.split():
        m = TAG_RE.match(t)
        if m:
            prod.append((tuple(int(x) for x in m.groups()), t))
    if not prod:
        pytest.skip("no production tags (v<major>.<minor>.<patch>) exist yet")

    prod.sort()
    newest = prod[-1][1]

    branch = find_production_branch()
    assert branch is not None, f"No production branch found (tried {BRANCH_CANDIDATES}, local or origin). {FIX}"

    tag_commit = git("rev-parse", f"refs/tags/{newest}^{{commit}}")
    assert tag_commit.returncode == 0, f"cannot resolve tag {newest} to a commit"
    sha = tag_commit.stdout.strip()

    r = git("merge-base", "--is-ancestor", sha, branch)
    assert r.returncode == 0, f"The newest production tag {newest} ({sha[:10]}) is not reachable from {branch}. {FIX}"
