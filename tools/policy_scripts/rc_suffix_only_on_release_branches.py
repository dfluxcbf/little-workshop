"""Policy `rc-suffix-only-on-release-branches` (version pillar; gates release): if the project's
VERSION file carries an -rc.<N> suffix, the checked-out branch is a release/* branch."""

import os
import re
import subprocess

import pytest

PROJECT = os.environ.get("POLICY_PROJECT_DIR", "/project")
RC_RE = re.compile(r"-rc\.\d+\s*$")
FIX = (
    "Fix: an -rc.N VERSION is written by workshop_version_release_candidate_cut on a release/* "
    "branch only; elsewhere VERSION is a stable or -dev.N version (`lws version sync-version-files` "
    "on a working branch)."
)


def _read_version():
    path = os.path.join(PROJECT, "VERSION")
    if not os.path.isfile(path):
        pytest.skip("no VERSION file in the project")
    with open(path, encoding="utf-8") as fh:
        return fh.read().strip()


def _current_branch():
    result = subprocess.run(
        ["git", "-c", "safe.directory=*", "-C", PROJECT, "symbolic-ref", "--short", "-q", "HEAD"],
        capture_output=True, text=True,
    )
    return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else None


def test_rc_version_requires_release_branch():
    version = _read_version()
    if not RC_RE.search(version):
        pytest.skip(f"VERSION {version!r} has no -rc.<N> suffix")

    branch = _current_branch()
    assert branch is not None, (
        f"VERSION {version!r} carries an -rc.<N> suffix but the checkout is on a detached HEAD, "
        f"not a release/* branch. {FIX}"
    )
    assert branch.startswith("release/") and len(branch) > len("release/"), (
        f"VERSION {version!r} carries an -rc.<N> suffix but the checked-out branch is {branch!r}, "
        f"not a release/* branch. {FIX}"
    )
