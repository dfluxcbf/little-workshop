"""Settling a finish's rebase that stopped on files little-workshop generates: the Claude files
(`.claude/settings.json`, `.claude/little-workshop.md`, `.claude/hooks/*`, the generated rule and
skills) and the git hooks (`.githooks/*`). Neither side of such a conflict is the answer: the
generator is, so each conflicted generated file is written again from it and staged, and the rebase
goes on. The Claude files come from Registry & Portfolio over its Pillar Interface (the generator is
its code); the git hooks are this pillar's own (`commands/git_hooks.py`). A conflicted file the
generator does not write is left for the user."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

import httpx

from common.paths import Paths
from common.pillar_socket import resolve_pillar_socket
from common.uds_client import client_for_socket
from version.commands.git_hooks import GENERATED_HOOKS, RETIRED_PRE_COMMIT, RETIRED_PRE_COMMIT_MARK
from version.configstore import load_config
from version.gitops import run

GENERATED_PREFIXES = (".claude/", ".githooks/")
SETTINGS_PATH = ".claude/settings.json"

# (project id, checkout, the settings.json sides) -> {"files": {path: text}, "retired": [paths]}, or None.
Fetch = Callable[[str, Path, list[dict]], dict | None]


def fetch_from_registry(project_id: str, checkout: Path, settings_sides: list[dict]) -> dict | None:
    """Registry & Portfolio's answer, or None when it does not give one."""
    paths = Paths.from_env()
    socket_path = resolve_pillar_socket(paths, "registry", timeout=5.0)
    if socket_path is None:
        return None
    try:
        with client_for_socket(socket_path, timeout=30.0) as client:
            response = client.post(f"/v1/projects/{project_id}/claude_files/regenerated",
                                   json={"checkout": str(checkout), "settings_sides": settings_sides})
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPError, OSError, ValueError):
        return None


def is_generated_path(path: str) -> bool:
    return path.startswith(GENERATED_PREFIXES)


def _side(repo_root: Path, stage: int, path: str) -> dict | None:
    shown = run(["git", "show", f":{stage}:{path}"], repo_root, check=False)
    try:
        side = json.loads(shown.stdout) if shown.returncode == 0 else None
    except ValueError:
        return None
    return side if isinstance(side, dict) else None


def regenerate_conflicted(repo_root: Path, conflicted: list[str], fetch: Fetch | None = None) -> list[str]:
    """Write each conflicted generated file in `conflicted` from the generator and stage it (a retired one
    is removed). Returns the paths settled; the others stay conflicted."""
    generated = [path for path in conflicted if is_generated_path(path)]
    if not generated:
        return []
    settled: list[str] = []
    for path in (path for path in generated if path.startswith(".githooks/")):
        if path in GENERATED_HOOKS:
            target = repo_root / path
            target.write_text(GENERATED_HOOKS[path], encoding="utf-8")
            target.chmod(0o755)
            run(["git", "add", "--", path], repo_root)
        elif path == RETIRED_PRE_COMMIT and any(
                RETIRED_PRE_COMMIT_MARK in run(["git", "show", f":{n}:{path}"], repo_root, check=False).stdout for n in (2, 3)):
            run(["git", "rm", "-q", "--force", "--ignore-unmatch", "--", path], repo_root)  # little-workshop's, retired
        else:
            continue  # the project's own hook: a real conflict
        settled.append(path)
    project_id = load_config(repo_root).get("project_id")
    claude = [path for path in generated if path.startswith(".claude/")]
    if claude and project_id:
        sides = [side for side in (_side(repo_root, n, SETTINGS_PATH) for n in (2, 3)) if side] if SETTINGS_PATH in claude else []
        answer = (fetch or fetch_from_registry)(project_id, repo_root, sides)
        if answer:
            files, retired = answer.get("files") or {}, set(answer.get("retired") or [])
            for path in claude:
                if path in files:
                    target = repo_root / path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(files[path], encoding="utf-8")
                    run(["git", "add", "--", path], repo_root)
                elif path in retired:
                    run(["git", "rm", "-q", "--force", "--ignore-unmatch", "--", path], repo_root)
                else:
                    continue  # under .claude/ but the project's own: a real conflict
                settled.append(path)
    return settled
