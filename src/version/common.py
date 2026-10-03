"""Shared utilities for Gitflow command implementations — ported from
`little-versions`' `commands/common.py`."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from common.job_steps import Option, StepFailed, ask, can_ask, step
from version.configstore import load_config
from version.gitops import (
    DirtyWorkingTreeError, GitError, MissingBranchError, branch_exists, commit_if_dirty, conflicted_paths, dirty_paths, is_dirty,
    no_requirement,
)


@dataclass
class CommandResult:
    success: bool
    message: str
    data: dict | None = None


def require_clean_tree(repo_root: Path, force: bool = False) -> None:
    if not force and is_dirty(repo_root):
        raise DirtyWorkingTreeError("Working tree is dirty. Commit or stash changes first, or use --force.")


def _listed(paths: list[str], limit: int = 8) -> str:
    shown = ", ".join(paths[:limit])
    return shown + (f" and {len(paths) - limit} more" if len(paths) > limit else "")


def ensure_clean_tree(repo_root: Path, force: bool, *, commit_message: str, key: str = "check_clean_tree") -> None:
    """The "uncommitted changes" step. Run as a job the user is asked how to go on (commit them and
    continue, continue without, or stop); run directly, a dirty tree raises as `require_clean_tree` does."""
    with step(key) as handle:
        paths = dirty_paths(repo_root)
        if not paths:
            handle.detail = "no uncommitted changes"
            return
        if force:
            handle.detail = f"continuing past {len(paths)} uncommitted change(s) (--force)"
            return
        if not can_ask():
            raise DirtyWorkingTreeError("Working tree is dirty. Commit or stash changes first, or use --force.")
        choice = ask(key, f"{len(paths)} uncommitted change(s) in {repo_root}: {_listed(paths)}. How should this go on?", [
            Option("commit", "Commit the changes and continue"),
            Option("force", "Continue without committing them"),
            Option("stop", "Stop here and fail the job"),
        ])
        if choice == "commit":
            commit_if_dirty(repo_root, no_requirement(commit_message))
            handle.detail = f"committed {len(paths)} change(s) and continued"
        elif choice == "force":
            handle.detail = f"continued past {len(paths)} uncommitted change(s)"
        else:
            raise StepFailed("stopped by the user: the working tree has uncommitted changes")


def resolve_conflicts_with_user(
    repo_root: Path, key: str, where: str, *, resume: Callable[[], None], abort: Callable[[], None], first_error: str,
) -> None:
    """The "resolve conflicts" step, once `where` (a rebase or merge) stopped on conflicts the code cannot
    settle itself. The user resolves and stages the files, then chooses to continue (`resume` carries the
    operation on and raises `GitError` while conflicts remain) or to abort (`abort` restores the checkout and the
    job fails). Without a user to ask, the conflict error is raised as it always was."""
    if not can_ask():
        raise GitError(first_error)
    with step(key) as handle:
        problem = first_error
        while True:
            files = conflicted_paths(repo_root)
            listed = f" Conflicted files: {_listed(files)}." if files else ""
            choice = ask(key, f"{problem}{listed} Resolve them in {repo_root}, stage them (git add), then continue.", [
                Option("continue", "I resolved the conflicts and staged them: continue"),
                Option("abort", "Abort and restore the repository (fails the job)"),
            ])
            if choice == "abort":
                abort()
                raise StepFailed(f"aborted by the user while resolving conflicts in {where}")
            try:
                resume()
            except GitError as exc:
                problem = str(exc)
                continue
            handle.detail = f"conflicts in {where} resolved by the user"
            return


def require_branch_exists(repo_root: Path, name: str) -> None:
    if not branch_exists(repo_root, name):
        raise MissingBranchError(f"Required branch {name!r} does not exist.")


def get_integration_branch(repo_root: Path) -> str:
    return load_config(repo_root)["integration_branch"]


def get_production_branch(repo_root: Path) -> str:
    return load_config(repo_root)["production_branch"]


def run_version_hooks(repo_root: Path, new_version: str) -> list[str]:
    cfg = load_config(repo_root)
    hooks: list[str] = cfg.get("version_hooks", [])
    errors: list[str] = []
    env = {**os.environ, "LVX_NEW_VERSION": new_version}
    for hook_path in hooks:
        resolved_hook = Path(hook_path)
        if not resolved_hook.is_absolute():
            resolved_hook = repo_root / resolved_hook
        suffix = Path(hook_path).suffix.lower()
        if suffix == ".sh":
            cmd = ["sh", str(resolved_hook), new_version]
        elif suffix == ".py":
            cmd = ["python3", str(resolved_hook), new_version]
        else:
            errors.append(f"Unknown hook type (skipped): {hook_path!r}")
            continue
        result = subprocess.run(cmd, env=env, cwd=str(repo_root), check=False, capture_output=True, text=True)
        if result.returncode != 0:
            errors.append(f"Hook {hook_path!r} exited {result.returncode}: {result.stderr.strip() or result.stdout.strip()}")
    return errors
