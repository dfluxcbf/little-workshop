"""Sandboxed test execution (`little-requirements`' own never-built E6:
"on-demand testing") — calls Deployment & Servers' worktree interface
(M4's shared capability) rather than provisioning an isolated checkout
of its own, runs the project's real test command inside it, and ingests
the resulting JUnit-XML report through the same `junit.py` parser
`import_junit` uses directly. The worktree is always released again,
success or failure, so a failed run never leaks disk.

UI tests (pytest-playwright tests under `tests/ui/`, see
`common.playwright`) need the app they test to be running: when a run
includes any, and the project's pytest has pytest-playwright, the
branch's own app is started from the worktree on a free port — the
project's `[server.screenshot]` command, the same scratch-port launch
Agentic Development's screenshots use — and pytest gets `--base-url`
pointing at it. The app is stopped again whatever the outcome.
"""

from __future__ import annotations

import socket
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator

from common import playwright
from common.paths import Paths
from common.projectfiles import ProjectFilesClient
from common.shell import CommandRunner, DEFAULT_RUNNER
from common.worktree_client import provision_worktree, release_worktree
from requirements.junit import JUnitParseError, ParsedResult, parse_junit_xml


class TestRunError(Exception):
    pass


DEFAULT_TEST_COMMAND = ["python3", "-m", "pytest"]
DEPLOYMENT_MANIFEST = "manifest.deployment.toml"
UI_APP_LOG = ".requirements-ui-app.log"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def ui_test_files(worktree_path: Path, files: list[str] | None = None) -> list[str]:
    """The UI test files a run covers — among `files` when the run is
    limited to them, else every one in the worktree."""
    if files is None:
        files = [str(f.relative_to(worktree_path)) for f in worktree_path.glob("test*/**/test*.py")]
    return sorted(f for f in files if playwright.is_ui_test_file(f))


@contextmanager
def ui_app(
    paths: Paths, project_id: str, worktree_path: Path, base: list[str], ui_files: list[str], runner: CommandRunner,
    *, sleep: Callable[[float], None] | None = None, probe: Callable[[int], bool] | None = None,
    free_port: Callable[[], int] | None = None,
) -> Iterator[list[str]]:
    """Yields the extra pytest arguments a run needs for its UI tests
    (`--base-url`), with the app running for as long as the block lasts.
    Yields nothing extra when there are no UI tests, pytest-playwright
    isn't installed, or the project has no `[server.screenshot]` command
    to start its app with — the tests then run as they are, against
    whatever base URL their own configuration gives them."""
    from deployment.manifest import ManifestError, parse_manifest
    from deployment.netutil import port_is_listening, wait_for_port

    if not ui_files or not playwright.has_pytest_playwright(runner.run([*base, "-VV"], cwd=worktree_path).stdout):
        yield []
        return
    try:
        manifest = parse_manifest(ProjectFilesClient(paths, caller_pillar="requirements").read(project_id, DEPLOYMENT_MANIFEST))
    except (FileNotFoundError, ManifestError):
        yield []
        return
    shot = manifest.screenshot
    if shot is None or not shot.enabled or not shot.command:
        yield []
        return

    port = (free_port or _free_port)()
    command = " ".join(part.replace("{port}", str(port)) for part in shot.command)
    log = worktree_path / UI_APP_LOG
    # The app's output goes to a file: left on the captured pipe, the
    # backgrounded process would hold it open and the launch would never return.
    runner.run(["bash", "-c", f"{command} > {log} 2>&1 &"], cwd=worktree_path)
    try:
        if not wait_for_port(port, shot.ready_timeout_seconds, sleep=sleep or time.sleep, probe=probe or port_is_listening):
            output = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
            tail = "\n".join(output.strip().splitlines()[-15:])
            raise TestRunError(
                f"UI tests need the app running, but it did not start listening on port {port} "
                f"within {shot.ready_timeout_seconds}s (`{command}`){': ' + tail if tail else ''}"
            )
        yield [f"--base-url=http://127.0.0.1:{port}"]
    finally:
        runner.run(["pkill", "-f", command])


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
        base = list(test_command or DEFAULT_TEST_COMMAND)
        with ui_app(paths, project_id, worktree_path, base, ui_test_files(worktree_path), runner) as ui_args:
            result = runner.run([*base, *ui_args, f"--junit-xml={report_path}"], cwd=worktree_path, timeout=timeout)

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
        with ui_app(paths, project_id, worktree_path, base, ui_test_files(worktree_path, files), runner) as ui_args:
            result = runner.run([*base, *ui_args, *files, f"--junit-xml={report_path}"], cwd=worktree_path, timeout=timeout)
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
