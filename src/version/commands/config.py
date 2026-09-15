"""`config` command implementations — ported from `little-versions`'
`commands/config.py`."""

from __future__ import annotations

from pathlib import Path

from version.common import CommandResult
from version.configstore import add_version_hook, list_version_hooks, load_config, remove_version_hook


def cmd_config_list(repo_root: Path) -> CommandResult:
    cfg = load_config(repo_root)
    return CommandResult(success=True, message="ok", data={"config": cfg})


def cmd_config_add_hook(repo_root: Path, hook_path: str) -> CommandResult:
    suffix = Path(hook_path).suffix.lower()
    if suffix not in (".sh", ".py"):
        return CommandResult(success=False, message=f"Hook must be a .sh or .py file. Got: {hook_path!r}")
    hooks_before = list_version_hooks(repo_root)
    if hook_path in hooks_before:
        return CommandResult(success=False, message=f"Hook already registered: {hook_path!r}")
    add_version_hook(repo_root, hook_path)
    return CommandResult(success=True, message=f"Registered version hook: {hook_path!r}", data={"hook": hook_path})


def cmd_config_remove_hook(repo_root: Path, hook_path: str) -> CommandResult:
    removed = remove_version_hook(repo_root, hook_path)
    if not removed:
        return CommandResult(success=False, message=f"Hook not found: {hook_path!r}")
    return CommandResult(success=True, message=f"Removed version hook: {hook_path!r}", data={"hook": hook_path})
