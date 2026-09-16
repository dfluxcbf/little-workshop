"""Build/test Job execution — `little-runner`'s disclaimed CI non-goal,
made real (docs/milestones.md M11). Calls Deployment & Servers' worktree
interface (the same shared `common/worktree_client.py` Requirements &
Quality's sandboxed test execution uses) rather than provisioning an
isolated checkout of its own; runs the project's own build/test command
inside it — arbitrary argv, since one pillar can't assume every
project's build looks like `pytest` (Bazel, npm, make, ...). The
worktree is always released again, success or failure.
"""

from __future__ import annotations

from dataclasses import dataclass

from common.paths import Paths
from common.shell import CommandRunner, DEFAULT_RUNNER
from common.worktree_client import provision_worktree, release_worktree


@dataclass
class RunOutcome:
    exit_code: int
    log: str


def run_build(
    paths: Paths,
    project_id: str,
    commitish: str,
    command: list[str],
    *,
    runner: CommandRunner = DEFAULT_RUNNER,
    timeout: float = 1800.0,
) -> RunOutcome:
    worktree_id, worktree_path = provision_worktree(paths, project_id, commitish)
    try:
        result = runner.run(command, cwd=worktree_path, timeout=timeout)
        log = result.stdout
        if result.stderr:
            log += ("\n" if log else "") + result.stderr
        return RunOutcome(exit_code=result.returncode, log=log)
    finally:
        release_worktree(paths, project_id, worktree_id)
