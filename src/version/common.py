"""Shared utilities for Gitflow command implementations — ported from
`little-versions`' `commands/common.py`."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from version.configstore import load_config
from version.gitops import DirtyWorkingTreeError, MissingBranchError, branch_exists, is_dirty


@dataclass
class CommandResult:
    success: bool
    message: str
    data: dict | None = None


def require_clean_tree(repo_root: Path, force: bool = False) -> None:
    if not force and is_dirty(repo_root):
        raise DirtyWorkingTreeError("Working tree is dirty. Commit or stash changes first, or use --force.")


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
