"""version.common's `run_version_hooks` (never exercised — no test ran a
real hook script before) and version.workflows' less-common branches
(already-exists guard, wrong-branch guard, hook-warning message, the
untracked-state-file cleanup, checkout_existing_branch's no-op case), plus
version.stages' one remaining gap (`rc_tag()` on a non-RC version)."""

import subprocess

import pytest
from version_repo_helper import make_repo

from version.common import get_production_branch, require_branch_exists, run_version_hooks
from version.configstore import add_version_hook, load_config
from version.gitops import MissingBranchError, current_branch
from version.stages import GitflowVersion
from version.workflows import (
    checkout_existing_branch,
    clear_untracked_state_file,
    create_prefixed_branch,
    require_checked_out_branch,
    success_with_hook_warnings,
)


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def test_get_production_branch_reads_from_config(tmp_path):
    repo = make_repo(tmp_path)
    assert get_production_branch(repo) == "master"


def test_require_branch_exists_raises_for_missing_branch(tmp_path):
    repo = make_repo(tmp_path)
    with pytest.raises(MissingBranchError):
        require_branch_exists(repo, "does-not-exist")


def test_run_version_hooks_runs_a_real_sh_hook(tmp_path):
    repo = make_repo(tmp_path)
    hooks_dir = repo / "hooks"
    hooks_dir.mkdir()
    marker = repo / "hook_ran.txt"
    (hooks_dir / "sync.sh").write_text(
        f"#!/bin/sh\necho \"$LVX_NEW_VERSION\" > {marker}\n", encoding="utf-8"
    )
    add_version_hook(repo, "hooks/sync.sh")

    errors = run_version_hooks(repo, "1.2.3")
    assert errors == []
    assert marker.read_text().strip() == "1.2.3"


def test_run_version_hooks_runs_a_real_py_hook(tmp_path):
    repo = make_repo(tmp_path)
    hooks_dir = repo / "hooks"
    hooks_dir.mkdir()
    marker = repo / "hook_ran.txt"
    (hooks_dir / "bump.py").write_text(
        f"import os, pathlib\npathlib.Path(r'{marker}').write_text(os.environ['LVX_NEW_VERSION'])\n",
        encoding="utf-8",
    )
    add_version_hook(repo, "hooks/bump.py")

    errors = run_version_hooks(repo, "2.0.0")
    assert errors == []
    assert marker.read_text().strip() == "2.0.0"


def test_run_version_hooks_reports_unknown_extension(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "hooks").mkdir()
    (repo / "hooks" / "thing.exe").write_text("x", encoding="utf-8")
    cfg = load_config(repo)
    cfg["version_hooks"] = ["hooks/thing.exe"]
    from version.configstore import write_config

    write_config(repo, cfg)

    errors = run_version_hooks(repo, "1.0.0")
    assert len(errors) == 1
    assert "Unknown hook type" in errors[0]


def test_run_version_hooks_reports_nonzero_exit(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "hooks").mkdir()
    (repo / "hooks" / "fail.sh").write_text("#!/bin/sh\nexit 3\n", encoding="utf-8")
    add_version_hook(repo, "hooks/fail.sh")

    errors = run_version_hooks(repo, "1.0.0")
    assert len(errors) == 1
    assert "exited 3" in errors[0]


def test_run_version_hooks_resolves_absolute_paths(tmp_path):
    repo = make_repo(tmp_path)
    hooks_dir = repo / "hooks"
    hooks_dir.mkdir()
    marker = repo / "ran.txt"
    hook = hooks_dir / "abs.sh"
    hook.write_text(f"#!/bin/sh\ntouch {marker}\n", encoding="utf-8")
    add_version_hook(repo, str(hook))

    errors = run_version_hooks(repo, "1.0.0")
    assert errors == []
    assert marker.exists()


def test_create_prefixed_branch_refuses_when_already_exists(tmp_path):
    repo = make_repo(tmp_path)
    _git(["checkout", "-b", "feature/widgets", "dev"], repo)
    _git(["checkout", "dev"], repo)

    result = create_prefixed_branch(repo, prefix="feature", short_name="widgets", source_branch="dev", issue=None)
    assert result.success is False
    assert "already exists" in result.message


def test_create_prefixed_branch_links_issue_and_commits(tmp_path):
    repo = make_repo(tmp_path)
    _git(["checkout", "dev"], repo)

    result = create_prefixed_branch(repo, prefix="feature", short_name="widgets", source_branch="dev", issue=7)
    assert result.success
    assert result.data["issue"] == 7

    from version.configstore import get_issue_link

    assert get_issue_link(repo, "feature/widgets") == 7


def test_require_checked_out_branch_fails_on_wrong_branch(tmp_path):
    repo = make_repo(tmp_path)
    result = require_checked_out_branch(repo, "dev")
    assert result is not None
    assert result.success is False
    assert "Must run from" in result.message


def test_require_checked_out_branch_passes_on_correct_branch(tmp_path):
    repo = make_repo(tmp_path)
    _git(["checkout", "dev"], repo)
    assert require_checked_out_branch(repo, "dev") is None


def test_success_with_hook_warnings_appends_warning_text():
    result = success_with_hook_warnings(message="base message", hook_errors=["hook X failed"])
    assert result.success is True
    assert "base message" in result.message
    assert "hook X failed" in result.message


def test_success_with_hook_warnings_no_errors_leaves_message_unchanged():
    result = success_with_hook_warnings(message="base message", hook_errors=[])
    assert result.message == "base message"


def test_clear_untracked_state_file_removes_untracked_state(tmp_path):
    repo = make_repo(tmp_path)
    state_file = repo / ".lvx" / "state.json"
    state_file.write_text("{}", encoding="utf-8")
    assert state_file.exists()

    clear_untracked_state_file(repo)
    assert not state_file.exists()


def test_clear_untracked_state_file_keeps_tracked_state(tmp_path):
    repo = make_repo(tmp_path)
    state_file = repo / ".lvx" / "state.json"
    state_file.write_text("{}", encoding="utf-8")
    _git(["add", ".lvx/state.json"], repo)
    _git(["commit", "-m", "track state (unusual, but exercise the code path)"], repo)

    clear_untracked_state_file(repo)
    assert state_file.exists()  # tracked, so left alone


def test_clear_untracked_state_file_is_a_noop_when_file_absent(tmp_path):
    repo = make_repo(tmp_path)
    clear_untracked_state_file(repo)  # must not raise


def test_checkout_existing_branch_switches_when_present(tmp_path):
    repo = make_repo(tmp_path)
    _git(["checkout", "-b", "feature/widgets", "dev"], repo)
    _git(["checkout", "dev"], repo)

    checkout_existing_branch(repo, "feature/widgets")
    assert current_branch(repo) == "feature/widgets"


def test_checkout_existing_branch_is_a_noop_when_branch_missing(tmp_path):
    repo = make_repo(tmp_path)
    _git(["checkout", "dev"], repo)

    checkout_existing_branch(repo, "feature/ghost")
    assert current_branch(repo) == "dev"  # unchanged, no raise


def test_rc_tag_raises_for_a_non_rc_version():
    stable = GitflowVersion(1, 0, 0)
    with pytest.raises(ValueError, match="Not an RC version"):
        stable.rc_tag()
