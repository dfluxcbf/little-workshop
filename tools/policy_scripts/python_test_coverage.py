"""Policy `python-test-coverage` (requirements pillar; gates branch_finish): the project's own
Python test suite covers at least 90 percent of its statements.

Runs the suite (tests/unit when the project has one, else tests/) with coverage in the sandbox,
which has no network: a test-time dependency the sandbox image does not carry fails the check
with pytest's own output attached."""

import os
import re
import subprocess
import tempfile

import pytest

PROJECT = os.environ.get("POLICY_PROJECT_DIR", "/project")
THRESHOLD = 90
FIX = (
    "Fix: add tests for the uncovered lines `--cov-report=term-missing` lists (run "
    "`python3 -m pytest tests/unit --cov=src --cov-report=term-missing` locally)."
)

TOTAL_RE = re.compile(r"^TOTAL\s+\d+\s+\d+\s+(\d+)%\s*$", re.MULTILINE)


def test_python_statement_coverage_is_at_least_90_percent():
    """Runs the project's own real pytest suite with coverage measurement — only
    the *newest* run matters, same as every other check in this project; a
    project that isn't a Python project in this ecosystem's own convention
    (no pyproject.toml, no src/, no tests/) is skipped, never failed — a
    TypeScript/Node project (little-home) can have its own src/ and tests/
    directories too, so pyproject.toml is the real signal."""
    if not os.path.isfile(os.path.join(PROJECT, "pyproject.toml")):
        pytest.skip("no pyproject.toml - not a Python project")
    tests_dir = os.path.join(PROJECT, "tests")
    src_dir = os.path.join(PROJECT, "src")
    if not os.path.isdir(tests_dir):
        pytest.skip("no tests/ directory - nothing to measure coverage of")
    if not os.path.isdir(src_dir):
        pytest.skip("no src/ directory - not this ecosystem's Python project layout")

    # This sandbox runs with --network none, so anything under tests/integration
    # (real HTTP/socket calls between pillars) can only ever time out here --
    # never pass, never usefully fail, just burn the check's time budget.
    # A project that splits out a network-free tests/unit dir gets checked
    # against that instead; a project that doesn't falls back to tests/ as
    # before.
    unit_dir = os.path.join(tests_dir, "unit")
    target = "tests/unit" if os.path.isdir(unit_dir) else "tests"

    # coverage.py's own data file defaults to <cwd>/.coverage -- cwd is
    # PROJECT, which the sandbox mounts read-only, so every single check
    # crashed here before a single test ran (2026-09-23's finding: this
    # policy had never actually measured any project's real coverage).
    # COVERAGE_FILE is coverage.py's own documented override, honored by
    # pytest-cov underneath it -- redirecting it to a real temp dir needs
    # no pytest-cov-version-specific flag.
    with tempfile.TemporaryDirectory(prefix="policy-coverage-") as scratch:
        env = dict(os.environ, PYTHONPATH=src_dir, COVERAGE_FILE=os.path.join(scratch, ".coverage"))
        result = subprocess.run(
            ["python3", "-m", "pytest", target, "--cov=src", "--cov-report=term-missing", "-q"],
            cwd=PROJECT, capture_output=True, text=True, env=env, timeout=1500,
        )
    match = TOTAL_RE.search(result.stdout)
    assert match, (
        f"could not measure coverage (pytest exited {result.returncode}, "
        f"possibly a dependency this check's own sandbox doesn't carry):\n{result.stdout[-4000:]}\n{result.stderr[-2000:]}"
    )
    percent = int(match.group(1))
    # The modules not fully covered, with the lines term-missing lists for each.
    missing = "\n".join(
        line for line in result.stdout.splitlines()
        if (m := re.match(r"^src/\S+\s+\d+\s+\d+\s+(\d+)%", line)) and int(m.group(1)) < 100
    )
    assert percent >= THRESHOLD, (
        f"Statement coverage is {percent}%, below the required {THRESHOLD}%. {FIX}\n{missing[-3000:]}"
    )
