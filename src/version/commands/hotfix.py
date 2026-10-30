"""`hotfix` command implementations — ported from `little-versions`'
`commands/hotfix.py`."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from common.job_steps import StepFailed, can_ask, done, step
from version.common import CommandResult, ensure_clean_tree, get_integration_branch, get_production_branch, require_branch_exists, resolve_conflicts_with_user, run_version_hooks
from version.configstore import clear_hotfix_finish_state, get_issue_link, load_hotfix_finish_state, save_hotfix_finish_state
from version.gitops import MergeConflictError, checkout, commit_if_dirty, create_annotated_tag, current_branch, is_merge_in_progress, merge_abort, merge_commit, merge_no_ff, no_requirement
from version.stages import GitflowVersion, bump_patch
from version.workflows import checkout_existing_branch, clear_untracked_state_file, create_prefixed_branch, delete_branch_if_configured, merge_conflict_result, read_version, require_checked_out_branch, success_with_hook_warnings, write_version


def cmd_hotfix_start(repo_root: Path, name: str, issue: int | None = None, force: bool = False) -> CommandResult:
    ensure_clean_tree(repo_root, force, commit_message=f"chore: commit pending changes before starting hotfix/{name}")
    prod = get_production_branch(repo_root)
    invalid = require_checked_out_branch(repo_root, prod)
    if invalid is not None:
        return invalid
    return create_prefixed_branch(repo_root, prefix="hotfix", short_name=name, source_branch=prod, issue=issue)


def _hotfix_finish_post_prod_merge(
    repo_root: Path, hotfix_branch: str, prod: str, integ: str, new_ver: GitflowVersion
) -> CommandResult:
    with step("bump_version") as handle:
        old_ver = read_version(repo_root)
        write_version(repo_root, new_ver)
        hook_errors = run_version_hooks(repo_root, str(new_ver))
        commit_if_dirty(repo_root, no_requirement(f"chore: bump version to {new_ver}"))
        handle.detail = f"{old_ver} -> {new_ver}"

    tag = new_ver.production_tag()
    with step("tag") as handle:
        create_annotated_tag(repo_root, tag, f"Hotfix release {new_ver}")
        handle.detail = tag

    save_hotfix_finish_state(
        repo_root,
        hotfix_branch=hotfix_branch,
        production_branch=prod,
        integration_branch=integ,
        merge_message=f"Back-merge hotfix {hotfix_branch[len('hotfix/'):]} into {integ}",
        new_version=str(new_ver),
        phase="dev_backmerge",
    )
    commit_if_dirty(repo_root, no_requirement("chore(version): advance hotfix finish state to dev_backmerge"))

    back_message = f"Back-merge hotfix {hotfix_branch[len('hotfix/'):]} into {integ}"
    with step("merge_integration") as handle:
        checkout(repo_root, integ)
        handle.detail = f"{hotfix_branch} into {integ}"
        conflict = None
        try:
            merge_no_ff(repo_root, hotfix_branch, back_message)
        except MergeConflictError as exc:
            if not can_ask():
                return merge_conflict_result(exc, continue_cmd="version hotfix finish --continue", abort_cmd="version hotfix finish --abort")
            handle.detail = "stopped on conflicts"
            conflict = exc
    if conflict is None:
        done("resolve_integration_conflicts", "no conflicts")
    else:
        resolve_conflicts_with_user(
            repo_root, "resolve_integration_conflicts", f"the back-merge into {integ}",
            resume=lambda: merge_commit(repo_root, back_message), abort=lambda: _abort_hotfix(repo_root, hotfix_branch), first_error=str(conflict),
        )

    with step("delete_branch") as handle:
        delete_branch_if_configured(repo_root, hotfix_branch)
        clear_hotfix_finish_state(repo_root)
        commit_if_dirty(repo_root, no_requirement("chore(version): clear hotfix finish state"))
        handle.detail = hotfix_branch

    return success_with_hook_warnings(
        message=f"Hotfix {hotfix_branch[len('hotfix/'):]} finished. Tag {tag} created on {prod}.",
        hook_errors=hook_errors,
        data={"hotfix": hotfix_branch[len("hotfix/"):], "tag": tag, "new_version": str(new_ver)},
    )


def _abort_hotfix(repo_root: Path, hotfix_branch: str) -> None:
    if is_merge_in_progress(repo_root):
        merge_abort(repo_root)
    clear_untracked_state_file(repo_root)
    checkout_existing_branch(repo_root, hotfix_branch)
    clear_hotfix_finish_state(repo_root)
    commit_if_dirty(repo_root, no_requirement("chore(version): abort hotfix finish"))


def cmd_hotfix_finish(repo_root: Path, force: bool = False, action: str | None = None) -> CommandResult:
    if action == "abort":
        finish_state = load_hotfix_finish_state(repo_root)
        if not finish_state and not is_merge_in_progress(repo_root):
            return CommandResult(success=False, message="No hotfix finish in progress to abort.")
        if is_merge_in_progress(repo_root):
            merge_abort(repo_root)
        hotfix_branch = (finish_state or {}).get("hotfix_branch", "hotfix branch")
        clear_untracked_state_file(repo_root)
        checkout_existing_branch(repo_root, hotfix_branch)
        clear_hotfix_finish_state(repo_root)
        commit_if_dirty(repo_root, no_requirement("chore(version): abort hotfix finish"))
        return CommandResult(success=True, message=f"Aborted hotfix finish for {hotfix_branch!r}. Repository restored.")

    if action == "continue":
        finish_state = load_hotfix_finish_state(repo_root)
        if not finish_state and not is_merge_in_progress(repo_root):
            return CommandResult(success=False, message="No hotfix finish in progress. Run 'version hotfix finish' first.")
        if not finish_state:
            return CommandResult(
                success=False,
                message="A merge is in progress but no hotfix finish state was found. Resolve manually or run 'git merge --abort'.",
            )

        hotfix_branch = finish_state["hotfix_branch"]
        prod = finish_state["production_branch"]
        integ = finish_state["integration_branch"]
        new_ver = GitflowVersion.parse(finish_state["new_version"])
        hotfix_name = hotfix_branch[len("hotfix/"):]
        prod_merge_message = finish_state["merge_message"]
        backmerge_message = f"Back-merge hotfix {hotfix_name} into {integ}"

        cur = current_branch(repo_root)

        if cur == prod:
            if is_merge_in_progress(repo_root):
                merge_commit(repo_root, prod_merge_message)
            return _hotfix_finish_post_prod_merge(repo_root, hotfix_branch, prod, integ, new_ver)

        if cur == integ:
            if is_merge_in_progress(repo_root):
                merge_commit(repo_root, backmerge_message)
            tag = new_ver.production_tag()
            delete_branch_if_configured(repo_root, hotfix_branch)
            clear_hotfix_finish_state(repo_root)
            commit_if_dirty(repo_root, no_requirement("chore(version): clear hotfix finish state"))
            return CommandResult(
                success=True,
                message=f"Hotfix {hotfix_name} finished. Tag {tag} created on {prod}.",
                data={"hotfix": hotfix_name, "tag": tag, "new_version": str(new_ver)},
            )

        return CommandResult(success=False, message=f"Cannot resume hotfix finish from {cur!r}. Switch to {prod!r} or {integ!r} and retry.")

    # --- Normal start path ---
    try:
        return _hotfix_finish_normal(repo_root, force)
    except StepFailed as exc:
        return CommandResult(success=False, message=str(exc))


def _hotfix_finish_normal(repo_root: Path, force: bool) -> CommandResult:
    ensure_clean_tree(repo_root, force, commit_message=f"chore: commit pending changes before finishing {current_branch(repo_root)}")

    with step("check_branch") as handle:
        cur = current_branch(repo_root)
        if not cur.startswith("hotfix/"):
            raise StepFailed(f"Must run from a hotfix/* branch. Current: {cur!r}")
        handle.detail = cur

    hotfix_name = cur[len("hotfix/"):]
    with step("check_branches") as handle:
        prod = get_production_branch(repo_root)
        integ = get_integration_branch(repo_root)
        require_branch_exists(repo_root, prod)
        require_branch_exists(repo_root, integ)
        handle.detail = f"production {prod}, integration {integ}"

    issue = get_issue_link(repo_root, cur)
    issue_label = str(issue) if issue is not None else "-1"
    merge_msg = f"fix ({issue_label}): merge {hotfix_name}"

    current_ver = read_version(repo_root)
    new_ver = bump_patch(current_ver)

    with step("save_state"):
        save_hotfix_finish_state(
            repo_root, hotfix_branch=cur, production_branch=prod, integration_branch=integ,
            merge_message=merge_msg, new_version=str(new_ver), phase="prod_merge",
        )
        commit_if_dirty(repo_root, no_requirement("chore(version): begin hotfix finish"))

    conflict = None
    with step("merge_production") as handle:
        checkout(repo_root, prod)
        handle.detail = f"{cur} into {prod}"
        try:
            merge_no_ff(repo_root, cur, merge_msg)
        except MergeConflictError as exc:
            if not can_ask():
                return merge_conflict_result(exc, continue_cmd="version hotfix finish --continue", abort_cmd="version hotfix finish --abort")
            handle.detail = "stopped on conflicts"
            conflict = exc
    if conflict is None:
        done("resolve_production_conflicts", "no conflicts")
    else:
        resolve_conflicts_with_user(
            repo_root, "resolve_production_conflicts", f"the merge into {prod}",
            resume=lambda: merge_commit(repo_root, merge_msg), abort=lambda: _abort_hotfix(repo_root, cur), first_error=str(conflict),
        )

    return _hotfix_finish_post_prod_merge(repo_root, cur, prod, integ, new_ver)


def cmd_hotfix_status(repo_root: Path) -> CommandResult:
    cur = current_branch(repo_root)
    if not cur.startswith("hotfix/"):
        return CommandResult(success=False, message=f"Not on a hotfix/* branch. Current: {cur!r}")
    issue = get_issue_link(repo_root, cur)
    return CommandResult(success=True, message=f"Hotfix branch: {cur!r}", data={"branch": cur, "issue": issue})
