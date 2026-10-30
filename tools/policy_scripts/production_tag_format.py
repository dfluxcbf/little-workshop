"""Policy `production-tag-format` (version pillar; gates release): a production tag is an
annotated tag named v<major>.<minor>.<patch>.

Only the newest production tag is judged, so one old mistake in a project's tag history never
fails this forever."""

import os
import re
import subprocess

import pytest

PROJECT = os.environ.get("POLICY_PROJECT_DIR", "/project")
PRODUCTION_RE = re.compile(r"^v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
# Pre-release tags (release candidates, etc.) are not production tags.
PRERELEASE_RE = re.compile(r"(?i)(^|[-_.+])(rc|alpha|beta|pre|preview|dev|snapshot)[-_.]?\d*")
FIX = (
    "Fix: production tags are written by workshop_version_release_finish / "
    "workshop_version_hotfix_finish as annotated v<major>.<minor>.<patch> tags; never tag by hand."
)


def _git(*args):
    return subprocess.run(
        ["git", "-c", "safe.directory=*", "-C", PROJECT, *args],
        capture_output=True,
        text=True,
    )


def _tags():
    res = _git("for-each-ref", "--format=%(refname:strip=2) %(objecttype)", "refs/tags")
    assert res.returncode == 0, f"git for-each-ref failed: {res.stderr}"
    tags = []
    for line in res.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        name, objtype = line.rsplit(" ", 1)
        tags.append((name, objtype))
    return tags


def _semver_key(name):
    return tuple(int(x) for x in PRODUCTION_RE.match(name).groups())


def test_newest_production_tag_is_an_annotated_semver_tag():
    inside = _git("rev-parse", "--is-inside-work-tree")
    bare = _git("rev-parse", "--is-bare-repository")
    assert inside.returncode == 0 or bare.returncode == 0, f"{PROJECT} is not a git repository: {inside.stderr}"

    tags = _tags()
    production = [(n, t) for n, t in tags if not PRERELEASE_RE.search(n) and PRODUCTION_RE.match(n)]
    malformed = [n for n, t in tags if not PRERELEASE_RE.search(n) and not PRODUCTION_RE.match(n)]

    if not production:
        if malformed:
            pytest.fail(f"No well-formed production tag exists; malformed candidates: {malformed}. {FIX}")
        pytest.skip("no production tags exist yet")

    name, objtype = max(production, key=lambda nt: _semver_key(nt[0]))
    assert objtype == "tag", f"{name!r} is a lightweight tag (object type {objtype!r}), not annotated. {FIX}"
