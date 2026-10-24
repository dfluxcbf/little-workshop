#!/usr/bin/env python3
"""Install little-workshop's Claude Code plugins for every project of this machine.

Adds `plugins/` as the local marketplace `little-workshop` and installs each plugin it lists at user scope, with
Claude Code's own `claude plugin` commands (REQ-000000425); a plugin already installed is updated instead.

    python3 tools/install_plugins.py [--dry-run] [--scope user|project]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

MARKETPLACE_DIR = Path(__file__).resolve().parent.parent / "plugins"


def claude(*args: str) -> tuple[int, str]:
    done = subprocess.run(["claude", "plugin", *args], capture_output=True, text=True)
    return done.returncode, done.stdout + done.stderr


def plugins_of(marketplace_dir: Path) -> tuple[str, list[str]]:
    manifest = json.loads((marketplace_dir / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    return manifest["name"], [p["name"] for p in manifest["plugins"]]


def plan(marketplace_dir: Path, scope: str, have_marketplace: bool, installed: str) -> list[list[str]]:
    name, plugins = plugins_of(marketplace_dir)
    commands = [["marketplace", "update", name]] if have_marketplace else [["marketplace", "add", str(marketplace_dir)]]
    for plugin in plugins:
        key = f"{plugin}@{name}"
        commands.append(["update", key, "--scope", scope] if key in installed else ["install", key, "--scope", scope])
    return commands


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--scope", choices=("user", "project"), default="user")
    args = parser.parse_args(argv)
    name, _ = plugins_of(MARKETPLACE_DIR)
    _, marketplaces = claude("marketplace", "list")
    _, installed = claude("list")
    status = 0
    for command in plan(MARKETPLACE_DIR, args.scope, name in marketplaces, installed):
        print("claude plugin " + " ".join(command))
        if not args.dry_run:
            code, output = claude(*command)
            print(output.strip())
            status = status or code
    return status


if __name__ == "__main__":
    sys.exit(main())
