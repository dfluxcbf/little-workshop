from pathlib import Path

import pytest

from common.shell import CommandResult
from requirements import testrun
from requirements.models import TestVerdict

JUNIT_XML = """<testsuite>
    <testcase classname="tests.test_a" name="test_pass" />
</testsuite>"""


class FakeRunner:
    def __init__(self, *, write_report: bool = True, returncode: int = 0):
        self.write_report = write_report
        self.returncode = returncode
        self.calls: list[list[str]] = []

    def run(self, argv, timeout=None, cwd=None):
        self.calls.append(argv)
        if self.write_report:
            report_arg = next(a for a in argv if a.startswith("--junit-xml="))
            report_path = Path(report_arg.split("=", 1)[1])
            report_path.write_text(JUNIT_XML, encoding="utf-8")
        return CommandResult(self.returncode, "", "")


@pytest.fixture
def paths(tmp_path):
    from common.paths import Paths

    p = Paths(config_home=tmp_path / "cfg", state_home=tmp_path / "state")
    p.ensure_layout()
    return p


def test_run_sandboxed_provisions_runs_and_releases_a_worktree(monkeypatch, paths, tmp_path):
    worktree_dir = tmp_path / "worktree"
    worktree_dir.mkdir()
    released = []

    monkeypatch.setattr(testrun, "provision_worktree", lambda p, project_id, commitish, **kw: ("wt1", worktree_dir))
    monkeypatch.setattr(testrun, "release_worktree", lambda p, project_id, worktree_id, **kw: released.append((project_id, worktree_id)))

    runner = FakeRunner()
    results = testrun.run_sandboxed(paths, "little-widget", "HEAD", runner=runner)

    assert [r.test_name for r in results] == ["tests.test_a::test_pass"]
    assert results[0].verdict == TestVerdict.PASSED
    assert released == [("little-widget", "wt1")]
    assert runner.calls[0][:3] == ["python3", "-m", "pytest"]


def test_run_sandboxed_releases_the_worktree_even_if_the_test_command_fails(monkeypatch, paths, tmp_path):
    worktree_dir = tmp_path / "worktree"
    worktree_dir.mkdir()
    released = []

    monkeypatch.setattr(testrun, "provision_worktree", lambda p, project_id, commitish, **kw: ("wt1", worktree_dir))
    monkeypatch.setattr(testrun, "release_worktree", lambda p, project_id, worktree_id, **kw: released.append(worktree_id))

    runner = FakeRunner(write_report=True, returncode=1)
    testrun.run_sandboxed(paths, "little-widget", "HEAD", runner=runner)

    assert released == ["wt1"]


def test_run_sandboxed_raises_when_no_report_is_produced(monkeypatch, paths, tmp_path):
    worktree_dir = tmp_path / "worktree"
    worktree_dir.mkdir()

    monkeypatch.setattr(testrun, "provision_worktree", lambda p, project_id, commitish, **kw: ("wt1", worktree_dir))
    monkeypatch.setattr(testrun, "release_worktree", lambda p, project_id, worktree_id, **kw: None)

    runner = FakeRunner(write_report=False, returncode=2)
    with pytest.raises(testrun.TestRunError, match="produced no JUnit report"):
        testrun.run_sandboxed(paths, "little-widget", "HEAD", runner=runner)


def test_run_sandboxed_raises_on_a_malformed_junit_report(monkeypatch, paths, tmp_path):
    worktree_dir = tmp_path / "worktree"
    worktree_dir.mkdir()

    monkeypatch.setattr(testrun, "provision_worktree", lambda p, project_id, commitish, **kw: ("wt1", worktree_dir))
    monkeypatch.setattr(testrun, "release_worktree", lambda p, project_id, worktree_id, **kw: None)

    class BadRunner:
        def run(self, argv, timeout=None, cwd=None):
            report_arg = next(a for a in argv if a.startswith("--junit-xml="))
            Path(report_arg.split("=", 1)[1]).write_text("not xml", encoding="utf-8")
            return CommandResult(0, "", "")

    with pytest.raises(testrun.TestRunError):
        testrun.run_sandboxed(paths, "little-widget", "HEAD", runner=BadRunner())


def test_run_sandboxed_uses_a_custom_test_command(monkeypatch, paths, tmp_path):
    worktree_dir = tmp_path / "worktree"
    worktree_dir.mkdir()

    monkeypatch.setattr(testrun, "provision_worktree", lambda p, project_id, commitish, **kw: ("wt1", worktree_dir))
    monkeypatch.setattr(testrun, "release_worktree", lambda p, project_id, worktree_id, **kw: None)

    runner = FakeRunner()
    testrun.run_sandboxed(paths, "little-widget", "HEAD", test_command=["python3", "-m", "pytest", "tests/unit"], runner=runner)

    assert runner.calls[0][:4] == ["python3", "-m", "pytest", "tests/unit"]
    assert runner.calls[0][4].startswith("--junit-xml=")


# -- UI tests (pytest-playwright under tests/ui/) -------------------------

MANIFEST = """[server]
name = "widget"
command = "python3 -m widget"
port = 8100

[server.screenshot]
command = ["python3", "-m", "widget", "--port", "{port}"]
ready_timeout_seconds = 1
"""


