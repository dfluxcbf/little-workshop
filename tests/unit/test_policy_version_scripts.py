"""The two version-policy test scripts under tools/policy_scripts/, run for
real (pytest against a throwaway git repository) — they are what the Policy
sandbox executes, so they are tested as the artifacts they are."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "tools" / "policy_scripts"
RULE1 = SCRIPTS / "commit_bumps_version.py"
RULE2 = SCRIPTS / "branch_commit_dev_suffix.py"


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


def commit(repo, version, message="c"):
    if version is None:
        (repo / "VERSION").unlink(missing_ok=True)
    else:
        (repo / "VERSION").write_text(version + "\n", encoding="utf-8")
    (repo / "n.txt").write_text(message, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--allow-empty", "-m", message)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q", "-b", "dev")
    git(r, "config", "user.email", "t@example.com")
    git(r, "config", "user.name", "T")
    return r


def verdict(script, repo):
    result = subprocess.run(
        [sys.executable, "-m", "pytest", str(script), "-q", "-p", "no:cacheprovider", "--no-header"],
        capture_output=True, text=True, env={**os.environ, "POLICY_PROJECT_DIR": str(repo)},
    )
    tail = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
    if re.search(r"\d+ failed", tail):
        return "fail"
    if re.search(r"\d+ passed", tail):
        return "pass"
    if "skipped" in tail:
        return "skip"
    return f"error: {result.stdout}{result.stderr}"


# -- commit-bumps-version ---------------------------------------------------


def test_rule1_passes_when_head_outranks_its_parent(repo):
    commit(repo, "0.1.0")
    commit(repo, "0.1.1-dev.1")
    assert verdict(RULE1, repo) == "pass"


@pytest.mark.parametrize("new", ["0.1.0", "0.0.9", "0.1.0-dev.5"])
def test_rule1_fails_when_head_does_not_outrank_its_parent(repo, new):
    commit(repo, "0.1.0")
    commit(repo, new)
    assert verdict(RULE1, repo) == "fail"


def test_rule1_orders_prereleases_below_their_release_and_dev_below_rc(repo):
    commit(repo, "0.2.0-dev.9")
    commit(repo, "0.2.0-rc.1")
    commit(repo, "0.2.0")
    assert verdict(RULE1, repo) == "pass"


def test_rule1_fails_on_a_missing_or_malformed_head_version(repo):
    commit(repo, "0.1.0")
    commit(repo, "banana")
    assert verdict(RULE1, repo) == "fail"
    commit(repo, None)
    assert verdict(RULE1, repo) == "fail"


def test_rule1_only_judges_the_newest_commit_so_old_mistakes_do_not_fail_forever(repo):
    commit(repo, "0.1.0")
    commit(repo, "0.1.0")  # a past mistake: no bump
    commit(repo, "0.1.1-dev.1")  # the newest commit is fine
    assert verdict(RULE1, repo) == "pass"


def test_rule1_accepts_a_valid_head_after_a_malformed_parent(repo):
    commit(repo, "not-a-version")
    commit(repo, "0.1.0")
    assert verdict(RULE1, repo) == "pass"


def test_rule1_is_not_applicable_without_a_parent_or_a_parent_version(repo):
    commit(repo, "0.1.0")
    assert verdict(RULE1, repo) == "skip"
    (repo / "VERSION").unlink()
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "drop version")
    commit(repo, "0.1.0")
    assert verdict(RULE1, repo) == "skip"


# -- branch-commit-dev-suffix -----------------------------------------------


def test_rule2_first_branch_commit_must_be_next_patch_dev_1(repo):
    commit(repo, "0.1.0")
    git(repo, "checkout", "-q", "-b", "feature/x")
    commit(repo, "0.1.1-dev.1")
    assert verdict(RULE2, repo) == "pass"


@pytest.mark.parametrize("wrong", ["0.1.1-dev.2", "0.1.0-dev.1", "0.2.0-dev.1", "0.1.1", "0.1.1-rc.1"])
def test_rule2_rejects_a_wrong_first_branch_commit(repo, wrong):
    commit(repo, "0.1.0")
    git(repo, "checkout", "-q", "-b", "feature/x")
    commit(repo, wrong)
    assert verdict(RULE2, repo) == "fail"


def test_rule2_first_commit_off_a_dev_line_version_bumps_that_patch(repo):
    commit(repo, "0.1.1-dev.1")
    git(repo, "checkout", "-q", "-b", "improvement/y")
    commit(repo, "0.1.2-dev.1")
    assert verdict(RULE2, repo) == "pass"


def test_rule2_later_commits_increment_n_by_exactly_one(repo):
    commit(repo, "0.1.0")
    git(repo, "checkout", "-q", "-b", "feature/x")
    commit(repo, "0.1.1-dev.1")
    commit(repo, "0.1.1-dev.2")
    assert verdict(RULE2, repo) == "pass"
    commit(repo, "0.1.1-dev.4")
    assert verdict(RULE2, repo) == "fail"


def test_rule2_only_judges_the_newest_commit_so_old_mistakes_do_not_fail_forever(repo):
    commit(repo, "0.1.0")
    git(repo, "checkout", "-q", "-b", "feature/x")
    commit(repo, "0.1.1")  # a past mistake: not a dev.N version
    commit(repo, "0.1.1-dev.1")  # newest commit is well-formed
    assert verdict(RULE2, repo) == "pass"


def test_rule2_is_not_applicable_on_integration_release_hotfix_or_detached_head(repo):
    commit(repo, "0.1.0")
    commit(repo, "0.1.1-dev.1")
    assert verdict(RULE2, repo) == "skip"
    for name in ("release/0.2.0", "hotfix/oops"):
        git(repo, "checkout", "-q", "-b", name)
        commit(repo, "0.1.2")
        assert verdict(RULE2, repo) == "skip"
        git(repo, "checkout", "-q", "dev")
    git(repo, "checkout", "-q", "--detach")
    assert verdict(RULE2, repo) == "skip"


def test_shared_helper_blocks_are_identical_in_both_scripts():
    def block(path):
        text = path.read_text(encoding="utf-8")
        return text[text.index("# --- shared helpers"):text.index("# --- end shared helpers ---")]

    assert block(RULE1) == block(RULE2)
