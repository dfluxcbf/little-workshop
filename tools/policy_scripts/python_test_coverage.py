import os
import re
import subprocess

import pytest

PROJECT = os.environ.get("POLICY_PROJECT_DIR", "/project")
THRESHOLD = 90

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

    env = dict(os.environ, PYTHONPATH=src_dir)
    result = subprocess.run(
        ["python3", "-m", "pytest", "tests", "--cov=src", "--cov-report=term-missing", "-q"],
        cwd=PROJECT, capture_output=True, text=True, env=env, timeout=1500,
    )
    match = TOTAL_RE.search(result.stdout)
    assert match, (
        f"could not measure coverage (pytest exited {result.returncode}, "
        f"possibly a dependency this check's own sandbox doesn't carry):\n{result.stdout[-4000:]}\n{result.stderr[-2000:]}"
    )
    percent = int(match.group(1))
    assert percent >= THRESHOLD, f"statement coverage is {percent}%, below the required {THRESHOLD}%"
