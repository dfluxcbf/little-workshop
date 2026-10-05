"""Finding and ending the processes of one Claude session, for stopping a running task.

A task's Claude is started with `--session-id <uuid>`, so its command line names the session; the
tools it runs (shells, test runs, MCP servers) are its descendants. Read from `/proc`, which is
all a Linux host needs, and injectable for tests."""

from __future__ import annotations

import os
import signal
from pathlib import Path
from typing import Callable

PROC = Path("/proc")


def _processes(proc_root: Path) -> dict[int, tuple[int, list[str]]]:
    """{pid: (parent pid, argv)} for every process that can be read."""
    found: dict[int, tuple[int, list[str]]] = {}
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            argv = [a.decode(errors="replace") for a in (entry / "cmdline").read_bytes().split(b"\0") if a]
            # `pid (comm) state ppid ...`: comm may hold spaces and parentheses, so split after the last ')'.
            stat = (entry / "stat").read_text(encoding="utf-8", errors="replace")
            ppid = int(stat[stat.rindex(")") + 1:].split()[1])
        except (OSError, ValueError, IndexError):
            continue  # gone meanwhile, or not ours to read
        found[int(entry.name)] = (ppid, argv)
    return found


def session_processes(session_id: str, *, proc_root: Path = PROC) -> list[int]:
    """The processes whose command line names `session_id`, then all their descendants, parents first."""
    if not session_id:
        return []
    table = _processes(proc_root)
    roots = sorted(pid for pid, (_, argv) in table.items() if session_id in argv)
    children: dict[int, list[int]] = {}
    for pid, (ppid, _) in table.items():
        children.setdefault(ppid, []).append(pid)
    ordered: list[int] = []
    queue = list(roots)
    while queue:
        pid = queue.pop(0)
        if pid in ordered:
            continue
        ordered.append(pid)
        queue.extend(sorted(children.get(pid, [])))
    return ordered


def terminate_session(
    session_id: str, *, proc_root: Path = PROC, kill: Callable[[int, int], None] = os.kill, sig: int = signal.SIGTERM,
) -> list[int]:
    """Send `sig` to every process of the session, children before their parents (so a parent does
    not restart what it ran), and return the pids it was sent to."""
    pids = list(reversed(session_processes(session_id, proc_root=proc_root)))
    sent = []
    for pid in pids:
        try:
            kill(pid, sig)
        except (ProcessLookupError, PermissionError):
            continue
        sent.append(pid)
    return sent
