"""Shared Gitflow workflow helpers — ported from `little-versions`'
`commands/workflows.py`."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from common.job_steps import StepFailed, done, step
from version.common import CommandResult, ensure_clean_tree, get_integration_branch, require_branch_exists, run_version_hooks
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
from version.stages import GitflowVersion, bump_patch, normalize_to_stable

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
    try:
        with step("create_branch") as handle:
            if branch_exists(repo_root, branch_name):
                raise StepFailed(f"Branch {branch_name!r} already exists.")
            create_and_checkout(repo_root, branch_name, source_branch)
            handle.detail = f"{branch_name} from {source_branch}"
    except StepFailed as exc:
        return CommandResult(success=False, message=str(exc))

    if issue is not None:
        with step("link_issue") as handle:
            set_issue_link(repo_root, branch_name, issue)
            commit_if_dirty(repo_root, no_requirement(f"chore(version): link issue #{issue} to {branch_name}"))
            handle.detail = f"issue #{issue}"
    else:
        done("link_issue", "no issue given")

    return CommandResult(
        success=True,
        message=f"Created and checked out {branch_name!r} from {source_branch!r}.",
        data={"branch": branch_name, "issue": issue},
    )


def start_integration_branch(
    repo_root: Path, *, prefix: str, short_name: str, issue: int | None, force: bool = False
) -> CommandResult:
    ensure_clean_tree(repo_root, force, commit_message=f"chore: commit pending changes before starting {prefix}/{short_name}")

    integ = get_integration_branch(repo_root)
    invalid = require_checked_out_branch(repo_root, integ)
    if invalid is not None:
        return invalid

    return create_prefixed_branch(repo_root, prefix=prefix, short_name=short_name, source_branch=integ, issue=issue)


def require_checked_out_branch(repo_root: Path, required_branch: str) -> CommandResult | None:
    """None when `required_branch` is checked out; otherwise a failure result (its `check_branch` step failed)."""
    try:
        with step("check_branch") as handle:
            current = current_branch(repo_root)
            if current != required_branch:
                raise StepFailed(f"Must run from {required_branch!r}. Current branch: {current!r}")
            require_branch_exists(repo_root, required_branch)
            handle.detail = f"on {required_branch}"
    except StepFailed as exc:
        return CommandResult(success=False, message=str(exc))
    return None


@dataclass(frozen=True)
class PatchMergePlan:
    work_branch: str
    target_branch: str
    merge_message: str
    pre_merge_cleanup: Callable[[Path], None] | None = None
    pre_merge_commit_message: str | None = None


def finished_version(merged: GitflowVersion, target_before: GitflowVersion) -> GitflowVersion:
    """The version a finished working branch lands on. A branch already
    working toward the next patch (its commits carry `X.Y.Z-dev.N`, per
    `branch-commit-dev-suffix`) lands on `X.Y.Z` itself; one that never
    bumped gets the next patch. Either way, strictly above what the target
    carried before the merge. Always bumping the patch here (little-versions'
    rule, from before branches bumped on their own) skipped one patch per
    finish -- 0.2.6 -> 0.2.8 -- and doubled the Release job's patch count."""
    candidate = normalize_to_stable(merged) if merged.is_dev() else bump_patch(merged)
    floor = normalize_to_stable(target_before)
    if (candidate.major, candidate.minor, candidate.patch) <= (floor.major, floor.minor, floor.patch):
        candidate = bump_patch(floor)
    return candidate


def squash_merge_and_bump_patch(repo_root: Path, plan: PatchMergePlan) -> CommandResult:
    with step("squash") as handle:
        if plan.pre_merge_cleanup is not None:
            plan.pre_merge_cleanup(repo_root)
        if plan.pre_merge_commit_message is not None:
            commit_if_dirty(repo_root, plan.pre_merge_commit_message)

        merge_base = get_merge_base(repo_root, "HEAD", plan.target_branch)
        squash_to_single_commit(repo_root, merge_base, plan.merge_message)
        handle.detail = plan.merge_message

    with step("merge") as handle:
        checkout(repo_root, plan.target_branch)
        target_ver = read_version(repo_root)
        merge_no_ff(repo_root, plan.work_branch, plan.merge_message)
        handle.detail = f"{plan.work_branch} into {plan.target_branch}"

    with step("bump_version") as handle:
        new_ver = finished_version(read_version(repo_root), target_ver)
        write_version(repo_root, new_ver)
        hook_errors = run_version_hooks(repo_root, str(new_ver))
        commit_if_dirty(repo_root, no_requirement(f"chore: bump version to {new_ver}"))
        handle.detail = f"{target_ver} -> {new_ver}"

    with step("delete_branch") as handle:
        delete_branch_if_configured(repo_root, plan.work_branch)
        handle.detail = plan.work_branch
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
