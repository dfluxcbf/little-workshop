---
paths:
  - "tests/**"
---
# Tests

- `tests/unit` is the Definition-of-Done suite: network-free, no dependence on sibling projects or a running
  daemon, and never skipping inside a sandboxed worktree, because one skip or failure marks every requirement
  linked to the "Unit tests" suite as not done. Record DoD runs with `workshop_requirements_run_tests` and
  `["python3","-m","pytest","tests/unit"]`; the run takes minutes and the MCP call can time out while the job
  continues, so read the job status instead of retrying.
- `tests/integration` uses the live `little-workshop-<pillar>.service` unit names and stops the live pillars, so
  it never runs beside the live install; `.claude/hooks/live_install_guard.py` denies the commands that would
  reach it. Pass the explicit `tests/unit` path, for coverage runs too.
- Tag tests with the requirement ids they cover: a `# --- REQ-nnnnnnnnn: <title> ---` section per requirement
  (`shared_project_standards/requirements_standard.md`).
- `tests/ui`: the pytest-playwright `page` fixture and relative URLs (`page.goto("/")`) only.
- Reuse `tests/unit/conftest.py` and `tests/unit/socket_test_helpers.py` before adding new helpers.
- A policy script is tested as the artifact it is: run it with pytest in a subprocess against a scratch git
  repository, with `POLICY_PROJECT_DIR` (and `POLICY_COMMIT_DIR` for a pending commit) pointing at it.
