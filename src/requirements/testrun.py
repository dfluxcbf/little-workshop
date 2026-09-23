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


def _collect(command: list[str], worktree_path, runner: CommandRunner, timeout: float) -> list[str]:
    result = runner.run([*command, "--collect-only", "-q"], cwd=worktree_path, timeout=timeout)
    node_ids = [line.strip() for line in result.stdout.splitlines() if "::" in line and not line.startswith(" ")]
    if not node_ids and result.returncode not in (0, 5):  # 5: pytest found nothing to collect
        tail = "\n".join((result.stdout + "\n" + result.stderr).strip().splitlines()[-15:])
        raise TestRunError(f"could not list this project's tests (exit {result.returncode}): {tail}")
    return node_ids


def collect_sandboxed(
    paths: Paths,
    project_id: str,
    commitish: str,
    *,
    test_command: list[str] | None = None,
    runner: CommandRunner = DEFAULT_RUNNER,
    timeout: float = 300.0,
) -> list[str]:
    """Every test node id the project's test command would run at `commitish`
    — `--collect-only`, so no test executes."""
    worktree_id, worktree_path = provision_worktree(paths, project_id, commitish)
    try:
        return _collect(list(test_command or DEFAULT_TEST_COMMAND), worktree_path, runner, timeout)
    finally:
        release_worktree(paths, project_id, worktree_id)


def run_suites_sandboxed(
    paths: Paths,
    project_id: str,
    commitish: str,
    scopes: dict[str, set[str]],
    *,
    test_command: list[str] | None = None,
    runner: CommandRunner = DEFAULT_RUNNER,
    timeout: float = 1800.0,
) -> dict[str, list[ParsedResult]]:
    """Run just the tests the given suites cover (see `requirements.discovery`),
    in one test-command invocation, and return each suite's own results.
    A test whose file two suites cover is reported under both."""
    from requirements.discovery import files_for_scopes, owning_file

    base = list(test_command or DEFAULT_TEST_COMMAND)
    worktree_id, worktree_path = provision_worktree(paths, project_id, commitish)
    try:
        files_by_suite = files_for_scopes(_collect(base, worktree_path, runner, timeout), scopes)
        files = sorted({f for fs in files_by_suite.values() for f in fs})
        if not files:
            raise TestRunError("none of the selected suites covers a test this project has")
        report_path = worktree_path / f".requirements-junit-{uuid.uuid4().hex}.xml"
        result = runner.run([*base, *files, f"--junit-xml={report_path}"], cwd=worktree_path, timeout=timeout)
        if not report_path.exists():
            raise TestRunError(
                f"test command produced no JUnit report (exit {result.returncode}): {result.stdout}\n{result.stderr}"
            )
        try:
            parsed = parse_junit_xml(report_path.read_text(encoding="utf-8"))
        except JUnitParseError as exc:
            raise TestRunError(str(exc)) from exc
        by_suite: dict[str, list[ParsedResult]] = {key: [] for key in scopes}
        for item in parsed:
            owner = owning_file(item.test_name, files)
            for key, suite_files in files_by_suite.items():
                if owner in suite_files:
                    by_suite[key].append(item)
        return by_suite
    finally:
        release_worktree(paths, project_id, worktree_id)
