"""Additional gitutil.py coverage: real origin remotes, remote-only
branches, and the check=True error path -- gaps test_deployment_gitutil.py
didn't reach yet."""

import subprocess
from pathlib import Path

import pytest

from deployment import gitutil


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def make_repo(tmp_path: Path, name: str = "repo") -> Path:
    repo = tmp_path / name
    repo.mkdir()
    _git(["init", "-b", "main"], repo)
    _git(["config", "user.email", "t@example.com"], repo)
    _git(["config", "user.name", "T"], repo)
    (repo / "VERSION").write_text("1.0.0\n", encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-m", "first"], repo)
    return repo


def make_clone_with_origin(tmp_path: Path) -> Path:
    """A real bare origin plus a real clone -- the only way to exercise
    branch_location's REMOTE paths for real, since they depend on an
    actual `origin` remote existing."""
    upstream = make_repo(tmp_path, "upstream")
    bare = tmp_path / "upstream.git"
    subprocess.run(["git", "clone", "--bare", str(upstream), str(bare)], check=True, capture_output=True)
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", str(bare), str(clone)], check=True, capture_output=True, text=True)
    _git(["config", "user.email", "t@example.com"], clone)
    _git(["config", "user.name", "T"], clone)
    return clone


def test_run_with_check_raises_on_failure(tmp_path):
    repo = make_repo(tmp_path)
    with pytest.raises(RuntimeError, match="git command failed"):
        gitutil.run(["git", "not-a-real-subcommand"], repo, check=True)


def test_run_without_check_does_not_raise_on_failure(tmp_path):
    repo = make_repo(tmp_path)
    result = gitutil.run(["git", "not-a-real-subcommand"], repo)
    assert result.returncode != 0


def test_branch_location_remote_when_tracking_ref_already_exists(tmp_path):
    clone = make_clone_with_origin(tmp_path)
    # main is already fetched as both a local branch (checked out) and
    # available under refs/remotes/origin/main from the clone itself.
    result = gitutil.run(["git", "rev-parse", "--verify", "--quiet", "refs/remotes/origin/main"], clone)
    assert result.returncode == 0
    # Delete the local branch's own ref info isn't possible while checked
    # out, so instead prove the REMOTE branch (never checked out locally)
    # resolves via the already-fetched tracking ref path.
    upstream_dir = tmp_path / "upstream"
    _git(["checkout", "-b", "feature"], upstream_dir)
    subprocess.run(
        ["git", "push", str(tmp_path / "upstream.git"), "feature"],
        cwd=upstream_dir, check=True, capture_output=True,
    )
    subprocess.run(["git", "fetch", "origin"], cwd=clone, check=True, capture_output=True)
    assert gitutil.branch_location(clone, "feature") == gitutil.REMOTE


def test_branch_location_fetches_a_remote_only_branch_not_yet_tracked(tmp_path):
    clone = make_clone_with_origin(tmp_path)
    upstream = tmp_path / "upstream"
    _git(["checkout", "-b", "topic"], upstream)
    (upstream / "extra.txt").write_text("x", encoding="utf-8")
    _git(["add", "-A"], upstream)
    _git(["commit", "-m", "topic work"], upstream)
    subprocess.run(["git", "push", str(tmp_path / "upstream.git"), "topic"], cwd=upstream, check=True, capture_output=True)

    # The clone has never fetched "topic" -- no local branch, no tracking
    # ref yet -- so branch_location must ls-remote and fetch it itself.
    assert gitutil.run(["git", "rev-parse", "--verify", "--quiet", "refs/remotes/origin/topic"], clone).returncode != 0
    assert gitutil.branch_location(clone, "topic") == gitutil.REMOTE
    assert gitutil.run(["git", "rev-parse", "--verify", "--quiet", "refs/remotes/origin/topic"], clone).returncode == 0


def test_list_branches_excludes_origin_head_symref(tmp_path):
    clone = make_clone_with_origin(tmp_path)
    result = subprocess.run(["git", "for-each-ref", "refs/remotes/origin"], cwd=clone, capture_output=True, text=True)
    assert "origin/HEAD" in result.stdout
    branches = gitutil.list_branches(clone)
    assert "HEAD" not in branches
    assert all(not b.startswith("origin/HEAD") for b in branches)


def test_remove_worktree_falls_back_to_rmtree_when_not_a_registered_worktree(tmp_path):
    repo = make_repo(tmp_path)
    stray = tmp_path / "stray-dir"
    stray.mkdir()
    (stray / "file.txt").write_text("hi", encoding="utf-8")

    # `git worktree remove --force` fails here (stray isn't a registered
    # worktree of repo), so the shutil.rmtree fallback must do the work.
    gitutil.remove_worktree(repo, stray)
    assert not stray.exists()


def test_current_commit_returns_placeholder_outside_a_repo(tmp_path):
    empty = tmp_path / "not-a-repo"
    empty.mkdir()
    assert gitutil.current_commit(empty) == "?"


# -- policy script locator + GitHub URL ---------------------------------------


def _script_repo(tmp_path):
    repo = tmp_path / "workshop"
    (repo / "tools" / "policy_scripts").mkdir(parents=True)
    _git(["init", "-b", "dev"], repo)
    _git(["config", "user.email", "t@example.com"], repo)
    _git(["config", "user.name", "T"], repo)
    return repo


def _commit_script(repo, name, text, version):
    (repo / "tools" / "policy_scripts" / name).write_text(text, encoding="utf-8")
    (repo / "VERSION").write_text(version + "\n", encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-m", f"{name} {version}"], repo)
    return gitutil.run(["git", "rev-parse", "HEAD"], repo).stdout.strip()


def test_locate_file_version_finds_the_commit_that_holds_exactly_that_content(tmp_path):
    repo = _script_repo(tmp_path)
    first = _commit_script(repo, "a.py", "v1\n", "0.1.1-dev.1")
    second = _commit_script(repo, "a.py", "v2\n", "0.1.1-dev.2")
    _commit_script(repo, "b.py", "other\n", "0.1.1-dev.3")  # a later commit that leaves a.py alone

    current = gitutil.locate_file_version(repo, "v2\n", "tools/policy_scripts")
    assert current == {"path": "tools/policy_scripts/a.py", "commit": second, "version": "0.1.1-dev.2"}

    # an older version that is still what a policy runs links to the commit that had it, not today's file
    older = gitutil.locate_file_version(repo, "v1\n", "tools/policy_scripts")
    assert older["commit"] == first and older["version"] == "0.1.1-dev.1"


def test_locate_file_version_is_none_for_content_that_was_never_committed(tmp_path):
    repo = _script_repo(tmp_path)
    _commit_script(repo, "a.py", "v1\n", "0.1.1-dev.1")
    assert gitutil.locate_file_version(repo, "v1\n# edited\n", "tools/policy_scripts") is None
    assert gitutil.locate_file_version(repo, "v1\n", "no/such/dir") is None


def test_locate_file_version_reports_no_version_when_the_commit_has_no_version_file(tmp_path):
    repo = _script_repo(tmp_path)
    (repo / "tools" / "policy_scripts" / "a.py").write_text("v1\n", encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-m", "no version"], repo)
    assert gitutil.locate_file_version(repo, "v1\n", "tools/policy_scripts")["version"] is None


@pytest.mark.parametrize("remote,expected", [
    ("git@github.com:dfluxcbf/little-workshop.git", "https://github.com/dfluxcbf/little-workshop"),
    ("https://github.com/dfluxcbf/little-workshop.git", "https://github.com/dfluxcbf/little-workshop"),
    ("https://github.com/dfluxcbf/little-workshop", "https://github.com/dfluxcbf/little-workshop"),
    ("ssh://git@github.com/o/r.git", "https://github.com/o/r"),
    ("git@gitlab.com:o/r.git", None),
    ("https://github.com/only-owner", None),
])
def test_github_base_url_normalises_ssh_and_https_remotes(tmp_path, remote, expected):
    repo = _script_repo(tmp_path)
    _git(["remote", "add", "origin", remote], repo)
    assert gitutil.github_base_url(repo) == expected


def test_github_base_url_is_none_without_an_origin_remote(tmp_path):
    assert gitutil.github_base_url(_script_repo(tmp_path)) is None
