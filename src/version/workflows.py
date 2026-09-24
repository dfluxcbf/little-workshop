"""Shared Gitflow workflow helpers — ported from `little-versions`'
`commands/workflows.py`."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from version.common import CommandResult, get_integration_branch, require_branch_exists, require_clean_tree, run_version_hooks
from version.configstore import load_config, set_issue_link
from version.gitops import (
    branch_exists,
    checkout,
    commit_if_dirty,
    create_and_checkout,
    current_branch,
    delete_branch,
    get_merge_base,
    merge_no_ff,
    no_requirement,
    run,
    squash_to_single_commit,
    write_file_in_repo,
)
from version.stages import GitflowVersion, bump_patch

VERSION_FILE = "VERSION"


def read_version(repo_root: Path) -> GitflowVersion:
    vfile = repo_root / VERSION_FILE
    if not vfile.exists():
        return GitflowVersion(0, 1, 0)
    return GitflowVersion.parse(vfile.read_text(encoding="utf-8").strip())


def write_version(repo_root: Path, version: GitflowVersion) -> Path:
    return write_file_in_repo(repo_root, VERSION_FILE, str(version) + "\n")


def create_prefixed_branch(
    repo_root: Path, *, prefix: str, short_name: str, source_branch: str, issue: int | None
) -> CommandResult:
    branch_name = f"{prefix}/{short_name}"
    if branch_exists(repo_root, branch_name):
        return CommandResult(success=False, message=f"Branch {branch_name!r} already exists.")

    create_and_checkout(repo_root, branch_name, source_branch)

    if issue is not None:
        set_issue_link(repo_root, branch_name, issue)
        commit_if_dirty(repo_root, no_requirement(f"chore(version): link issue #{issue} to {branch_name}"))

    return CommandResult(
        success=True,
        message=f"Created and checked out {branch_name!r} from {source_branch!r}.",
        data={"branch": branch_name, "issue": issue},
    )


def start_integration_branch(
    repo_root: Path, *, prefix: str, short_name: str, issue: int | None, force: bool = False
) -> CommandResult:
    require_clean_tree(repo_root, force)

    integ = get_integration_branch(repo_root)
    invalid = require_checked_out_branch(repo_root, integ)
    if invalid is not None:
        return invalid

    return create_prefixed_branch(repo_root, prefix=prefix, short_name=short_name, source_branch=integ, issue=issue)


def require_checked_out_branch(repo_root: Path, required_branch: str) -> CommandResult | None:
    current = current_branch(repo_root)
    if current != required_branch:
        return CommandResult(success=False, message=f"Must run from {required_branch!r}. Current branch: {current!r}")
    require_branch_exists(repo_root, required_branch)
    return None


@dataclass(frozen=True)
class PatchMergePlan:
    work_branch: str
    target_branch: str
    merge_message: str
    pre_merge_cleanup: Callable[[Path], None] | None = None
    pre_merge_commit_message: str | None = None


def squash_merge_and_bump_patch(repo_root: Path, plan: PatchMergePlan) -> CommandResult:
    if plan.pre_merge_cleanup is not None:
        plan.pre_merge_cleanup(repo_root)
    if plan.pre_merge_commit_message is not None:
        commit_if_dirty(repo_root, plan.pre_merge_commit_message)

    merge_base = get_merge_base(repo_root, "HEAD", plan.target_branch)
    squash_to_single_commit(repo_root, merge_base, plan.merge_message)

    checkout(repo_root, plan.target_branch)
    merge_no_ff(repo_root, plan.work_branch, plan.merge_message)

    current_ver = read_version(repo_root)
    new_ver = bump_patch(current_ver)
    write_version(repo_root, new_ver)
    hook_errors = run_version_hooks(repo_root, str(new_ver))
    commit_if_dirty(repo_root, no_requirement(f"chore: bump version to {new_ver}"))

    delete_branch_if_configured(repo_root, plan.work_branch)
    return success_with_hook_warnings(
        message=f"Merged {plan.work_branch!r} into {plan.target_branch!r}. Version bumped to {new_ver}.",
        hook_errors=hook_errors,
        data={"merged_branch": plan.work_branch, "new_version": str(new_ver)},
    )


def delete_branch_if_configured(repo_root: Path, branch_name: str) -> None:
    cfg = load_config(repo_root)
    if cfg.get("delete_branch_on_finish", True):
        delete_branch(repo_root, branch_name, force=True)


def success_with_hook_warnings(*, message: str, hook_errors: list[str], data: dict | None = None) -> CommandResult:
    if hook_errors:
        message += "\nWarning: some version hooks failed:\n" + "\n".join(hook_errors)
    return CommandResult(success=True, message=message, data=data)


def merge_conflict_result(exc: Exception, continue_cmd: str, abort_cmd: str) -> CommandResult:
    return CommandResult(
        success=False,
        message=f"{exc}\nResolve conflicts, stage the files, then run:\n  {continue_cmd}\nTo abort, run:\n  {abort_cmd}",
    )


def clear_untracked_state_file(repo_root: Path) -> None:
    state_file = repo_root / ".lvx" / "state.json"
    if not state_file.exists():
        return
    tracked = run(["git", "ls-files", "--error-unmatch", ".lvx/state.json"], repo_root, check=False)
    if tracked.returncode != 0:
        state_file.unlink()


def checkout_existing_branch(repo_root: Path, branch_name: str) -> None:
    if branch_exists(repo_root, branch_name):
        checkout(repo_root, branch_name)
