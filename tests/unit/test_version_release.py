import subprocess

from version_repo_helper import make_repo

from version.commands.release import (
    cmd_release_candidate_cut,
    cmd_release_candidate_list,
    cmd_release_candidate_promote,
    cmd_release_finish,
    cmd_release_start,
    cmd_release_status,
)
from version.gitops import current_branch, list_tags


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _write_commit(repo, filename, content, message):
    (repo / filename).write_text(content, encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-m", message], repo)


def test_release_start_creates_branch_and_sets_stable_version(tmp_path):
    repo = make_repo(tmp_path)
    _git(["checkout", "dev"], repo)
    result = cmd_release_start(repo)
    assert result.success
    assert current_branch(repo) == "release/0.1.0"
    assert (repo / "VERSION").read_text().strip() == "0.1.0"


def test_release_candidate_cut_list_and_promote(tmp_path):
    repo = make_repo(tmp_path)
    _git(["checkout", "dev"], repo)
    cmd_release_start(repo)

    cut1 = cmd_release_candidate_cut(repo)
    assert cut1.success
    assert cut1.data["tag"] == "v0.1.0-rc.1"

    cut2 = cmd_release_candidate_cut(repo)
    assert cut2.data["tag"] == "v0.1.0-rc.2"

    listed = cmd_release_candidate_list(repo)
    assert listed.data["tags"] == ["v0.1.0-rc.1", "v0.1.0-rc.2"]

    promote = cmd_release_candidate_promote(repo, 2)
    assert promote.success

    status = cmd_release_status(repo)
    assert status.data["promoted_rc"] == 2
    assert status.data["rc_tags"] == ["v0.1.0-rc.1", "v0.1.0-rc.2"]


def test_release_candidate_promote_unknown_rc_fails(tmp_path):
    repo = make_repo(tmp_path)
    _git(["checkout", "dev"], repo)
    cmd_release_start(repo)

    result = cmd_release_candidate_promote(repo, 99)
    assert result.success is False


def test_release_finish_merges_prod_and_backmerges_dev(tmp_path):
    repo = make_repo(tmp_path)
    _git(["checkout", "dev"], repo)
    cmd_release_start(repo)
    _write_commit(repo, "release_notes.md", "notes\n", "release content")

    result = cmd_release_finish(repo)
    assert result.success, result.message
    assert current_branch(repo) == "dev"

    assert list_tags(repo, "v0.1.0") == ["v0.1.0"]

    _git(["checkout", "master"], repo)
    assert (repo / "VERSION").read_text().strip() == "0.2.0"
    assert (repo / "release_notes.md").exists()

    _git(["checkout", "dev"], repo)
    assert (repo / "VERSION").read_text().strip() == "0.2.1"
    assert (repo / "release_notes.md").exists()

    branches = subprocess.run(["git", "branch"], cwd=repo, capture_output=True, text=True).stdout
    assert "release/0.1.0" not in branches


def test_release_finish_rejects_a_promoted_rc_that_was_never_cut(tmp_path):
    repo = make_repo(tmp_path)
    _git(["checkout", "dev"], repo)
    cmd_release_start(repo)
    cmd_release_candidate_cut(repo)  # rc.1

    # Promote a second RC that was never actually cut on this branch —
    # refuse cleanly rather than crash inside gitops.
    from version.configstore import set_promoted_rc

    set_promoted_rc(repo, "release/0.1.0", 99)

    result = cmd_release_finish(repo)
    assert result.success is False
    assert "does not exist" in result.message


def test_release_finish_rejects_promoted_rc_not_reachable_from_head(tmp_path):
    repo = make_repo(tmp_path)
    _git(["checkout", "dev"], repo)
    cmd_release_start(repo)
    cmd_release_candidate_cut(repo)  # rc.1, tags current HEAD

    # Move the release branch's tip past the RC tag (a real commit exists,
    # but it isn't an ancestor of a *second*, later-cut RC we didn't take).
    _write_commit(repo, "more.py", "y = 1\n", "more release work")
    cmd_release_candidate_cut(repo)  # rc.2, tags this later commit

    from version.configstore import set_promoted_rc

    set_promoted_rc(repo, "release/0.1.0", 2)
    _git(["reset", "--hard", "v0.1.0-rc.1"], repo)

    result = cmd_release_finish(repo)
    assert result.success is False
    assert "not reachable" in result.message


def test_release_finish_conflict_supports_abort(tmp_path):
    repo = make_repo(tmp_path)
    _write_commit(repo, "shared.py", "a = 1\n", "master baseline")
    _git(["checkout", "dev"], repo)
    _git(["merge", "--ff-only", "master"], repo)
    cmd_release_start(repo)
    _write_commit(repo, "shared.py", "a = 2\n", "release change")

    _git(["checkout", "master"], repo)
    _write_commit(repo, "shared.py", "a = 3\n", "conflicting master change")
    _git(["checkout", "release/0.1.0"], repo)

    result = cmd_release_finish(repo)
    assert result.success is False

    abort_result = cmd_release_finish(repo, action="abort")
    assert abort_result.success
    assert current_branch(repo) == "release/0.1.0"
