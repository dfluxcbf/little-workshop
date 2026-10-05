"""What systemd's journal says about the units Core watches (REQ-000000400): each run of a unit that
ended abnormally ("<unit>: Failed with result '<result>'."), with systemd's own lines for that run and
the last lines its process logged.

systemd's restart counter (`NRestarts`) missed most of what went wrong: a unit started anew (every
redeploy) counts from zero, a unit removed after it failed (`--collect`) reads zero, and a stop that
overran its timeout and ended in SIGKILL is no restart at all (2026-10-04: Orchestration and Policy
were killed that way during a redeploy and no incident was filed). The journal keeps every failure
whatever became of the unit. It is read from where the last read stopped, a position kept in a small
file, so a failure that happened while Core itself was down, Core's own crash among them, is found
when Core is back. The first read ever starts at that moment: nothing older is reported.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable

from common.shell import CommandRunner, DEFAULT_RUNNER

# systemd's catalog ids: "<unit>: Failed with result '<result>'." (with UNIT_RESULT), "<unit>: Main process
# exited, code=<code>, status=<status>" (with EXIT_CODE and EXIT_STATUS) and "Stopping <unit>..." (a stop
# was asked for).
FAILURE_MESSAGE_ID = "d9b373ed55a64feb8242e02dbe79a49c"
PROCESS_EXIT_MESSAGE_ID = "98e322203f7a4ed290d09fe03c09fe15"
STOPPING_MESSAGE_ID = "de5b426a63be47a7b6ac3eaac82e2f6f"

OVERLAP_SECONDS = 5  # each read starts a little before the last one; entries already seen are skipped
SEEN_KEPT = 200
LINE_LIMIT = 200
PROCESS_LINES_READ = 400
# uvicorn's access-log line says nothing about why a process ended.
_ACCESS_LOG = re.compile(r'"(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS) \S+ HTTP/[\d.]+" \d{3}')


@dataclass
class UnitFailure:
    unit: str
    invocation: str  # the unit run that ended (systemd's invocation id)
    result: str  # systemd's UNIT_RESULT: exit-code, signal, core-dump, timeout, oom-kill, start-limit-hit, watchdog, ...
    at: datetime  # local time
    exit_code: str = ""  # how the main process ended: exited, killed or dumped
    exit_status: str = ""  # its exit status, or the signal's number
    stop_requested: bool = False  # a stop was asked for in that run (a redeploy, a `systemctl stop`)
    systemd_lines: list[str] = field(default_factory=list)
    process_lines: list[str] = field(default_factory=list)


def _message(entry: dict) -> str:
    message = entry.get("MESSAGE", "")
    if isinstance(message, list):  # journalctl's JSON for a message that is not valid UTF-8
        message = bytes(message).decode("utf-8", "replace")
    return str(message)


def _line(entry: dict) -> str:
    at = datetime.fromtimestamp(int(entry.get("__REALTIME_TIMESTAMP", "0")) / 1e6)
    return f"{at:%H:%M:%S} {_message(entry)[:LINE_LIMIT]}"


class Journal:
    def __init__(self, position_path: Path, *, runner: CommandRunner = DEFAULT_RUNNER, clock: Callable[[], float] = time.time):
        self.position_path = position_path
        self.runner = runner
        self.clock = clock

    def _position(self) -> dict | None:
        try:
            return json.loads(self.position_path.read_text())
        except (OSError, ValueError):
            return None

    def _keep(self, since: float, seen: list[str]) -> None:
        self.position_path.parent.mkdir(parents=True, exist_ok=True)
        self.position_path.write_text(json.dumps({"since": since, "seen": seen[-SEEN_KEPT:]}))

    def _entries(self, matches: list[str], *, lines: int | None = None, since: float | None = None) -> list[dict] | None:
        argv = ["journalctl", "--user", "--no-pager", "-o", "json"]
        if lines is not None:
            argv += ["-n", str(lines)]
        if since is not None:
            argv.append(f"--since=@{int(since) - OVERLAP_SECONDS}")
        result = self.runner.run([*argv, *matches], timeout=30)
        if not result.ok:
            return None
        entries = []
        for text in result.stdout.splitlines():
            try:
                entries.append(json.loads(text))
            except ValueError:
                continue
        return entries

    def failures(self, units: Iterable[str]) -> list[UnitFailure]:
        """Every failure of `units` logged since the last call; none on the very first."""
        units = sorted(set(units))
        position, now = self._position(), self.clock()
        if position is None:
            self._keep(now, [])
            return []
        entries = self._entries([f"MESSAGE_ID={FAILURE_MESSAGE_ID}", *(f"USER_UNIT={u}" for u in units)], since=position["since"]) if units else []
        if entries is None:
            return []  # the journal could not be read: try again from the same place next time
        seen, found = list(position.get("seen", [])), []
        for entry in entries:
            cursor = entry.get("__CURSOR")
            if not cursor or cursor in seen:
                continue
            seen.append(cursor)
            found.append(self._failure(entry))
        self._keep(now, seen)
        return found

    def _failure(self, entry: dict) -> UnitFailure:
        failure = UnitFailure(
            unit=entry.get("USER_UNIT", ""), invocation=entry.get("USER_INVOCATION_ID", ""),
            result=entry.get("UNIT_RESULT", "unknown"), at=datetime.fromtimestamp(int(entry.get("__REALTIME_TIMESTAMP", "0")) / 1e6),
        )
        if failure.invocation:
            story = self.systemd_entries(failure.invocation)
            for item in story:
                if item.get("MESSAGE_ID") == PROCESS_EXIT_MESSAGE_ID:
                    failure.exit_code, failure.exit_status = item.get("EXIT_CODE", ""), item.get("EXIT_STATUS", "")
                elif item.get("MESSAGE_ID") == STOPPING_MESSAGE_ID:
                    failure.stop_requested = True
            failure.systemd_lines = [_line(item) for item in story]
            failure.process_lines = self.process_lines(failure.invocation)
        return failure

    def systemd_entries(self, invocation: str) -> list[dict]:
        """systemd's own lines about one run of a unit: the stop asked for, a timeout, a kill, the exit."""
        return self._entries([f"USER_INVOCATION_ID={invocation}"]) or []

    def process_lines(self, invocation: str, *, keep: int = 25) -> list[str]:
        """The last lines one run's process logged, its access log left out."""
        entries = self._entries([f"_SYSTEMD_INVOCATION_ID={invocation}"], lines=PROCESS_LINES_READ) or []
        return [_line(e) for e in entries if not _ACCESS_LOG.search(_message(e))][-keep:]
