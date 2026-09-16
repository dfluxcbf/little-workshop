import subprocess
from pathlib import Path

from agent import gitutil


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-b", "dev"], repo)
    _git(["config", "user.email", "t@example.com"], repo)
    _git(["config", "user.name", "T"], repo)
    (repo / "f.txt").write_text("x\n", encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-m", "first"], repo)
    return repo


def test_is_dirty(tmp_path):
    repo = make_repo(tmp_path)
    assert gitutil.is_dirty(repo) is False
    (repo / "new.txt").write_text("x", encoding="utf-8")
    assert gitutil.is_dirty(repo) is True


def test_current_branch(tmp_path):
    repo = make_repo(tmp_path)
    assert gitutil.current_branch(repo) == "dev"


def test_checkout_new_and_commit_if_dirty(tmp_path):
    repo = make_repo(tmp_path)
    gitutil.checkout_new(repo, "feature/x", "dev")
    assert gitutil.current_branch(repo) == "feature/x"
    (repo / "new.txt").write_text("hi", encoding="utf-8")
    committed = gitutil.commit_if_dirty(repo, "add new.txt")
    assert committed is True
    assert gitutil.is_dirty(repo) is False


def test_commit_if_dirty_noop_when_clean(tmp_path):
    repo = make_repo(tmp_path)
    assert gitutil.commit_if_dirty(repo, "nothing") is False


def test_ahead_count(tmp_path):
    repo = make_repo(tmp_path)
    gitutil.checkout_new(repo, "feature/x", "dev")
    (repo / "new.txt").write_text("hi", encoding="utf-8")
    gitutil.commit_if_dirty(repo, "add file")
    assert gitutil.ahead_count(repo, "dev", "feature/x") == 1
    assert gitutil.ahead_count(repo, "feature/x", "dev") == 0


def test_branch_location_local_only(tmp_path):
    repo = make_repo(tmp_path)
    gitutil.checkout_new(repo, "feature/x", "dev")
    gitutil.checkout(repo, "dev")
    assert gitutil.branch_location(repo, "feature/x") == "local"


def test_branch_location_unknown(tmp_path):
    repo = make_repo(tmp_path)
    assert gitutil.branch_location(repo, "ghost") is None


def test_reset_branch_to(tmp_path):
    repo = make_repo(tmp_path)
    base_sha = gitutil.head_sha(repo)
    (repo / "new.txt").write_text("hi", encoding="utf-8")
    gitutil.commit_if_dirty(repo, "advance dev")
    # git refuses to force-move the currently checked-out branch — move
    # off it first, matching how the rescue flow actually uses this (a
    # fallback branch is checked out before dev gets reset).
    gitutil.checkout_new(repo, "rescue/x")
    gitutil.reset_branch_to(repo, "dev", base_sha)
    result = subprocess.run(["git", "rev-parse", "dev"], cwd=repo, capture_output=True, text=True)
    assert result.stdout.strip() == base_sha


def test_has_origin_remote_false_when_none_configured(tmp_path):
    repo = make_repo(tmp_path)
    assert gitutil.has_origin_remote(repo) is False


def test_has_origin_remote_true_when_configured(tmp_path):
    repo = make_repo(tmp_path)
    bare = tmp_path / "bare.git"
    subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True)
    _git(["remote", "add", "origin", str(bare)], repo)
    assert gitutil.has_origin_remote(repo) is True


def test_fetch_is_a_noop_without_origin_remote(tmp_path):
    repo = make_repo(tmp_path)
    gitutil.fetch(repo)  # must not raise


def test_pull_ff_only_is_a_noop_without_origin_remote(tmp_path):
    repo = make_repo(tmp_path)
    gitutil.pull_ff_only(repo, "dev")  # must not raise


def test_fetch_runs_git_fetch_when_origin_configured(tmp_path):
    repo = make_repo(tmp_path)
    bare = tmp_path / "bare.git"
    subprocess.run(["git", "init", "--bare", "-b", "dev", str(bare)], check=True, capture_output=True)
    _git(["remote", "add", "origin", str(bare)], repo)
    gitutil.fetch(repo)  # must not raise, and actually runs `git fetch`


def test_pull_ff_only_runs_git_pull_when_origin_configured(tmp_path):
    repo = make_repo(tmp_path)
    bare = tmp_path / "bare.git"
    subprocess.run(["git", "init", "--bare", "-b", "dev", str(bare)], check=True, capture_output=True)
    _git(["remote", "add", "origin", str(bare)], repo)
    gitutil.push(repo, "dev")
    gitutil.pull_ff_only(repo, "dev")  # already up to date, but exercises the real `git pull --ff-only`


def test_run_with_check_raises_on_failure(tmp_path):
    repo = make_repo(tmp_path)
    try:
        gitutil.run(["git", "checkout", "no-such-branch"], repo, check=True)
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert "git command failed" in str(exc)


def test_branch_location_remote_only(tmp_path):
    repo = make_repo(tmp_path)
    bare = tmp_path / "bare.git"
    subprocess.run(["git", "init", "--bare", "-b", "dev", str(bare)], check=True, capture_output=True)
    _git(["remote", "add", "origin", str(bare)], repo)
    gitutil.checkout_new(repo, "feature/x", "dev")
    gitutil.push(repo, "feature/x")
    gitutil.checkout(repo, "dev")
    _git(["branch", "-D", "feature/x"], repo)  # local ref gone, origin keeps it
    _git(["fetch", "origin", "--prune"], repo)
    assert gitutil.branch_location(repo, "feature/x") == "remote"


def test_checkout_remote_tracking(tmp_path):
    repo = make_repo(tmp_path)
    bare = tmp_path / "bare.git"
    subprocess.run(["git", "init", "--bare", "-b", "dev", str(bare)], check=True, capture_output=True)
    _git(["remote", "add", "origin", str(bare)], repo)
    gitutil.checkout_new(repo, "feature/x", "dev")
    gitutil.push(repo, "feature/x")
    gitutil.checkout(repo, "dev")
    _git(["branch", "-D", "feature/x"], repo)
    _git(["fetch", "origin", "--prune"], repo)

    gitutil.checkout_remote_tracking(repo, "feature/x")
    assert gitutil.current_branch(repo) == "feature/x"
    result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "feature/x@{upstream}"], cwd=repo, capture_output=True, text=True,
    )
    assert result.stdout.strip() == "origin/feature/x"
