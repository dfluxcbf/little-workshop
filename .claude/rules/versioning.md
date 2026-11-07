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

- Working branches never change `VERSION`: the feature/improvement/hotfix/release finishes write it once and run
  the version hooks (`tools/on_version_bump.py`). Old branches whose commits carry `-dev.N` still finish.
- Run `tools/sync_versions.py` when `VERSION` changes; never hand-edit the version in `pyproject.toml`.
- `.githooks/` is generated: `lws version install-hooks --project-id little-workshop` (or `lws version init`)
  writes `commit-msg` (the local secret scan, `lws policy scan-secrets`) from `src/version/commands/git_hooks.py`
  and removes the retired `pre-commit`; change the generator, not the copies. `lws version init` also rewrites
  `.lvx/config.json`, so use `install-hooks` to refresh hooks.
- The version state (finishes in progress, issue links) is under the state directory, never in the repository
  (`src/version/configstore.py`); `.lvx/state.json` is read once, if present, and is git-ignored.
- The Version pillar's own git calls run with hooks off (`core.hooksPath=/dev/null` in `src/version/gitops.py`).
