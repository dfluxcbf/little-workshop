---
paths:
  - "tests/**"
---
- `tests/unit`: network-free, never skips in a sandboxed worktree, no dependence on sibling projects or a running daemon.
- Tag tests with the requirement ids they cover (see `shared_project_standards/requirements_standard.md`).
- `tests/ui`: pytest-playwright `page` fixture and relative URLs (`page.goto("/")`) only.
- Reuse `tests/unit/conftest.py` and `socket_test_helpers.py` before adding new helpers.
