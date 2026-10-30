"""Policy `merge-commit-message-format` (version pillar; gates branch_finish): if the newest
commit on the integration or production branch is a merge commit, its first line reads
"feat (<issue number or -1>): <title>", "impr (<issue number or -1>): <title>" or
"fix (<issue number or -1>): <title>".

Only the newest commit of each branch is judged, so an old merge never fails this forever."""

import os
import re
import subprocess

import pytest

PROJECT = os.environ.get("POLICY_PROJECT_DIR", "/project")
INTEGRATION = ["develop", "dev", "development", "integration"]
PRODUCTION = ["main", "master", "production", "prod"]
PATTERN = re.compile(r"^(feat|impr|fix) \((\d+|-1)\): \S.*$")
FIX = (
    "Fix: merges onto these branches come from workshop_version_*_finish, which writes this form; "
    "never merge by hand. If a hand-made merge is the newest, the next finish (confirmed past this "
    "check with workshop_policy_confirm) puts a well-formed merge on top of it."
)


def git(*args):
    r = subprocess.run(
        ["git", "-c", "safe.directory=*", "-C", PROJECT, *args],
        capture_output=True,
        text=True,
    )
    return r.returncode, r.stdout.strip()


def branch_exists(name):
    for ref in (f"refs/heads/{name}", f"refs/remotes/origin/{name}"):
        rc, _ = git("rev-parse", "--verify", "--quiet", ref)
        if rc == 0:
            return ref
    return None


def test_merge_commit_first_line_format():
    rc, out = git("rev-parse", "--is-inside-work-tree")
    if rc != 0:
        pytest.skip(f"{PROJECT} is not a git repository")

    refs = {}
    for name in INTEGRATION + PRODUCTION:
        ref = branch_exists(name)
        if ref:
            refs[name] = ref
    if not refs:
        pytest.skip("no integration or production branch found")

    failures = []
    for name, ref in refs.items():
        rc, out = git("log", "-1", "--format=%H%n%P%n%s", ref)
        if rc != 0 or not out:
            continue
        lines = out.split("\n")
        sha = lines[0]
        parents = lines[1].split() if len(lines) > 1 else []
        subject = lines[2] if len(lines) > 2 else ""
        if len(parents) < 2:
            continue
        if not PATTERN.match(subject):
            failures.append(f"{name} ({sha[:10]}): {subject!r}")

    assert not failures, (
        "The newest merge commit's first line must read 'feat|impr|fix (<issue number or -1>): <title>'; "
        f"violations: {'; '.join(failures)}. {FIX}"
    )
