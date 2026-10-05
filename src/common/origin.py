"""Where a request came from -- what the policy bypass decides by (`docs/pillars/policy_compliance.md`'s
"Policy bypass"). Every Job and every policy evaluation carries one origin; only `user_cli` may ever be
bypassed, and anything not positively identified as the user's own terminal is `unknown`, which never is.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

USER_CLI = "user_cli"            # `lws` typed by the user in a terminal
CLAUDE = "claude"                # Claude Code (its shell, an agent run) or the MCP server
SCHEDULED_JOB = "scheduled_job"  # a scheduled task: right away, once at a date and time, every week
UI_JOB = "ui_job"                # a Job triggered through the Dashboard
AUTOMATION = "automation"        # an event trigger or another unattended caller
UNKNOWN = "unknown"

ORIGINS = (USER_CLI, CLAUDE, SCHEDULED_JOB, UI_JOB, AUTOMATION, UNKNOWN)

# Environment markers Claude Code sets in the shells it starts (sudo's env_reset drops them, hence the
# process-ancestry check as well).
CLAUDE_ENV_MARKERS = ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SESSION_ID")
MAX_ANCESTORS = 64


def normalize(origin: str | None) -> str:
    return origin if origin in ORIGINS else UNKNOWN


def _ancestor_pids(proc: Path = Path("/proc"), start: int | None = None) -> list[int]:
    pids: list[int] = []
    pid = start if start is not None else os.getppid()
    while pid > 1 and len(pids) < MAX_ANCESTORS:
        pids.append(pid)
        try:
            stat = (proc / str(pid) / "stat").read_text()
            pid = int(stat.rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            break
    return pids


def _is_claude_process(proc: Path, pid: int) -> bool:
    try:
        name = (proc / str(pid) / "comm").read_text().strip().lower()
    except OSError:
        return False
    if name == "claude" or name.startswith("claude-"):
        return True
    try:
        environ = (proc / str(pid) / "environ").read_bytes().split(b"\0")
    except OSError:
        return False  # not ours to read (root's); the other checks stand
    return any(entry.split(b"=", 1)[0].decode(errors="ignore") in CLAUDE_ENV_MARKERS for entry in environ)


def invoked_by_claude(environ: dict[str, str] | None = None, proc: Path = Path("/proc")) -> bool:
    """True when this process runs inside Claude Code: its own environment carries a Claude marker, or one of
    its ancestors is a `claude` process or runs with one (which survives `sudo` clearing the environment)."""
    env = os.environ if environ is None else environ
    if any(env.get(marker) for marker in CLAUDE_ENV_MARKERS):
        return True
    return any(_is_claude_process(proc, pid) for pid in _ancestor_pids(proc))


def cli_origin(environ: dict[str, str] | None = None, proc: Path = Path("/proc")) -> str:
    """The origin of an `lws` invocation: Claude when inside Claude Code; `user_cli` only when stdin and
    stderr are a terminal (a person typing, not a unit or a pipe); otherwise `unknown`, never bypassable."""
    env = os.environ if environ is None else environ
    if invoked_by_claude(env, proc):
        return CLAUDE
    try:
        if sys.stdin.isatty() and sys.stderr.isatty():
            return USER_CLI
    except (ValueError, OSError):
        pass
    return UNKNOWN
