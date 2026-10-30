"""`version.init` — ported from `little-versions`' `commands/init.py`, plus
one addition with no little-versions equivalent: installing the project's
git hooks (`sync_version_files.install_git_hooks`): `.githooks/pre-commit`,
which keeps VERSION correct on every working-branch commit, and
`.githooks/commit-msg`, which runs the policies gating `commit`."""

from __future__ import annotations

from pathlib import Path

from version.branches import INTEGRATION_BRANCHES, PRODUCTION_BRANCHES
from version.commands.sync_version_files import install_git_hooks
from version.common import CommandResult
from version.configstore import write_config
from version.gitops import branch_exists, has_commits, is_git_repo
from version.schema import default_config


def cmd_init(
    repo_root: Path,
    production_branch: str | None = None,
    integration_branch: str | None = None,
    project_id: str | None = None,
) -> CommandResult:
    if not is_git_repo(repo_root):
        return CommandResult(success=False, message=f"Not a git repository: {repo_root}")

    if not has_commits(repo_root):
        return CommandResult(success=False, message="Repository has no commits. Make an initial commit first.")

    if production_branch is None:
        production_branch = next((c for c in PRODUCTION_BRANCHES if branch_exists(repo_root, c)), None)
        if production_branch is None:
            return CommandResult(
                success=False, message=f"No production branch found. Create one of: {list(PRODUCTION_BRANCHES)}"
            )
    elif production_branch not in PRODUCTION_BRANCHES:
        return CommandResult(
            success=False,
            message=f"Invalid production branch {production_branch!r}. Must be one of: {list(PRODUCTION_BRANCHES)}",
        )
    elif not branch_exists(repo_root, production_branch):
        return CommandResult(success=False, message=f"Production branch {production_branch!r} does not exist.")

    if integration_branch is None:
        integration_branch = next((c for c in INTEGRATION_BRANCHES if branch_exists(repo_root, c)), None)
        if integration_branch is None:
            return CommandResult(
                success=False, message=f"No integration branch found. Create one of: {list(INTEGRATION_BRANCHES)}"
            )
    elif integration_branch not in INTEGRATION_BRANCHES:
        return CommandResult(
            success=False,
            message=f"Invalid integration branch {integration_branch!r}. Must be one of: {list(INTEGRATION_BRANCHES)}",
        )
    elif not branch_exists(repo_root, integration_branch):
        return CommandResult(success=False, message=f"Integration branch {integration_branch!r} does not exist.")

    cfg = default_config(production_branch=production_branch, integration_branch=integration_branch)
    if project_id:
        cfg["project_id"] = project_id
    write_config(repo_root, cfg)

    hook_installed = False
    if project_id:
        install_git_hooks(repo_root, project_id)
        hook_installed = True

    message = f"Initialized version control: production={production_branch!r}, integration={integration_branch!r}"
    if hook_installed:
        message += "; installed .githooks/pre-commit and .githooks/commit-msg (core.hooksPath)"
    return CommandResult(
        success=True,
        message=message,
        data={
            "production_branch": production_branch,
            "integration_branch": integration_branch,
            "hook_installed": hook_installed,
        },
    )
