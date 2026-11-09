#!/usr/bin/env python3
"""Write the workspace's Claude Code files (src/registry/workspace.py): the little-projects root session's
settings, guard and `project-session` skill, and little-workshop's own `settings.local.json`, from the
registered projects (`lws project list`). Shows what would change; `--write` writes it.

    python3 tools/sync_workspace_claude.py [--root ..] [--write]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from registry import workspace  # noqa: E402


def registered() -> dict[str, str]:
    out = subprocess.run(["lws", "project", "list"], capture_output=True, text=True,
                         env={**os.environ, "LWS_NO_PROGRESS": "1"})
    rows = json.loads(out.stdout)
    rows = rows if isinstance(rows, list) else rows.get("projects", [])
    return {row["name"]: row["path"] for row in rows if row.get("path")}


def _json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def planned(root: Path, projects: dict[str, str], workshop: Path = REPO) -> dict[Path, str]:
    root_settings = root / ".claude" / "settings.json"
    local = workshop / ".claude" / "settings.local.json"
    return {
        root_settings: json.dumps(workspace.root_settings(_json(root_settings), projects, str(root)), indent=2) + "\n",
        root / workspace.ROOT_GUARD_PATH: workspace.root_guard_py(projects),
        root / workspace.ROOT_SKILL_PATH: workspace.project_session_skill_md(),
        local: json.dumps(workspace.workshop_local_settings(_json(local), projects), indent=2) + "\n",
    }


def main(argv: list[str] | None = None, projects: dict[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=str(REPO.parent), help="The little-projects folder (default: ..)")
    parser.add_argument("--workshop", default=str(REPO), help="little-workshop's checkout (default: this one)")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    changed = 0
    for path, text in planned(root, projects if projects is not None else registered(), Path(args.workshop)).items():
        current = path.read_text() if path.is_file() else None
        if current == text:
            continue
        changed += 1
        if args.write:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        print(f"{'updated' if args.write else 'would update'} {path}")
    print(f"{changed} file(s) {'written' if args.write else 'differ; run with --write'}" if changed else "up to date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
