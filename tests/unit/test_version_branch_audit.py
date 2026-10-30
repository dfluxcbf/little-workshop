"""version.branch_audit — the read-only branch-hygiene classifier ported
from the ecosystem's own root `branch_audit.py`. Every test runs against
a real git repo (`version_repo_helper.make_repo`, the same fixture every
other version-pillar test file uses) — no mocked git calls, matching
this pillar's existing test style."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from version.branch_audit import audit, audit_as_dicts
from version.configstore import load_config, write_config
from version_repo_helper import make_repo


def _git(args: list[str], cwd: Path, *, env: dict | None = None) -> None:
    full_env = {**os.environ, **(env or {})} if env else None
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True, env=full_env)


def _commit(cwd: Path, message: str, *, days_ago: int = 0) -> None:
    env = None
    if days_ago:
        import time

        ts = str(int(time.time()) - days_ago * 86400)
        env = {"GIT_AUTHOR_DATE": ts, "GIT_COMMITTER_DATE": ts}
    (cwd / f"{message.replace(' ', '_')}.txt").write_text(message, encoding="utf-8")
    _git(["add", "-A"], cwd)
    _git(["commit", "-m", message], cwd, env=env)


def test_clean_repo_has_no_violations(tmp_path):
    repo = make_repo(tmp_path)
    assert audit(repo) == []


def test_merged_topic_branch_is_stale_merged(tmp_path):
    repo = make_repo(tmp_path)
    _git(["checkout", "dev"], repo)
    _git(["checkout", "-b", "feature/done"], repo)
    _commit(repo, "finish the feature")
    _git(["checkout", "dev"], repo)
    _git(["merge", "--no-ff", "feature/done"], repo)
    _git(["checkout", "master"], repo)

    violations = audit(repo)
    assert len(violations) == 1
    assert violations[0].kind == "stale_merged"
    assert violations[0].branch == "feature/done"


def test_unmerged_topic_branch_with_no_violations_if_recent(tmp_path):
    repo = make_repo(tmp_path)
    _git(["checkout", "dev"], repo)
    _git(["checkout", "-b", "feature/in-progress"], repo)
    _commit(repo, "work in progress")
    _git(["checkout", "master"], repo)

    assert audit(repo) == []


def test_unmerged_topic_branch_abandoned_past_stale_threshold(tmp_path):
    repo = make_repo(tmp_path)
    cfg = load_config(repo)
    cfg["stale_branch_days"] = 1
    write_config(repo, cfg)
    _git(["add", "-A"], repo)
    _git(["commit", "-m", "lower stale_branch_days for the test"], repo)
    _git(["checkout", "dev"], repo)
    _git(["merge", "--ff-only", "master"], repo)
    _git(["checkout", "-b", "feature/old"], repo)
    _commit(repo, "old work", days_ago=10)
    _git(["checkout", "master"], repo)

    violations = audit(repo)
    assert len(violations) == 1
    assert violations[0].kind == "abandoned"
    assert violations[0].branch == "feature/old"


def test_unknown_branch_name_is_a_naming_violation(tmp_path):
    repo = make_repo(tmp_path)
    _git(["checkout", "dev"], repo)
    _git(["checkout", "-b", "some-random-branch"], repo)
    _commit(repo, "stray work")
    _git(["checkout", "master"], repo)

    violations = audit(repo)
    assert len(violations) == 1
    assert violations[0].kind == "naming"
    assert violations[0].branch == "some-random-branch"
    assert "rename/finish" in violations[0].recommended_action


def test_unknown_branch_name_already_merged_recommends_delete(tmp_path):
    repo = make_repo(tmp_path)
    _git(["checkout", "dev"], repo)
    _git(["checkout", "-b", "stray"], repo)
    _commit(repo, "stray but harmless")
    _git(["checkout", "dev"], repo)
    _git(["merge", "--no-ff", "stray"], repo)
    _git(["checkout", "master"], repo)

    violations = audit(repo)
    assert len(violations) == 1
    assert violations[0].kind == "naming"
    assert "delete (already merged)" in violations[0].recommended_action


def test_release_and_support_branches_are_exempt(tmp_path):
    repo = make_repo(tmp_path)
    _git(["checkout", "dev"], repo)
    _git(["checkout", "-b", "release/1.0.0"], repo)
    _commit(repo, "release prep")
    _git(["checkout", "dev"], repo)
    _git(["checkout", "-b", "support/1.x"], repo)
    _commit(repo, "support branch")
    _git(["checkout", "master"], repo)

    assert audit(repo) == []


def test_detached_head_is_a_violation(tmp_path):
    repo = make_repo(tmp_path)
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()
    _git(["checkout", sha], repo)

    violations = audit(repo)
    assert any(v.kind == "detached_head" for v in violations)


def test_stray_worktree_is_a_violation(tmp_path):
    repo = make_repo(tmp_path)
    other = tmp_path / "other-worktree"
    _git(["worktree", "add", str(other), "dev"], repo)

    violations = audit(repo)
    assert any(v.kind == "stray_worktree" for v in violations)


def test_mid_rebase_is_skipped_entirely(tmp_path):
    repo = make_repo(tmp_path)
    _git(["checkout", "dev"], repo)
    _git(["checkout", "-b", "feature/conflict-a"], repo)
    (repo / "shared.txt").write_text("a\n", encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-m", "a"], repo)
    _git(["checkout", "dev"], repo)
    _git(["checkout", "-b", "feature/conflict-b"], repo)
    (repo / "shared.txt").write_text("b\n", encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-m", "b"], repo)
    subprocess.run(["git", "rebase", "feature/conflict-a"], cwd=repo, capture_output=True, text=True)

    assert audit(repo) == []


def test_audit_as_dicts_matches_audit(tmp_path):
    repo = make_repo(tmp_path)
    _git(["checkout", "dev"], repo)
    _git(["checkout", "-b", "feature/done"], repo)
    _commit(repo, "finish it")
    _git(["checkout", "dev"], repo)
    _git(["merge", "--no-ff", "feature/done"], repo)
    _git(["checkout", "master"], repo)

    dicts = audit_as_dicts(repo)
    assert dicts == [{"kind": "stale_merged", "branch": "feature/done", "detail": "already merged into dev.", "recommended_action": "delete: git branch -d feature/done (and the remote copy, if any)."}]


# --- REQ-000000421: A branch squash-merged into its target counts as merged ---


def test_a_squash_merged_topic_branch_is_stale_merged(tmp_path):
    repo = make_repo(tmp_path)
    _git(["checkout", "dev"], repo)
    _git(["checkout", "-b", "feature/done"], repo)
    _commit(repo, "first part")
    _commit(repo, "second part")
    _git(["checkout", "dev"], repo)
    _git(["merge", "--squash", "feature/done"], repo)
    _git(["commit", "-m", "feat (-1): done"], repo)
    _git(["checkout", "master"], repo)

    violations = audit(repo)
    assert [(v.kind, v.branch) for v in violations] == [("stale_merged", "feature/done")]


def test_squash_detection_does_not_take_unmerged_work_for_merged(tmp_path):
    from version import gitops

    repo = make_repo(tmp_path)
    _git(["checkout", "dev"], repo)
    _git(["checkout", "-b", "feature/open"], repo)
    _commit(repo, "not merged")
    assert gitops.is_merged(repo, "feature/open", "dev") is False
    _git(["checkout", "dev"], repo)
    _git(["merge", "--squash", "feature/open"], repo)
    _git(["commit", "-m", "feat (-1): open"], repo)
    assert gitops.is_merged(repo, "feature/open", "dev") is True
