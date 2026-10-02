---
name: test-runner
description: Runs little-workshop's unit suite (tests/unit) and reports only failures, errors and skips with their causes. Use after code changes instead of running pytest inline, so the long output stays out of the main context.
tools: Bash, Read, Grep, Glob
model: haiku
---
Run `python3 -m pytest tests/unit -q -rfEs --no-header` from the repo root (or a narrower path/`-k` if the caller
gives one). Use a timeout of 10 minutes; if it still times out, say so rather than retrying.

Report, in under 150 words:
1. Pass/fail/skip/error counts.
2. For each failure or error: test id, one-line cause, and the file:line in `src/` or `tests/` that is responsible.
3. Every skip, flagged explicitly: a skipped unit test is a defect here because it marks the whole DoD suite skipped.

Do not edit files. Do not run `tests/integration` unless asked.
