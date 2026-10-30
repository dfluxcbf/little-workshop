"""Which tests a suite covers, worked out by collecting (never running) the
project's tests — requirements.discovery, and the collect/run halves of
requirements.testrun that use it."""

from __future__ import annotations

from pathlib import Path

import pytest

from common.shell import CommandResult
from requirements import discovery, testrun
from requirements.models import TestVerdict

COLLECTED = """tests/unit/test_agent_app.py::test_health
tests/unit/test_agent_app.py::TestRuns::test_list
tests/test_cook_along.py::test_finish[cooked]
tests/test_cook_along.py::test_start
tests/test_api_plan.py::test_choose

5 tests collected in 0.12s
"""

JUNIT = """<testsuite>
  <testcase classname="tests.unit.test_agent_app" name="test_health" />
  <testcase classname="tests.unit.test_agent_app.TestRuns" name="test_list"><failure message="boom">boom</failure></testcase>
  <testcase classname="tests.test_cook_along" name="test_finish[cooked]" />
  <testcase classname="tests.test_cook_along" name="test_start"><skipped message="later" /></testcase>
</testsuite>"""


# -- scopes -------------------------------------------------------------------------------


def test_a_module_named_suite_covers_its_own_module():
    assert discovery.suite_scope("test_agent_app", []) == {"test_agent_app"}


def test_a_feature_suite_covers_the_modules_of_the_tests_recorded_under_it():
    recorded = ["tests.test_cook_along::test_start", "tests.test_cook_along::test_finish[cooked]", "migrated-from-little-requirements"]
    assert discovery.suite_scope("Cook Along", recorded) == {"tests.test_cook_along"}


def test_a_suite_with_nothing_recorded_and_a_feature_name_covers_nothing():
    assert discovery.suite_scope("Audio Pipeline", []) == set()


def test_tests_are_matched_to_suites_by_file():
    node_ids = [line for line in COLLECTED.splitlines() if "::" in line]
    scopes = {
        "module": {"test_agent_app"},
        "feature": {"tests.test_cook_along"},
        "class": {"tests.test_api_plan.TestSomething"},
        "none": set(),
    }
    by_suite = discovery.tests_for_scopes(node_ids, scopes)
    assert by_suite["module"] == ["tests/unit/test_agent_app.py::test_health", "tests/unit/test_agent_app.py::TestRuns::test_list"]
    assert by_suite["feature"] == ["tests/test_cook_along.py::test_finish[cooked]", "tests/test_cook_along.py::test_start"]
    assert by_suite["class"] == ["tests/test_api_plan.py::test_choose"]
    assert by_suite["none"] == []


def test_a_result_is_traced_back_to_the_file_it_came_from():
    files = ["tests/unit/test_agent_app.py", "tests/test_cook_along.py"]
    assert discovery.owning_file("tests.unit.test_agent_app.TestRuns::test_list", files) == "tests/unit/test_agent_app.py"
    assert discovery.owning_file("tests.test_cook_along::test_start", files) == "tests/test_cook_along.py"
    assert discovery.owning_file("elsewhere::x", files) is None


# -- collect / run in a worktree ------------------------------------------------------------


class Runner:
    def __init__(self, collect_out=COLLECTED, collect_code=0, junit=JUNIT):
        self.collect_out, self.collect_code, self.junit = collect_out, collect_code, junit
        self.calls: list[list[str]] = []

    def run(self, argv, timeout=None, cwd=None):
        self.calls.append(argv)
        if "--collect-only" in argv:
            return CommandResult(self.collect_code, self.collect_out, "ImportError: no module named x" if self.collect_code else "")
        report = next(a for a in argv if a.startswith("--junit-xml="))
        Path(report.split("=", 1)[1]).write_text(self.junit, encoding="utf-8")
        return CommandResult(1, "", "")


@pytest.fixture
def worktree(monkeypatch, tmp_path):
    released = []
    monkeypatch.setattr(testrun, "provision_worktree", lambda p, project_id, commitish, **kw: ("wt1", tmp_path))
    monkeypatch.setattr(testrun, "release_worktree", lambda p, project_id, worktree_id, **kw: released.append(worktree_id))
    return released


def test_collecting_lists_node_ids_without_running_anything(worktree):
    runner = Runner()
    ids = testrun.collect_sandboxed(None, "p", "HEAD", runner=runner)
    assert len(ids) == 5 and ids[0] == "tests/unit/test_agent_app.py::test_health"
    assert runner.calls == [["python3", "-m", "pytest", "--collect-only", "-q"]]
    assert worktree == ["wt1"]


def test_a_collection_error_is_reported_and_the_worktree_released(worktree):
    with pytest.raises(testrun.TestRunError, match="could not list this project's tests"):
        testrun.collect_sandboxed(None, "p", "HEAD", runner=Runner(collect_out="", collect_code=2))
    assert worktree == ["wt1"]


def test_running_suites_runs_only_their_files_and_files_each_result_under_its_suite(worktree):
    runner = Runner()
    by_suite = testrun.run_suites_sandboxed(
        None, "p", "HEAD", {"mod": {"test_agent_app"}, "feat": {"tests.test_cook_along"}, "all": {"test_agent_app", "tests.test_cook_along"}},
        runner=runner,
    )
    run = runner.calls[1]
    assert run[:3] == ["python3", "-m", "pytest"] and run[3:5] == ["tests/test_cook_along.py", "tests/unit/test_agent_app.py"]
    assert "tests/test_api_plan.py" not in run  # no chosen suite covers it
    verdicts = {k: sorted((r.test_name, r.verdict) for r in v) for k, v in by_suite.items()}
    assert verdicts["mod"] == [("tests.unit.test_agent_app.TestRuns::test_list", TestVerdict.FAILED),
                               ("tests.unit.test_agent_app::test_health", TestVerdict.PASSED)]
    assert verdicts["feat"] == [("tests.test_cook_along::test_finish[cooked]", TestVerdict.PASSED),
                                ("tests.test_cook_along::test_start", TestVerdict.SKIPPED)]
    assert len(verdicts["all"]) == 4
    assert worktree == ["wt1"]


def test_running_suites_that_cover_no_test_is_refused_before_running(worktree):
    runner = Runner()
    with pytest.raises(testrun.TestRunError, match="none of the selected suites covers a test"):
        testrun.run_suites_sandboxed(None, "p", "HEAD", {"x": {"Audio Pipeline"}}, runner=runner)
    assert len(runner.calls) == 1 and worktree == ["wt1"]
