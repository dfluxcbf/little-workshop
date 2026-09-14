# Version policy

This project follows the super-repo's
[version policy](../../docs/policies/version_policy.md): gitflow branching and
version numbering via `little-versions` (`lvx`).

## Project-specific notes

- `.lvx/config.json`: production branch `master`, integration branch `dev`.
- No version hook script yet — add one under `tools/` once this project has
  version-bearing metadata files that need to stay in sync with `VERSION`.