class UiRunner(FakeRunner):
    def __init__(self, *, plugin: bool = True, **kw):
        super().__init__(**kw)
        self.plugin = plugin

    def run(self, argv, timeout=None, cwd=None):
        if argv[-1] == "-VV":
            self.calls.append(argv)
            listing = "  pytest-playwright-0.7.1 at /x\n" if self.plugin else "  pytest-cov-7.1.0 at /x\n"
            return CommandResult(0, "registered third-party plugins:\n" + listing, "")
        if argv[0] in ("bash", "pkill"):
            self.calls.append(argv)
            return CommandResult(0, "", "")
        if "--collect-only" in argv:
            self.calls.append(argv)
            return CommandResult(0, "tests/ui/test_menu.py::test_opens\ntests/unit/test_a.py::test_pass\n", "")
        return super().run(argv, timeout=timeout, cwd=cwd)


def _ui_worktree(monkeypatch, paths, tmp_path, *, manifest=MANIFEST, listening=True):
    worktree_dir = tmp_path / "worktree"
    (worktree_dir / "tests" / "ui").mkdir(parents=True)
    (worktree_dir / "tests" / "ui" / "test_menu.py").write_text("")
    (worktree_dir / "tests" / "unit").mkdir()
    (worktree_dir / "tests" / "unit" / "test_a.py").write_text("")
    manifest_path = paths.project_dir("little-widget") / "manifest.deployment.toml"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.unlink(missing_ok=True)
    if manifest is not None:
        manifest_path.write_text(manifest)
    monkeypatch.setattr(testrun, "provision_worktree", lambda p, project_id, commitish, **kw: ("wt1", worktree_dir))
    monkeypatch.setattr(testrun, "release_worktree", lambda p, project_id, worktree_id, **kw: None)
    monkeypatch.setattr(testrun, "_free_port", lambda: 45678)
    import deployment.netutil

    monkeypatch.setattr(deployment.netutil, "port_is_listening", lambda port, *a, **kw: listening)
    return worktree_dir


def _pytest_call(runner):
    return next(c for c in runner.calls if any(a.startswith("--junit-xml=") for a in c))


def test_ui_test_files_finds_only_files_under_a_ui_directory(tmp_path):
    (tmp_path / "tests" / "ui" / "flows").mkdir(parents=True)
    (tmp_path / "tests" / "ui" / "flows" / "test_checkout.py").write_text("")
    (tmp_path / "tests" / "ui" / "conftest.py").write_text("")
    (tmp_path / "tests" / "test_core.py").write_text("")
    assert testrun.ui_test_files(tmp_path) == ["tests/ui/flows/test_checkout.py"]
    assert testrun.ui_test_files(tmp_path, ["tests/test_core.py", "tests/ui/test_a.py"]) == ["tests/ui/test_a.py"]


def test_run_sandboxed_starts_the_app_for_ui_tests_and_passes_its_base_url(monkeypatch, paths, tmp_path):
    worktree_dir = _ui_worktree(monkeypatch, paths, tmp_path)
    runner = UiRunner()

    testrun.run_sandboxed(paths, "little-widget", "HEAD", runner=runner)

    launch = next(c for c in runner.calls if c[0] == "bash")
    assert launch == ["bash", "-c", f"python3 -m widget --port 45678 > {worktree_dir / testrun.UI_APP_LOG} 2>&1 &"]
    assert "--base-url=http://127.0.0.1:45678" in _pytest_call(runner)
    assert runner.calls[-1] == ["pkill", "-f", "python3 -m widget --port 45678"]  # stopped after the run
    assert runner.calls.index(launch) < runner.calls.index(_pytest_call(runner))


def test_run_suites_sandboxed_starts_the_app_only_when_a_chosen_suite_has_ui_tests(monkeypatch, paths, tmp_path):
    _ui_worktree(monkeypatch, paths, tmp_path)

    runner = UiRunner()
    testrun.run_suites_sandboxed(paths, "little-widget", "HEAD", {"s": {"tests.unit.test_a"}}, runner=runner)
    assert not any(c[0] == "bash" for c in runner.calls)
    assert not any(a.startswith("--base-url") for a in _pytest_call(runner))

    runner = UiRunner()
    testrun.run_suites_sandboxed(paths, "little-widget", "HEAD", {"s": {"tests.ui.test_menu"}}, runner=runner)
    call = _pytest_call(runner)
    assert "--base-url=http://127.0.0.1:45678" in call
    assert "tests/ui/test_menu.py" in call


def test_ui_tests_run_as_they_are_without_pytest_playwright_or_a_launch_command(monkeypatch, paths, tmp_path):
    cases = ((False, MANIFEST), (True, None), (True, MANIFEST.split("[server.screenshot]")[0]))
    for i, (plugin, manifest) in enumerate(cases):
        _ui_worktree(monkeypatch, paths, tmp_path / str(i), manifest=manifest)
        runner = UiRunner(plugin=plugin)
        testrun.run_sandboxed(paths, "little-widget", "HEAD", runner=runner)
        assert not any(c[0] in ("bash", "pkill") for c in runner.calls)
        assert not any(a.startswith("--base-url") for a in _pytest_call(runner))


def test_ui_app_that_never_starts_fails_the_run_and_is_still_stopped(monkeypatch, paths, tmp_path):
    _ui_worktree(monkeypatch, paths, tmp_path, listening=False)
    monkeypatch.setattr("time.sleep", lambda s: None)
    runner = UiRunner()
    with pytest.raises(testrun.TestRunError, match="did not start listening on port 45678"):
        testrun.run_sandboxed(paths, "little-widget", "HEAD", runner=runner)
    assert runner.calls[-1][0] == "pkill"
    assert not any(any(a.startswith("--junit-xml=") for a in c) for c in runner.calls)
