#!/usr/bin/env python3
"""Run a turn of work in a project's own Claude sub-session and print its report (REQ-000000546). The `project-session` skill of the little-projects root uses it.

    project_session.py <project-id> [--new] [--allow RULE ...] [--prompt TEXT]   (the prompt on stdin otherwise)
    project_session.py --status
    project_session.py --forget <project-id>

The sub-session is `claude -p` started in the project's checkout (the Windows `claude.exe` for a Windows-hosted
project), so its settings, briefing, skills and guard apply. One per project: its id is kept and resumed on the
next call, until `--new` or `--forget`. Edits are accepted (`--permission-mode acceptEdits`); anything the
project's permissions would prompt for is denied and listed in the report, to be allowed with `--allow`.
One turn per project at a time. Stdlib only.
"""

from __future__ import annotations

import argparse
import fcntl
import glob
import json
import os
import subprocess
import sys
import time
from pathlib import Path

STATE_DIR = Path(os.environ.get("LWS_PROJECT_SESSIONS_DIR") or Path.home() / ".config" / "little-workshop" / "project-sessions")
WINDOWS_CLAUDE = "/mnt/c/Users/*/.local/bin/claude.exe"
NOTE = ("You are a sub-session the little-projects root session started in this project on the user's behalf. "
        "Nobody answers questions here: carry the task through under this project's rules. When a permission or a "
        "decision that is really the user's blocks you, stop and state exactly what is needed (the command, or the "
        "decision and its options) in your final message. End with the short report this project's briefing asks for.")
MISSING_SESSION = ("No conversation found", "no conversation found", "not found")


def lws_projects() -> dict[str, dict]:
    out = subprocess.run(["lws", "project", "list"], capture_output=True, text=True,
                         env={**os.environ, "LWS_NO_PROGRESS": "1"})
    rows = json.loads(out.stdout)
    rows = rows if isinstance(rows, list) else rows.get("projects", [])
    return {row["name"]: row for row in rows}


def state_file() -> Path:
    return STATE_DIR / "sessions.json"


def load_state() -> dict:
    try:
        return json.loads(state_file().read_text())
    except (OSError, ValueError):
        return {}


def save_state(state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    state_file().write_text(json.dumps(state, indent=2) + "\n")


def claude_binary(project: dict) -> str:
    if project.get("host") == "windows":
        found = sorted(glob.glob(WINDOWS_CLAUDE))
        if found:
            return found[0]
    return "claude"


def command(binary: str, session_id: str | None, allow: list[str]) -> list[str]:
    argv = [binary, "-p", "--output-format", "json", "--permission-mode", "acceptEdits",
            "--append-system-prompt", NOTE]
    if session_id:
        argv += ["--resume", session_id]
    if allow:
        argv += ["--allowedTools", *allow]
    return argv


def run_turn(argv: list[str], cwd: str, prompt: str) -> tuple[dict | None, str]:
    done = subprocess.run(argv, cwd=cwd, input=prompt, capture_output=True, text=True)
    try:
        return json.loads(done.stdout.strip().splitlines()[-1]), done.stderr
    except (ValueError, IndexError):
        return None, (done.stderr or done.stdout).strip()


def summarize_input(tool_input: dict) -> str:
    for key in ("command", "file_path", "notebook_path", "url", "pattern"):
        if key in tool_input:
            return str(tool_input[key])[:200]
    return json.dumps(tool_input)[:200]


def report(project: str, session_id: str, how: str, result: dict) -> str:
    cost = result.get("total_cost_usd")
    head = (f"[{project} sub-session {session_id} ({how}), {result.get('num_turns', '?')} turns"
            + (f", ${cost:.2f}" if isinstance(cost, (int, float)) else "") + "]")
    lines = [head, str(result.get("result") or "").strip() or "(no final message)"]
    denials = result.get("permission_denials") or []
    if denials:
        lines.append("\nPermission denials (if they are fine: re-run with --allow '<rule>' and the prompt `continue`; a "
                     "command the project lists under `ask` stays denied there, so run it from the root instead):")
        lines += [f"- {d.get('tool_name')}: {summarize_input(d.get('tool_input') or {})}" for d in denials]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("project", nargs="?")
    parser.add_argument("--new", action="store_true", help="Start a new sub-session instead of resuming")
    parser.add_argument("--allow", action="append", default=[], help="A permission rule to allow for this turn")
    parser.add_argument("--prompt", help="The task (default: stdin)")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--forget", metavar="PROJECT")
    args = parser.parse_args(argv)

    state = load_state()
    if args.status:
        for name, entry in sorted(state.items()):
            print(f"{name}: {entry['session_id']} ({entry.get('turns', 0)} turns, last {entry.get('updated_at', '?')})")
        return 0
    if args.forget:
        state.pop(args.forget, None)
        save_state(state)
        print(f"forgot the {args.forget} sub-session")
        return 0
    if not args.project:
        parser.error("name the project")
    projects = lws_projects()
    if args.project not in projects:
        print(f"error: {args.project} is not a registered project ({', '.join(sorted(projects))})", file=sys.stderr)
        return 2
    project = projects[args.project]
    prompt = args.prompt if args.prompt is not None else sys.stdin.read()
    if not prompt.strip():
        parser.error("give the task on stdin or with --prompt")

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(STATE_DIR / f"{args.project}.lock", "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print(f"error: a {args.project} sub-session turn is already running; wait for its report", file=sys.stderr)
            return 3
        entry = None if args.new else state.get(args.project)
        binary = claude_binary(project)
        result, err = run_turn(command(binary, entry and entry["session_id"], args.allow), project["path"], prompt)
        how = "resumed" if entry else "new"
        if entry and (result is None or result.get("is_error")) and any(m in (err + str(result)) for m in MISSING_SESSION):
            result, err = run_turn(command(binary, None, args.allow), project["path"], prompt)
            how = "new: the old one could not be resumed"
        if result is None or not result.get("session_id"):
            print(f"error: the {args.project} sub-session did not answer: {err[-800:]}", file=sys.stderr)
            return 1
        turns = (entry or {}).get("turns", 0) + 1 if how == "resumed" else 1
        state = load_state()
        state[args.project] = {"session_id": result["session_id"], "binary": binary, "turns": turns,
                               "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
        save_state(state)
    print(report(args.project, result["session_id"], how, result))
    return 1 if result.get("is_error") else 0


if __name__ == "__main__":
    sys.exit(main())
