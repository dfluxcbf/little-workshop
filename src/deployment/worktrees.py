"""Worktree provisioning as a shared capability (docs/milestones.md M4)
— the thing `docs/pillars/policy_compliance.md`'s test-script sandbox
and Requirements & Quality's sandboxed test execution both call into
later (M9, M11) rather than each provisioning their own. Deployment &
Servers' own deploy flow (`flow.py`) is just the first caller.

A worktree is detached by default. Given a `branch`, it is checked out on
that branch instead (created from `commitish` when `new_branch`): an agent
task works and commits there (REQ-000000518). `on_windows` places a
Windows-hosted project's worktree on its own drive, under
`LWS_WINDOWS_WORKTREE_ROOT` (`C:\\little-workshop\\worktrees` by default),
where `git.exe` creates and owns it.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

from common import windows
from common.paths import Paths
from deployment import gitutil

DEFAULT_WINDOWS_WORKTREE_ROOT = "C:\\little-workshop\\worktrees"


class WorktreeError(Exception):
    pass


def new_worktree_id() -> str:
    return uuid.uuid4().hex


def windows_worktree_root() -> str:
    return os.environ.get("LWS_WINDOWS_WORKTREE_ROOT") or DEFAULT_WINDOWS_WORKTREE_ROOT


def worktree_path(paths: Paths, project_id: str, worktree_id: str, *, on_windows: bool = False) -> Path:
    """Where a worktree lives: the state home's `worktrees/`, or for `on_windows` the Windows worktree root."""
    if on_windows:
        return Path(windows.to_wsl_path(windows_worktree_root(), windows.mount_root())) / project_id.replace("/", "-") / worktree_id
    return paths.worktree_path(worktree_id)


def provision(
    paths: Paths, project_dir: Path, commitish: str, *, worktree_id: str | None = None, project_id: str = "",
    branch: str | None = None, new_branch: bool = False, on_windows: bool = False,
) -> tuple[str, Path]:
    """Checks out `commitish` into a fresh, isolated worktree — never the
    project's own directory (a user or Claude actively working there, on
    any branch, is never affected). Returns (worktree_id, path)."""
    worktree_id = worktree_id or new_worktree_id()
    worktree = worktree_path(paths, project_id, worktree_id, on_windows=on_windows and windows.is_windows_project(project_dir))
    if branch:
        gitutil.add_branch_worktree(project_dir, branch, worktree, base=commitish if new_branch else None)
    else:
        gitutil.add_worktree(project_dir, commitish, worktree)
    return worktree_id, worktree


def release(paths: Paths, project_dir: Path, worktree_id: str, *, project_id: str = "", on_windows: bool = False) -> None:
    worktree = worktree_path(paths, project_id, worktree_id, on_windows=on_windows and windows.is_windows_project(project_dir))
    gitutil.remove_worktree(project_dir, worktree)
