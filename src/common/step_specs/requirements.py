"""Declared steps of the Requirements & Quality pillar's Job types (keys as `src/requirements/app.py` reports them).

The create/update/delete/link/record Job types are one action each and are not listed."""

from __future__ import annotations

from common.job_steps import StepSpec

_RUN = (
    StepSpec("validate_request", "Validate the request and find the suite(s)"),
    StepSpec("run_tests", "Run the tests in a sandboxed checkout"),
    StepSpec("record_results", "Record the results"),
)

STEPS: dict[str, tuple[StepSpec, ...]] = {
    "requirements.run_tests": _RUN,
    "requirements.discover_tests": (
        StepSpec("find_suites", "Find the project's test suites"),
        StepSpec("collect_tests", "Collect the project's tests in a sandboxed checkout"),
        StepSpec("match_suites", "Match each test to the suites that cover it"),
    ),
    "requirements.import_junit": (
        StepSpec("validate_request", "Validate the request and find the suite"),
        StepSpec("parse_report", "Parse the JUnit report"),
        StepSpec("record_results", "Record the results"),
    ),
}
