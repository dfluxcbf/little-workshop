"""version.gitops' less-common paths: real command failures (not just the
conflict-shaped ones already covered by the topic/hotfix/release finish
tests), detached HEAD, already-exists guards, and the remote/tag/commit
helpers nothing else exercises."""

import subprocess

import pytest
from version_repo_helper import make_repo

from version.gitops import (
    GitError,
    RebaseConflictError,
    branch_exists,
    checkout,
    commit_all_tracked,
    commit_if_dirty,
    create_annotated_tag,
    create_branch,
    current_branch,
    delete_remote_branch,
    merge_commit,
    merge_no_ff,
    rebase_continue,
    rebase_onto,
    run,
    squash_to_single_commit,
    tag_exists,
)


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _write_commit(repo, filename, content, message):
    (repo / filename).write_text(content, encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-m", message], repo)


def test_run_raises_git_error_with_check_true(tmp_path):
    repo = make_repo(tmp_path)
    with pytest.raises(GitError, match="git command failed"):
        run(["git", "totally-not-a-real-subcommand"], repo)


def test_run_with_check_false_does_not_raise(tmp_path):
    repo = make_repo(tmp_path)
    result = run(["git", "totally-not-a-real-subcommand"], repo, check=False)
    assert result.returncode != 0


def test_current_branch_raises_in_detached_head(tmp_path):
    repo = make_repo(tmp_path)
    sha = run(["git", "rev-parse", "HEAD"], repo).stdout.strip()
    _git(["checkout", sha], repo)
    with pytest.raises(GitError, match="detached HEAD"):
        current_branch(repo)


def test_create_branch_raises_if_already_exists(tmp_path):
    repo = make_repo(tmp_path)
    create_branch(repo, "feature/widgets", "dev")
    with pytest.raises(GitError, match="already exists"):
        create_branch(repo, "feature/widgets", "dev")


def test_merge_no_ff_raises_plain_git_error_on_non_conflict_failure(tmp_path):
    repo = make_repo(tmp_path)
    checkout(repo, "dev")
    with pytest.raises(GitError):
        merge_no_ff(repo, "branch-that-does-not-exist", "merge message")


def test_rebase_onto_raises_plain_git_error_on_non_conflict_failure(tmp_path):
    repo = make_repo(tmp_path)
    checkout(repo, "dev")
    with pytest.raises(GitError):
        rebase_onto(repo, "branch-that-does-not-exist")


def test_create_annotated_tag_raises_if_tag_already_exists(tmp_path):
    from version.gitops import TagExistsError

    repo = make_repo(tmp_path)
    create_annotated_tag(repo, "v9.9.9", "a tag")
    with pytest.raises(TagExistsError):
        create_annotated_tag(repo, "v9.9.9", "a tag again")


def test_create_annotated_tag_on_an_explicit_commit(tmp_path):
    repo = make_repo(tmp_path)
    _write_commit(repo, "a.py", "x = 1\n", "a commit")
    first_sha = run(["git", "rev-parse", "HEAD~1"], repo).stdout.strip()
    _write_commit(repo, "b.py", "y = 1\n", "another commit")

    create_annotated_tag(repo, "v1.0.0", "tag the earlier commit", commit=first_sha)
    tagged_sha = run(["git", "rev-list", "-n", "1", "v1.0.0"], repo).stdout.strip()
    assert tagged_sha == first_sha


def test_commit_all_tracked_commits_only_tracked_changes(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "README.md").write_text("changed\n", encoding="utf-8")
    (repo / "untracked.txt").write_text("new\n", encoding="utf-8")

    sha = commit_all_tracked(repo, "chore: update readme")
    status = run(["git", "status", "--porcelain"], repo).stdout
    assert "README.md" not in status
    assert "untracked.txt" in status  # still untracked, -u never adds new files
    assert sha == run(["git", "rev-parse", "HEAD"], repo).stdout.strip()


def test_commit_if_dirty_returns_false_when_nothing_to_commit(tmp_path):
    repo = make_repo(tmp_path)
    assert commit_if_dirty(repo, "no-op commit") is False


def test_delete_remote_branch_removes_it_from_a_real_bare_remote(tmp_path):
    repo = make_repo(tmp_path)
    bare = tmp_path / "bare.git"
    run(["git", "init", "--bare", str(bare)], tmp_path)
    run(["git", "remote", "add", "origin", str(bare)], repo)
    run(["git", "push", "origin", "dev"], repo)
    create_branch(repo, "feature/widgets", "dev")
    run(["git", "push", "origin", "feature/widgets"], repo)

    delete_remote_branch(repo, "feature/widgets")

    remote_branches = run(["git", "ls-remote", "--heads", "origin"], repo).stdout
    assert "feature/widgets" not in remote_branches


def test_merge_commit_commits_staged_resolution_and_returns_sha(tmp_path):
    repo = make_repo(tmp_path)
    checkout(repo, "dev")
    _write_commit(repo, "shared.py", "a = 1\n", "dev baseline")
    create_branch(repo, "feature/x", "dev")
    checkout(repo, "feature/x")
    _write_commit(repo, "shared.py", "a = 2\n", "feature change")
    checkout(repo, "dev")
    _write_commit(repo, "shared.py", "a = 3\n", "conflicting dev change")

    with pytest.raises(Exception):
        merge_no_ff(repo, "feature/x", "merge feature/x")

    (repo / "shared.py").write_text("a = 2\n", encoding="utf-8")
    run(["git", "add", "shared.py"], repo)
    sha = merge_commit(repo, "resolved merge")
    assert sha == run(["git", "rev-parse", "HEAD"], repo).stdout.strip()


def test_squash_to_single_commit_returns_existing_sha_when_nothing_new(tmp_path):
    repo = make_repo(tmp_path)
    checkout(repo, "dev")
    create_branch(repo, "feature/empty", "dev")
    checkout(repo, "feature/empty")

    before_sha = run(["git", "rev-parse", "HEAD"], repo).stdout.strip()
    result_sha = squash_to_single_commit(repo, "dev", "squash message")
    assert result_sha == before_sha


def test_rebase_continue_raises_when_a_later_commit_still_conflicts(tmp_path):
    repo = make_repo(tmp_path)
    checkout(repo, "dev")
    _write_commit(repo, "shared.py", "base\n", "dev baseline")

    create_branch(repo, "feature/x", "dev")
    checkout(repo, "feature/x")
    _write_commit(repo, "shared.py", "feature-1\n", "feature change 1")
    _write_commit(repo, "shared.py", "feature-2\n", "feature change 2")

    checkout(repo, "dev")
    _write_commit(repo, "shared.py", "dev-conflict\n", "conflicting dev change")
    checkout(repo, "feature/x")

    with pytest.raises(RebaseConflictError):
        rebase_onto(repo, "dev")

    # Resolve the first conflicting commit, but NOT to the exact content
    # commit 1 originally produced ("feature-1") — if it matched exactly,
    # commit 2's patch (whose pre-image *is* "feature-1") would apply
    # cleanly and nothing would conflict again. Resolving to something
    # else keeps commit 2's diff from applying, so it conflicts too.
    (repo / "shared.py").write_text("resolved-differently\n", encoding="utf-8")
    run(["git", "add", "shared.py"], repo)

    with pytest.raises(RebaseConflictError, match="Further rebase conflicts remain"):
        rebase_continue(repo)
