"""The one seam every subprocess call in this project goes through.

Mirrors the pattern `little-runner`'s `shell.py`/`systemd.py` established
(see docs/milestones.md's implementation-kickoff note): production code
calls through a `CommandRunner`, and tests inject a fake one instead of
actually shelling out — except the small number of integration tests that
deliberately want the real thing, which use `DEFAULT_RUNNER` against a
live `systemd --user` session.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class CommandResult:
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


class CommandRunner:
    """Runs real subprocesses. Swap for a fake in tests."""

    def run(self, argv: list[str], timeout: float | None = None, cwd: Path | str | None = None) -> CommandResult:
        try:
            proc = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=cwd,
            )
        except subprocess.TimeoutExpired as exc:
            return CommandResult(
                returncode=-1,
                stdout=exc.stdout or "",
                stderr=(exc.stderr or "") + f"\ntimed out after {timeout}s",
            )
        return CommandResult(returncode=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)


DEFAULT_RUNNER = CommandRunner()
