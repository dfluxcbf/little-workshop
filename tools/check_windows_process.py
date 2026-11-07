#!/usr/bin/env python3
"""Run `common/windows_process.py` against this machine's real Windows (from WSL): start a harmless
native process, check it is alive by PID and start time, stop it, and check that a PID recorded with
another start time (a reused PID) reads as dead. Harmless: the process is `Start-Sleep` and is stopped.

Usage: python3 tools/check_windows_process.py
Exit 0 when every check passes; each check prints one line.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from common import windows_process  # noqa: E402
from common.shell import DEFAULT_RUNNER  # noqa: E402

CWD = "C:\\"


def main() -> int:
    runner = DEFAULT_RUNNER
    results: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, ok, detail))
        print(f"{'ok  ' if ok else 'FAIL'} {name}{': ' + detail if detail else ''}", flush=True)

    pid = windows_process.start_background(runner, "powershell.exe", ["-NoProfile", "-Command", "Start-Sleep 30"], CWD)
    check("start_background returns a PID", pid > 0, str(pid))
    started = windows_process.start_time(runner, pid)
    check("start_time reads the start time", bool(started), str(started))
    check("start_time is stable", windows_process.start_time(runner, pid) == started)
    check("is_alive by tasklist", windows_process.is_alive(runner, pid))
    check("is_alive with the recorded start time", windows_process.is_alive(runner, pid, started))
    check("a reused PID (other start time) reads as dead",
          not windows_process.is_alive(runner, pid, "2000-01-01T00:00:00.0000000Z"))
    windows_process.stop(runner, pid)
    deadline = time.monotonic() + 15
    while windows_process.is_alive(runner, pid, started) and time.monotonic() < deadline:
        time.sleep(0.5)
    check("stop ends it", not windows_process.is_alive(runner, pid, started))
    check("start_time of a gone process is None", windows_process.start_time(runner, pid) is None)

    server = windows_process.start_server(runner, "Start-Sleep 30", CWD)
    check("start_server returns a PID", server > 0, str(server))
    server_started = windows_process.start_time(runner, server)
    check("the server wrapper is alive", windows_process.is_alive(runner, server, server_started))
    windows_process.stop(runner, server)
    deadline = time.monotonic() + 15
    while windows_process.is_alive(runner, server, server_started) and time.monotonic() < deadline:
        time.sleep(0.5)
    check("stop tree-kills the server wrapper", not windows_process.is_alive(runner, server, server_started))

    failed = [name for name, ok, _ in results if not ok]
    print(f"{len(results) - len(failed)}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
