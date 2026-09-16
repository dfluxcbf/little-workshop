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
