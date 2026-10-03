"""`feature`/`improvement` finish logic — ported from `little-versions`'
`commands/feature.py` and `commands/improvement.py`. The two were
already near-identical there (`improvement` is explicitly documented as
"a project-specific extension treated as an alias of feature with commit
prefix 'impr'"); parameterizing by prefix + commit label here removes
that duplication instead of carrying it forward.
"""

from __future__ import annotations

from pathlib import Path

from common.job_steps import StepFailed, can_ask, done, step
from version.common import CommandResult, ensure_clean_tree, get_integration_branch, require_branch_exists, resolve_conflicts_with_user
from version.configstore import clear_finish_state, get_issue_link, load_finish_state, save_finish_state
from version.gitops import RebaseConflictError, commit_if_dirty, current_branch, is_rebase_in_progress, rebase_abort, rebase_continue, rebase_onto, no_requirement
from version.workflows import PatchMergePlan, squash_merge_and_bump_patch, start_integration_branch


def cmd_topic_start(
    repo_root: Path, *, prefix: str, name: str, issue: int | None = None, force: bool = False
) -> CommandResult:
    """Create and checkout `<prefix>/<name>` from the integration branch."""
    return start_integration_branch(repo_root, prefix=prefix, short_name=name, issue=issue, force=force)


def _finish_squash_merge_bump(repo_root: Path, work_branch: str, integ: str, merge_message: str) -> CommandResult:
    return squash_merge_and_bump_patch(
        repo_root,
        PatchMergePlan(
            work_branch=work_branch,
            target_branch=integ,
            merge_message=merge_message,
            pre_merge_cleanup=clear_finish_state,
            pre_merge_commit_message=no_requirement("chore(version): clear finish state"),
        ),
    )


def cmd_topic_finish(
    repo_root: Path, *, prefix: str, commit_label: str, force: bool = False, action: str | None = None
) -> CommandResult:
    try:
        return _topic_finish(repo_root, prefix=prefix, commit_label=commit_label, force=force, action=action)
    except StepFailed as exc:
        return CommandResult(success=False, message=str(exc))


def _topic_finish(
    repo_root: Path, *, prefix: str, commit_label: str, force: bool = False, action: str | None = None
) -> CommandResult:
    """Rebase, squash, merge `<prefix>/*` into the integration branch, bump
    patch, delete the branch.

    action=None       : normal start (rebase then squash+merge)
    action="continue" : resume after conflict resolution
    action="abort"    : abort the in-progress rebase
    """
    if action == "abort":
        finish_state = load_finish_state(repo_root)
        if not finish_state and not is_rebase_in_progress(repo_root):
            return CommandResult(success=False, message="No finish in progress to abort.")
        rebase_abort(repo_root)
        clear_finish_state(repo_root)
        work_branch = (finish_state or {}).get("work_branch", f"{prefix} branch")
        # clear_finish_state rewrites .lvx/state.json but never committed
        # it -- left dirty, this genuinely blocked switching away from the
        # work branch right after an abort ("Repository restored" wasn't
        # actually true). hotfix's own abort path already commits its
        # equivalent cleanup; this one didn't.
        commit_if_dirty(repo_root, no_requirement(f"chore(version): abort finish for {work_branch}"))
        return CommandResult(success=True, message=f"Aborted finish for {work_branch!r}. Repository restored.")

    if action == "continue":
        # Drain any in-progress rebase *before* reading the saved state: the
        # state-save commit is the newest commit on the work branch, so
        # `git rebase` (which replays one commit's diff at a time against a
        # moving base, unlike a merge) won't have restored `.lvx/state.json`
        # to the working tree until every earlier commit's conflicts are
        # resolved and it's actually replayed. Reading state first would
        # spuriously report "no finish in progress" the moment the conflict
        # lands on any commit older than the state-save one.
        if is_rebase_in_progress(repo_root):
            try:
                rebase_continue(repo_root)
            except RebaseConflictError as exc:
                return CommandResult(success=False, message=str(exc))

        finish_state = load_finish_state(repo_root)
        if not finish_state:
            return CommandResult(success=False, message=f"No finish in progress. Run '{prefix} finish' first.")
        work_branch = finish_state["work_branch"]
        integ = finish_state["integration_branch"]
        merge_message = finish_state["merge_message"]

        return _finish_squash_merge_bump(repo_root, work_branch, integ, merge_message)

    # Normal start path
    cur = current_branch(repo_root)
    ensure_clean_tree(repo_root, force, commit_message=f"chore: commit pending changes before finishing {cur}")

    with step("check_branch") as handle:
        cur = current_branch(repo_root)
        if not cur.startswith(f"{prefix}/"):
            raise StepFailed(f"Must run from a {prefix}/* branch. Current: {cur!r}")
        integ = get_integration_branch(repo_root)
        require_branch_exists(repo_root, integ)
        handle.detail = f"{cur} -> {integ}"

    short_name = cur[len(prefix) + 1 :]
    issue = get_issue_link(repo_root, cur)
    issue_label = str(issue) if issue is not None else "-1"
    merge_message = f"{commit_label} ({issue_label}): {short_name}"

    with step("save_state"):
        save_finish_state(repo_root, cur, integ, merge_message)
        commit_if_dirty(repo_root, no_requirement("chore(version): save finish state"))

    with step("rebase") as handle:
        try:
            rebase_onto(repo_root, integ)
            handle.detail = f"onto {integ}"
        except RebaseConflictError as exc:
            if not can_ask():
                return CommandResult(success=False, message=str(exc))
            handle.detail = "stopped on conflicts"
            conflict = exc
        else:
            conflict = None
    if conflict is None:
        done("resolve_conflicts", "no conflicts")
    else:
        resolve_conflicts_with_user(
            repo_root, "resolve_conflicts", f"the rebase onto {integ}", resume=lambda: rebase_continue(repo_root),
            abort=lambda: _abort_finish(repo_root, cur), first_error=str(conflict),
        )

    return _finish_squash_merge_bump(repo_root, cur, integ, merge_message)


def _abort_finish(repo_root: Path, work_branch: str) -> None:
    if is_rebase_in_progress(repo_root):
        rebase_abort(repo_root)
    clear_finish_state(repo_root)
    commit_if_dirty(repo_root, no_requirement(f"chore(version): abort finish for {work_branch}"))


def cmd_topic_status(repo_root: Path, *, prefix: str) -> CommandResult:
    cur = current_branch(repo_root)
    if not cur.startswith(f"{prefix}/"):
        return CommandResult(success=False, message=f"Not on a {prefix}/* branch. Current: {cur!r}")
    issue = get_issue_link(repo_root, cur)
    return CommandResult(success=True, message=f"{prefix.capitalize()} branch: {cur!r}", data={"branch": cur, "issue": issue})
