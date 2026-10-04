"""How a tool call ended, read from its result (docs/pillars/usage.md, "Call outcomes"): the branch a
workflow took after it.

Claude Code marks a shell command failed (`is_error`, its result starting `Exit code N`) only when the
command itself exits non-zero. A test run piped into `tail` or `grep` exits 0, so its failures show only
in the summary line it printed; and a little-workshop call answers with a Job, whose `status` says how it
went. So the outcome is read, in order, from: a test summary in the output (pytest, Bazel, Vitest), the
exit code, the Job's status, the tool's own error flag.

Outcomes: `ok`, `failed`, `held` (a Job waiting in `pending_confirmation`), `running` (a Job not done yet),
`cancelled`, or None when nothing was kept to say."""

from __future__ import annotations

import json
import re

WORKSHOP_TOOL = "mcp__little-workshop__"
JOB_STATUS = {"succeeded": "ok", "failed": "failed", "pending_confirmation": "held", "running": "running",
              "queued": "running", "cancelled": "cancelled", "stopped": "cancelled"}

_EXIT = re.compile(r"\AExit code (\d+)")
# pytest: "1 failed, 997 passed, 6 warnings in 3.2s" (the last such line is the summary)
_PYTEST_LINE = re.compile(r"\b\d+ (?:passed|failed|errors?)\b(?:,| in |\s*$)")
_PYTEST_COUNT = re.compile(r"\b(\d+) (passed|failed|errors?)\b")
# Bazel: "Executed 2 out of 2 tests: 1 test passes and 1 fails locally." and the per-target lines.
_BAZEL_EXECUTED = re.compile(r"Executed \d+ out of \d+ tests?")
_BAZEL_TARGET = re.compile(r"^\S+\s+(PASSED|FAILED|TIMEOUT|FLAKY|NO STATUS)\b", re.MULTILINE)
# Vitest: " Tests  2 failed | 10 passed (12)"
_VITEST = re.compile(r"^\s*Tests\s+(.*\(\d+\))\s*$", re.MULTILINE)
_VITEST_COUNT = re.compile(r"(\d+) (passed|failed)")


def _empty(outcome: str | None = None, exit_code: int | None = None) -> dict:
    return {"outcome": outcome, "exit_code": exit_code, "tests_passed": None, "tests_failed": None}


def test_counts(text: str) -> tuple[int, int] | None:
    """(passed, failed) from the last test summary in `text`, or None when it holds none."""
    vitest = _VITEST.findall(text)
    if vitest:
        counts = {kind: int(n) for n, kind in _VITEST_COUNT.findall(vitest[-1])}
        return counts.get("passed", 0), counts.get("failed", 0)
    if _BAZEL_EXECUTED.search(text):
        targets = _BAZEL_TARGET.findall(text)
        passed = sum(1 for t in targets if t in ("PASSED", "FLAKY"))
        return passed, len(targets) - passed
    lines = [line for line in text.splitlines() if _PYTEST_LINE.search(line)]
    if lines:
        counts: dict[str, int] = {}
        for n, kind in _PYTEST_COUNT.findall(lines[-1]):
            key = "failed" if kind.startswith("error") or kind == "failed" else "passed"
            counts[key] = counts.get(key, 0) + int(n)
        return counts.get("passed", 0), counts.get("failed", 0)
    return None


def _workshop(text: str, is_error: bool | None) -> str:
    try:
        answer = json.loads(text)
    except (TypeError, ValueError):
        return "failed" if is_error else "ok"
    if isinstance(answer, dict):
        if "type" in answer and answer.get("status") in JOB_STATUS:  # a Job
            return JOB_STATUS[answer["status"]]
        if answer.get("success") is False or answer.get("error"):
            return "failed"
    return "failed" if is_error else "ok"


def classify(tool: str, text: str | None, is_error: bool | None) -> dict:
    """{"outcome", "exit_code", "tests_passed", "tests_failed"} for one call's result."""
    if text is None and is_error is None:
        return _empty()
    text = text or ""
    if tool == "Bash":
        exit_match = _EXIT.match(text)
        exit_code = int(exit_match.group(1)) if exit_match else (None if is_error else 0)
        counts = test_counts(text)
        if counts is not None:
            passed, failed = counts
            return {"outcome": "failed" if failed or (exit_code not in (None, 0)) else "ok", "exit_code": exit_code,
                    "tests_passed": passed, "tests_failed": failed}
        return _empty("failed" if is_error or exit_code not in (None, 0) else "ok", exit_code)
    if tool.startswith(WORKSHOP_TOOL):
        return _empty(_workshop(text, is_error))
    return _empty("failed" if is_error else "ok")
