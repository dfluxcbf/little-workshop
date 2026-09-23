"""Worktree provisioning as a shared capability (docs/milestones.md M4)
— the thing `docs/pillars/policy_compliance.md`'s test-script sandbox
and Requirements & Quality's sandboxed test execution both call into
later (M9, M11) rather than each provisioning their own. Deployment &
Servers' own deploy flow (`flow.py`) is just the first caller.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from common.paths import Paths
from deployment import gitutil


class WorktreeError(Exception):
    pass


def new_worktree_id() -> str:
    return uuid.uuid4().hex


def provision(paths: Paths, project_dir: Path, commitish: str, *, worktree_id: str | None = None) -> tuple[str, Path]:
    """Checks out `commitish` into a fresh, isolated worktree — never the
    project's own directory (a user or Claude actively working there, on
    any branch, is never affected). Returns (worktree_id, path)."""
    worktree_id = worktree_id or new_worktree_id()
    worktree = paths.worktree_path(worktree_id)
    gitutil.add_worktree(project_dir, commitish, worktree)
    return worktree_id, worktree


def release(paths: Paths, project_dir: Path, worktree_id: str) -> None:
    worktree = paths.worktree_path(worktree_id)
    gitutil.remove_worktree(project_dir, worktree)
