"""Policy `production-version-stable` (version pillar; gates release): while the checked-out
branch is the production branch, the project's VERSION file contains a stable version with
no pre-release suffix.

The production branch is the one .lvx/config.json names (`production_branch`), else main,
master, production or prod."""

import json
import os
import re
import subprocess

import pytest

PROJECT = os.environ.get("POLICY_PROJECT_DIR", "/project")
DEFAULT_PRODUCTION_BRANCHES = {"main", "master", "production", "prod"}
# Stable semver: MAJOR.MINOR.PATCH with no "-prerelease" suffix (build metadata "+x" tolerated).
STABLE_VERSION_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(\+[0-9A-Za-z.-]+)?$")
FIX = (
    "Fix: the production branch only ever receives release and hotfix finishes "
    "(workshop_version_release_finish / workshop_version_hotfix_finish), which write the stable "
    "VERSION; never commit to it directly."
)


def _current_branch():
    result = subprocess.run(
        ["git", "-c", "safe.directory=*", "-C", PROJECT, "symbolic-ref", "--short", "-q", "HEAD"],
        capture_output=True, text=True,
    )
    return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else None


def _production_branches():
    try:
        with open(os.path.join(PROJECT, ".lvx", "config.json"), encoding="utf-8") as fh:
            configured = json.load(fh).get("production_branch")
        if configured:
            return {configured}
    except (OSError, ValueError):
        pass
    return set(DEFAULT_PRODUCTION_BRANCHES)


def test_version_file_is_stable_on_production_branch():
    assert os.path.isdir(PROJECT), f"project checkout not mounted at {PROJECT}"

    branch = _current_branch()
    if branch is None:
        pytest.skip("detached HEAD or no git metadata: the production branch is not checked out")
    if branch not in _production_branches():
        pytest.skip(f"the checked-out branch {branch!r} is not the production branch")

    version_path = os.path.join(PROJECT, "VERSION")
    assert os.path.isfile(version_path), f"VERSION is missing on the production branch. {FIX}"
    with open(version_path, encoding="utf-8") as fh:
        version = fh.read().strip()
    assert version, f"VERSION is empty on the production branch. {FIX}"
    assert STABLE_VERSION_RE.match(version), (
        f"VERSION {version!r} on the production branch {branch!r} is not a stable version "
        f"(it has a pre-release suffix or is malformed). {FIX}"
    )
