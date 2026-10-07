"""M16 `little-runner` parity: `lrun stabilize run` — ported from the
ecosystem's own root `stabilize_branches.py`, scoped to one project's
own real checkout (Registry & Portfolio's project boundary) rather than
walking every submodule directly, exactly the same simplification
`version/branch_audit.py`'s own docstring already explains for its
sibling command.

Unlike `branch_audit`, this genuinely mutates: brings the integration
branch up to date with `origin`, commits pending changes, finishes the
currently-checked-out feature/improvement/hotfix branch (delegating to
this pillar's own already-existing `cmd_topic_finish`/`cmd_hotfix_finish`
— never a second implementation of that logic), pushes, and deletes
already-merged remote/local branches. `execute=False` (the default,
matching the original tool) is a dry run: every step is planned and
reported, nothing on disk or on `origin` actually changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from version.branches import Branch
from version.commands.hotfix import cmd_hotfix_finish
from version.commands.topic_branch import cmd_topic_finish
from version.common import get_integration_branch, get_production_branch
from version.gitops import (
    GitError,
    branch_exists,
    commit_if_dirty,
    checkout,
    current_branch,
    delete_branch,
    is_dirty,
    is_merged,
    is_merge_in_progress,
    is_rebase_in_progress,
    run as git_run,
)

PROTECTED_PREFIXES = ("release/", "support/")
PROTECTED_NAMES = {"dev", "develop", "master", "main"}

FINISH_HANDLERS = {
    "feature": lambda repo_root: cmd_topic_finish(repo_root, prefix="feature", commit_label="feat"),
    "improvement": lambda repo_root: cmd_topic_finish(repo_root, prefix="improvement", commit_label="impr"),
    "hotfix": lambda repo_root: cmd_hotfix_finish(repo_root),
}


@dataclass
class StabilizeResult:
    branch_before: str = ""
    was_dirty: bool = False
    committed: bool = False
    integration_updated: bool = False
    finished: bool | None = None
    branch_after: str = ""
    pushed: list[str] = field(default_factory=list)
    deleted_remote_branches: list[str] = field(default_factory=list)
    deleted_local_branches: list[str] = field(default_factory=list)
    status: str = "stabilized"  # stabilized | skipped | failed | needs_manual_resolution
    message: str = ""


def _classify(branch: str, integration: str, production: str) -> str:
    if branch == integration:
        return "integration"
    if branch == production or branch.startswith(PROTECTED_PREFIXES):
        return "protected"
    try:
        branch_type = Branch.parse(branch).type.value
    except ValueError:
        return "unknown"
    return branch_type if branch_type in FINISH_HANDLERS else "unknown"


def stabilize(repo_root: Path, *, execute: bool = False) -> StabilizeResult:
    result = StabilizeResult()

    if is_merge_in_progress(repo_root) or is_rebase_in_progress(repo_root):
        result.status = "needs_manual_resolution"
        result.message = "Repository has an in-progress rebase/merge with conflicts. Resolve by hand, not touched."
        return result

    integration = get_integration_branch(repo_root)
    production = get_production_branch(repo_root)

    try:
        started_on = current_branch(repo_root)
    except GitError as exc:
        result.status = "failed"
        result.message = str(exc)
        return result
    result.branch_before = started_on

    fetch = git_run(["git", "fetch", "origin", "--prune"], repo_root, check=False)
    if fetch.returncode != 0:
        result.status = "skipped"
        result.message = "git fetch failed, not touched (no remote?)."
        return result

    if started_on != integration and execute:
        checkout(repo_root, integration)
    if execute:
        ff = git_run(["git", "merge", "--ff-only", f"origin/{integration}"], repo_root, check=False)
        if ff.returncode != 0:
            result.status = "needs_manual_resolution"
            result.message = f"{integration!r} has diverged from origin — not a fast-forward."
            if started_on != integration:
                checkout(repo_root, started_on)
            return result
        result.integration_updated = True
        if started_on != integration:
            checkout(repo_root, started_on)
    else:
        behind = git_run(["git", "rev-list", "--count", f"{integration}..origin/{integration}"], repo_root, check=False)
        result.integration_updated = behind.returncode == 0 and int(behind.stdout.strip() or 0) > 0

    result.was_dirty = is_dirty(repo_root)
    if result.was_dirty:
        if execute:
            sha = commit_if_dirty(repo_root, "chore(stabilize): commit pending changes")
            result.committed = bool(sha)
        else:
            result.committed = True

    branch_type = _classify(started_on, integration, production)

    if branch_type == "integration":
        result.branch_after = integration
    elif branch_type == "protected":
        result.status = "skipped"
        result.message = f"{started_on!r} is a long-lived branch — not auto-finished."
        return result
    elif branch_type == "unknown":
        result.status = "skipped"
        result.message = f"{started_on!r} does not match a recognized branch-type prefix — not touched."
        return result
    else:
        if execute:
            finish_result = FINISH_HANDLERS[branch_type](repo_root)
            if not finish_result.success:
                result.status = "needs_manual_resolution"
                result.message = finish_result.message
                return result
            result.finished = True
            result.branch_after = current_branch(repo_root)
        else:
            result.finished = True
            result.branch_after = integration

    branches_to_push = [integration]
    if branch_type == "hotfix":
        branches_to_push.append(production)
    for branch in branches_to_push:
        if execute:
            push = git_run(["git", "push", "origin", branch], repo_root, check=False)
            if push.returncode == 0:
                result.pushed.append(branch)
        else:
            result.pushed.append(branch)

    for base in {integration, production}:
        # Merged by history, or squash-merged (a finish's commits never become ancestors of the target).
        remotes = git_run(["git", "branch", "-r"], repo_root, check=False)
        for line in remotes.stdout.splitlines():
            ref = line.strip()
            if "->" in ref or not ref.startswith("origin/") or not is_merged(repo_root, ref, f"origin/{base}"):
                continue
            name = ref[len("origin/"):]
            if name in PROTECTED_NAMES or name.startswith(PROTECTED_PREFIXES) or name in {integration, production}:
                continue
            if name in result.deleted_remote_branches:
                continue  # already handled via the other base
            if execute:
                deleted = git_run(["git", "push", "origin", "--delete", name], repo_root, check=False)
                if deleted.returncode == 0:
                    result.deleted_remote_branches.append(name)
            else:
                result.deleted_remote_branches.append(name)
            if branch_exists(repo_root, name):
                if execute:
                    delete_branch(repo_root, name, force=True)
                result.deleted_local_branches.append(name)

    return result
