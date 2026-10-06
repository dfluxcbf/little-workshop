#!/usr/bin/env python3
"""Claude Code session title hook, installed by little-workshop's Usage & Efficiency pillar (`usage.configure_claude`
copies this file; do not edit the copy). Registered on SessionStart and UserPromptSubmit, it names a session after
the checkout it works in: `<project> · <VERSION> · <branch>`, such as `little-meals · 0.10.2 · dev` (REQ-000000413).

It renames only when the branch or the version differs from what it last applied to that session, so a name the
user gives in between stays until the next change. A checkout without a `VERSION` file at its top is not a
little-workshop project and is left alone. Standard library only: the copy runs on its own, outside the package."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

SEPARATOR = " · "
STATE_KEEP_DAYS = 30  # a session's record of what it was last named after is dropped this long after its last use
# Claude Code ignores a title on these SessionStart sources: the conversation keeps its name.
KEPT_NAME_SOURCES = ("clear", "compact")


def state_dir() -> Path:
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "little-workshop" / "session-titles"


def _git(cwd: str, *args: str) -> str | None:
    try:
        done = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout.strip() if done.returncode == 0 and done.stdout.strip() else None


def project_name(common_dir: str) -> str:
    """The project of a checkout from its git common directory: `<checkout>/.git` for a plain repository (its
    linked worktrees share it), `<super>/.git/modules/<name>` for a submodule, `<name>.git` for a bare one."""
    path = Path(common_dir)
    return path.parent.name if path.name == ".git" else path.name.removesuffix(".git")


def checkout(cwd: str) -> dict | None:
    """{project, version, branch} of the checkout `cwd` is in; None outside one or without a VERSION file."""
    top = _git(cwd, "rev-parse", "--show-toplevel")
    common = _git(cwd, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if not top or not common:
        return None
    try:
        version = (Path(top) / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    branch = _git(cwd, "rev-parse", "--abbrev-ref", "HEAD") or "?"
    if branch == "HEAD":  # a detached checkout is named after its commit
        branch = _git(cwd, "rev-parse", "--short", "HEAD") or "detached"
    return {"project": project_name(common), "version": version or "?", "branch": branch}


def title(state: dict) -> str:
    return SEPARATOR.join((state["project"], state["version"], state["branch"]))


def decide(event: dict, current: dict | None, last: dict | None) -> str | None:
    """The title to set for this hook call, or None to leave the session's name as it is: only when the checkout
    is a project and its branch or version is not what was last applied to this session."""
    if current is None:
        return None
    if event.get("hook_event_name") == "SessionStart" and event.get("source") in KEPT_NAME_SOURCES:
        return None
    if last is not None and (last.get("project"), last.get("branch"), last.get("version")) == \
            (current["project"], current["branch"], current["version"]):
        return None
    return title(current)


def _record(session_id: str) -> Path | None:
    safe = "".join(c for c in session_id if c.isalnum() or c in "-_")
    return state_dir() / f"{safe}.json" if safe else None


def _forget_old(directory: Path) -> None:
    cutoff = time.time() - STATE_KEEP_DAYS * 86400
    for path in directory.glob("*.json"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            pass


def run(event: dict) -> dict | None:
    """The hook's answer for `event` (what Claude Code sent on stdin), recording what it named the session after."""
    current = checkout(event.get("cwd") or os.getcwd())
    record = _record(str(event.get("session_id") or ""))
    last = None
    if record is not None:
        try:
            last = json.loads(record.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            last = None
    new_title = decide(event, current, last)
    if new_title is None:
        return None
    if record is not None:
        try:
            record.parent.mkdir(parents=True, exist_ok=True)
            record.write_text(json.dumps(current), encoding="utf-8")
            _forget_old(record.parent)
        except OSError:
            pass
    return {"hookSpecificOutput": {"hookEventName": event.get("hook_event_name"), "sessionTitle": new_title}}


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return 0
    if not isinstance(event, dict):
        return 0
    answer = run(event)
    if answer is not None:
        print(json.dumps(answer))
    return 0  # never block a session or a prompt over a name


if __name__ == "__main__":
    sys.exit(main())
