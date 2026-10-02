#!/usr/bin/env python3
"""Bring a project's generated Claude Code files in line with src/registry/claude_templates.py.

Every registered project carries the `.claude/` files little-workshop generates (claude_files():
the settings' permission table and guard hook, the guard script, the shared briefing section, the
instructions rule and the two skills). project.bootstrap writes them once; this rewrites them in an
existing checkout after the generator changes. Without --write it only prints what differs.

`.claude/settings.json` is merged, not replaced: the generated permission table and guard hook come
first, and every entry the project added beside them (its own allows, Read denies, extra hooks, other
settings) is kept. The project's own AGENTS.md is never touched; the script says when it does not
import the shared section yet.

    python3 tools/sync_claude_files.py --checkout ../little-meals            # what would change
    python3 tools/sync_claude_files.py --checkout ../little-meals --write    # make it so
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from registry import claude_templates as ct  # noqa: E402

PLATFORM_PROJECT = "little-workshop"


def detect_template(checkout: Path) -> str:
    if (checkout / "ProjectSettings" / "ProjectVersion.txt").is_file():
        return "unity"
    if (checkout / "pyproject.toml").is_file():
        return "python"
    return "bazel"


def detect_project_id(checkout: Path) -> str:
    """The scope guard's argument in the checkout's settings, else the directory name."""
    try:
        settings = json.loads((checkout / ct.SETTINGS_PATH).read_text(encoding="utf-8"))
        for entry in (settings.get("hooks") or {}).get("PreToolUse") or []:
            for hook in entry.get("hooks") or []:
                match = re.search(r"lws_scope_guard\.py\s+(\S+)", hook.get("command", ""))
                if match:
                    return match.group(1)
    except (OSError, ValueError):
        pass
    return checkout.resolve().name


def options_for(project_id: str) -> dict:
    return dict(ct.OWN_SETTINGS) if project_id == PLATFORM_PROJECT else {}


def generator_owned(project_id: str) -> set[str]:
    """Every permission entry the generator writes for this project, under any template."""
    owned: set[str] = set()
    for template in ct.TEST_BASH:
        for platform in (False, True):
            for entries in ct.settings(project_id, template, platform=platform)["permissions"].values():
                owned.update(entries)
    owned.update(e for entries in ct.settings(project_id, **ct.OWN_SETTINGS)["permissions"].values() for e in entries)
    owned.update(ct.RETIRED_RULES)
    return owned


def is_generated_entry(entry: str, owned: set[str]) -> bool:
    return entry in owned or entry.startswith(ct.MCP) or entry.startswith("Bash(lws ")


def is_guard_entry(entry: dict) -> bool:
    return any(ct.GUARD_PATH in (hook.get("command") or "") for hook in entry.get("hooks") or [])


def merged_settings(project_id: str, template: str, current: dict | None) -> dict:
    """The generated settings, plus what the project added beside them."""
    generated = ct.settings(project_id, template, **options_for(project_id))
    if not current:
        return generated
    owned = generator_owned(project_id)
    permissions = dict(generated["permissions"])
    for key, entries in (current.get("permissions") or {}).items():
        if key not in permissions:
            permissions[key] = entries
            continue
        extra = [e for e in entries if isinstance(e, str) and not is_generated_entry(e, owned)]
        permissions[key] = permissions[key] + [e for e in extra if e not in permissions[key]]
    hooks = dict(generated["hooks"])
    for event, entries in (current.get("hooks") or {}).items():
        if event not in hooks:
            hooks[event] = entries
            continue
        kept = [e for e in entries if not is_guard_entry(e) and e not in hooks[event]]
        hooks[event] = hooks[event] + kept
    merged = {"permissions": permissions, "hooks": hooks}
    merged.update({k: v for k, v in current.items() if k not in merged})
    return merged


def wanted_files(checkout: Path, project_id: str, template: str) -> dict[str, str]:
    files = ct.claude_files(project_id, template, **options_for(project_id))
    try:
        current = json.loads((checkout / ct.SETTINGS_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        current = None
    files[ct.SETTINGS_PATH] = json.dumps(merged_settings(project_id, template, current), indent=2) + "\n"
    return files


def briefing_note(checkout: Path) -> str | None:
    """Why the project's briefing does not load the shared section yet, or None."""
    for rel, token in ((ct.AGENTS_PATH, ct.SHARED_BRIEFING_IMPORT), ("AGENTS.md", "@" + ct.SHARED_BRIEFING_PATH)):
        path = checkout / rel
        if path.is_file():
            if token in path.read_text(encoding="utf-8"):
                return None
            return f"{rel} does not import the shared section: add a line `{token}`"
    return f"no AGENTS.md briefing: start one from claude_templates.agents_md() ending with `{ct.SHARED_BRIEFING_IMPORT}`"


def sync(checkout: Path, project_id: str | None, template: str | None, write: bool) -> list[str]:
    project_id = project_id or detect_project_id(checkout)
    template = template or detect_template(checkout)
    changed = []
    for rel, text in wanted_files(checkout, project_id, template).items():
        path = checkout / rel
        current = path.read_text(encoding="utf-8") if path.is_file() else None
        if current == text:
            continue
        changed.append(f"{'created' if current is None else 'updated'} {rel}")
        if write:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
    print(f"{checkout} ({project_id}, {template}): " + ("in sync" if not changed else
          f"{len(changed)} file(s) {'written' if write else 'to write'}"))
    for line in changed:
        print(f"  {line}")
    note = briefing_note(checkout)
    if note:
        print(f"  note: {note}")
    return changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--checkout", action="append", required=True, type=Path,
                        help="A project checkout (repeatable)")
    parser.add_argument("--project-id", default=None, help="Default: read from the checkout (one checkout only)")
    parser.add_argument("--template", choices=tuple(ct.TEST_BASH), default=None, help="Default: detected")
    parser.add_argument("--write", action="store_true", help="Write the files instead of listing them")
    args = parser.parse_args(argv)
    if args.project_id and len(args.checkout) > 1:
        parser.error("--project-id names one project: pass one --checkout with it")
    pending = 0
    for checkout in args.checkout:
        if not checkout.is_dir():
            print(f"{checkout}: not a directory", file=sys.stderr)
            return 2
        pending += len(sync(checkout, args.project_id, args.template, args.write))
    if pending and not args.write:
        print("run with --write to write them")
    return 0


if __name__ == "__main__":
    sys.exit(main())
