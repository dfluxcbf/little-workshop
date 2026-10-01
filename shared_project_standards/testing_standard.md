# Testing standard

Sources: `docs/requirements_policy.md`, agent prompts (`bug.md`, `milestone.md`,
`ui_playwright*.md`), policy `python-test-coverage`, `docs/pillars/requirements_quality.md`.

## Layout

- `tests/unit/`: network-free unit tests. The policy sandbox runs with `--network none`.
- `tests/integration/`: tests that make real HTTP or socket calls between components.
- `tests/ui/`: Playwright tests for anything a user sees or touches.

## Rules

1. **Every behaviour change ships with automated tests** that are tagged with the requirement ids
   they cover (see [requirements_standard.md](requirements_standard.md)).
2. **A bug fix starts with a failing test** that reproduces the report, then the smallest correct
   root-cause fix, then the whole suite green.
3. **A unit test must never skip inside a sandboxed worktree** (for example by requiring sibling
   projects beside the checkout). A skip degrades the whole suite's verdict.
4. **Coverage:** a Python project's own suite covers at least 90 percent of statements
   (`python-test-coverage`, measured on `tests/unit` when it exists, otherwise `tests/`; skipped for
   a project without `pyproject.toml`, `src/` or `tests/`).
5. **Playwright is the standard for UI and UX.** Tests use pytest-playwright's `page` fixture and
   relative URLs (`page.goto("/")`) so they run against whatever `--base-url` they receive;
   little-workshop starts the branch's app and passes it. Where a Playwright MCP server is attached,
   also drive the UI in a browser before reporting done; otherwise say in the report that the UI was
   not checked in a browser.
6. Non-pytest frameworks (Unity NUnit) emit JUnit-XML for `workshop_requirements_import_junit`.

## Recording a run for the Definition-of-Done report

The report takes the **worst** verdict of the suite's latest run: one failure marks every linked
requirement failed, one skip marks them all skipped. For little-workshop's "Unit tests" suite always
run `["python3", "-m", "pytest", "tests/unit"]`. A default run including `tests/integration` produced
sandbox-only failures that dropped 231 done requirements to 0 on 2026-09-24.

The run blocks for several minutes; the MCP call can time out ("orchestration daemon unreachable")
while the job continues. Read the job's final status instead of retrying.

## Done means green

A milestone or task is not `done` unless the project's test suite is green. When tests cannot run,
say so plainly and report `partial`.

Last reviewed: 2026-10-01.
