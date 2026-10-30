---
paths:
  - "VERSION"
  - "pyproject.toml"
  - "tools/sync_versions.py"
  - "tools/on_version_bump.py"
  - "src/version/**"
  - ".githooks/**"
  - ".lvx/**"
---
# Versions and hooks

- Every commit raises `VERSION` (policy `commit-bumps-version`), and a working branch's commits carry the next
  patch plus `-dev.N` (`branch-commit-dev-suffix`); the commit-msg hook checks both. On `feature/` and
  `improvement/` branches `.githooks/pre-commit` writes the right value (`lws version sync-version-files`).
- Run `tools/sync_versions.py` when `VERSION` changes; never hand-edit the version in `pyproject.toml`.
- `.githooks/` is generated: `lws version install-hooks --project-id little-workshop` (or `lws version init`)
  writes `pre-commit` and `commit-msg` from `src/version/commands/sync_version_files.py`; change the generator,
  not the copies. `lws version init` also rewrites `.lvx/config.json`, so use `install-hooks` to refresh hooks.
- The Version pillar's own git calls run with hooks off (`core.hooksPath=/dev/null` in `src/version/gitops.py`):
  finish and bookkeeping commits write `VERSION` themselves and must not be re-bumped by the hooks.
