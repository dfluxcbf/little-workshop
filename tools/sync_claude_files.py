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
from registry.claude_sync import (  # noqa: E402,F401 - the functions this command line is made of
    PLATFORM_PROJECT, briefing_note, detect_plugins, detect_project_id, detect_template, generator_owned, is_generated_entry,
    is_guard_entry, merged_settings, options_for, pending, wanted_files, write as write_files,
)


def sync(checkout: Path, project_id: str | None, template: str | None, write: bool) -> list[str]:
    report = pending(checkout, project_id, template)
    changed = report["changed"]
    if write:
        write_files(checkout, report["files"])
    print(f"{checkout} ({report['project_id']}, {report['template']}): " + ("in sync" if not changed else
          f"{len(changed)} file(s) {'written' if write else 'to write'}"))
    for line in changed:
        print(f"  {line}")
    if report["note"]:
        print(f"  note: {report['note']}")
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
