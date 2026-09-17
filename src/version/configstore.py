"""Load, write, and manage `.lvx/config.json`/`.lvx/state.json` — ported
from `little-versions`' `config/loader.py`. This pillar has no database
of its own (docs/milestones.md M3): git, and these two files inside the
repo being operated on, remain the actual store.
"""

from __future__ import annotations

import json
from pathlib import Path

from version.schema import CONFIG_DIR, CONFIG_FILE, STATE_FILE, validate_config


def config_path(repo_root: Path) -> Path:
    return repo_root / CONFIG_DIR / CONFIG_FILE


def state_path(repo_root: Path) -> Path:
    return repo_root / CONFIG_DIR / STATE_FILE


def load_config(repo_root: Path) -> dict:
    path = config_path(repo_root)
    if not path.exists():
        raise FileNotFoundError(f"Repository not initialized for version control. (missing: {path})")
    data = json.loads(path.read_text(encoding="utf-8"))
    return validate_config(data)


def write_config(repo_root: Path, cfg: dict) -> None:
    path = config_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")


def load_state(repo_root: Path) -> dict:
    path = state_path(repo_root)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_state(repo_root: Path, state: dict) -> None:
    path = state_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def set_issue_link(repo_root: Path, branch: str, issue_number: int) -> None:
    state = load_state(repo_root)
    state.setdefault("issue_links", {})[branch] = issue_number
    write_state(repo_root, state)


def get_issue_link(repo_root: Path, branch: str) -> int | None:
    state = load_state(repo_root)
    return state.get("issue_links", {}).get(branch)


def remove_issue_link(repo_root: Path, branch: str) -> None:
    state = load_state(repo_root)
    state.get("issue_links", {}).pop(branch, None)
    write_state(repo_root, state)


def list_issue_links(repo_root: Path) -> dict[str, int]:
    state = load_state(repo_root)
    return dict(state.get("issue_links", {}))


def set_promoted_rc(repo_root: Path, release_branch: str, rc_number: int) -> None:
    state = load_state(repo_root)
    state.setdefault("promoted_rcs", {})[release_branch] = rc_number
    write_state(repo_root, state)


def get_promoted_rc(repo_root: Path, release_branch: str) -> int | None:
    state = load_state(repo_root)
    return state.get("promoted_rcs", {}).get(release_branch)


# -- version hooks --------------------------------------------------------


def add_version_hook(repo_root: Path, hook_path: str) -> None:
    cfg = load_config(repo_root)
    hooks: list = list(cfg.get("version_hooks", []))
    if hook_path not in hooks:
        hooks.append(hook_path)
    cfg["version_hooks"] = hooks
    write_config(repo_root, cfg)


def remove_version_hook(repo_root: Path, hook_path: str) -> bool:
    cfg = load_config(repo_root)
    hooks: list = list(cfg.get("version_hooks", []))
    if hook_path in hooks:
        hooks.remove(hook_path)
        cfg["version_hooks"] = hooks
        write_config(repo_root, cfg)
        return True
    return False


def list_version_hooks(repo_root: Path) -> list[str]:
    cfg = load_config(repo_root)
    return list(cfg.get("version_hooks", []))


# -- finish-in-progress state, one slot per topic-branch kind ------------


def _save_finish_state(repo_root: Path, key: str, **fields) -> None:
    state = load_state(repo_root)
    state[key] = fields
    write_state(repo_root, state)


def _load_finish_state(repo_root: Path, key: str) -> dict | None:
    return load_state(repo_root).get(key)


def _clear_finish_state(repo_root: Path, key: str) -> None:
    state = load_state(repo_root)
    state.pop(key, None)
    write_state(repo_root, state)


def save_finish_state(repo_root: Path, work_branch: str, integration_branch: str, merge_message: str) -> None:
    _save_finish_state(
        repo_root, "finish_in_progress",
        work_branch=work_branch, integration_branch=integration_branch, merge_message=merge_message,
    )


def load_finish_state(repo_root: Path) -> dict | None:
    return _load_finish_state(repo_root, "finish_in_progress")


def clear_finish_state(repo_root: Path) -> None:
    _clear_finish_state(repo_root, "finish_in_progress")


def save_release_finish_state(
    repo_root: Path, release_branch: str, stable_version: str, production_branch: str,
    integration_branch: str, merge_message: str, phase: str,
) -> None:
    _save_finish_state(
        repo_root, "release_finish_in_progress",
        release_branch=release_branch, stable_version=stable_version, production_branch=production_branch,
        integration_branch=integration_branch, merge_message=merge_message, phase=phase,
    )


def load_release_finish_state(repo_root: Path) -> dict | None:
    return _load_finish_state(repo_root, "release_finish_in_progress")


def clear_release_finish_state(repo_root: Path) -> None:
    _clear_finish_state(repo_root, "release_finish_in_progress")


def save_hotfix_finish_state(
    repo_root: Path, hotfix_branch: str, production_branch: str, integration_branch: str,
    merge_message: str, new_version: str, phase: str,
) -> None:
    _save_finish_state(
        repo_root, "hotfix_finish_in_progress",
        hotfix_branch=hotfix_branch, production_branch=production_branch, integration_branch=integration_branch,
        merge_message=merge_message, new_version=new_version, phase=phase,
    )


def load_hotfix_finish_state(repo_root: Path) -> dict | None:
    return _load_finish_state(repo_root, "hotfix_finish_in_progress")


def clear_hotfix_finish_state(repo_root: Path) -> None:
    _clear_finish_state(repo_root, "hotfix_finish_in_progress")
