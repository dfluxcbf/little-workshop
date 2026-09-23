"""Sandboxed test execution (`little-requirements`' own never-built E6:
"on-demand testing") — calls Deployment & Servers' worktree interface
(M4's shared capability) rather than provisioning an isolated checkout
of its own, runs the project's real test command inside it, and ingests
the resulting JUnit-XML report through the same `junit.py` parser
`import_junit` uses directly. The worktree is always released again,
success or failure, so a failed run never leaks disk.
"""

from __future__ import annotations

import uuid

from common.paths import Paths
from common.shell import CommandRunner, DEFAULT_RUNNER
from common.worktree_client import provision_worktree, release_worktree
from requirements.junit import JUnitParseError, ParsedResult, parse_junit_xml


class TestRunError(Exception):
    pass


DEFAULT_TEST_COMMAND = ["python3", "-m", "pytest"]


def run_sandboxed(
    paths: Paths,
    project_id: str,
    commitish: str,
    *,
    test_command: list[str] | None = None,
    runner: CommandRunner = DEFAULT_RUNNER,
    timeout: float = 600.0,
) -> list[ParsedResult]:
    worktree_id, worktree_path = provision_worktree(paths, project_id, commitish)
    try:
        report_path = worktree_path / f".requirements-junit-{uuid.uuid4().hex}.xml"
        command = list(test_command or DEFAULT_TEST_COMMAND) + [f"--junit-xml={report_path}"]
        result = runner.run(command, cwd=worktree_path, timeout=timeout)

        if not report_path.exists():
            raise TestRunError(
                f"test command produced no JUnit report (exit {result.returncode}): "
                f"{result.stdout}\n{result.stderr}"
            )
        try:
            return parse_junit_xml(report_path.read_text(encoding="utf-8"))
        except JUnitParseError as exc:
            raise TestRunError(str(exc)) from exc
    finally:
        release_worktree(paths, project_id, worktree_id)
