"""Policy `version-format` (version pillar; gates commit, branch_finish): the project's
VERSION file holds <major>.<minor>.<patch>, optionally followed by -dev.<N> or -rc.<N>.

Judges the commit being made when the commit-msg hook runs it (POLICY_COMMIT_DIR holds its
staged VERSION), otherwise the checkout's VERSION file."""

import os
import re

import pytest

PROJECT = os.environ.get("POLICY_PROJECT_DIR", "/project")
COMMIT_DIR = os.environ.get("POLICY_COMMIT_DIR")
SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(-(dev|rc)\.(0|[1-9]\d*))?$")
FIX = (
    "Fix: VERSION holds one line such as 1.4.2, 1.4.3-dev.1 or 1.5.0-rc.2. On a feature/ or "
    "improvement/ branch the pre-commit hook writes it (`lws version sync-version-files`); release "
    "and hotfix finishes write it themselves."
)


def _version():
    """(what is judged, its raw text)."""
    if COMMIT_DIR:
        where, path = "the commit's VERSION", os.path.join(COMMIT_DIR, "VERSION")
    else:
        where, path = "VERSION", os.path.join(PROJECT, "VERSION")
    if not os.path.isfile(path):
        pytest.fail(f"{where} is missing from the repository root. {FIX}")
    with open(path, encoding="utf-8") as fh:
        return where, fh.read()


def test_version_file_is_semantic_version():
    where, raw = _version()
    content = raw.strip()
    assert content, f"{where} is empty. {FIX}"
    assert "\n" not in content, f"{where} must hold a single line, got {raw!r}. {FIX}"
    assert SEMVER_RE.match(content), (
        f"{where} is {content!r}, not <major>.<minor>.<patch>[-dev.<N>|-rc.<N>]. {FIX}"
    )
