# little-workshop standards

Repository `git@github.com:dfluxcbf/little-workshop.git`. `.claude/AGENTS.md` imports this file into every
session: keep it short, and put what only one part of the tree needs in a `.claude/rules/` file.

- Governing documents: `docs/governance_policy.md` (the constitution), `docs/requirements_policy.md` and
  `docs/version_policy.md` (its own, exempt from the super-repo's), `docs/requirements.md` (the register index;
  rows in `docs/requirements/`, mirrored in the Requirements pillar), `docs/pillars/*.md` (one spec per pillar).
- Policies are code: `tools/policy_scripts/` plus `policies.json`. Besides [../policy_register.md](../policy_register.md),
  its own are `back-merge-no-ff` and `hotfix-propagates-by-merge`: a release or hotfix reaches `dev` by a
  non-fast-forward merge, never a cherry-pick.
- Commit subjects read `feat|impr|fix (<issue or -1>): <title>`; the body cites `REQ-nnnnnnnnn` or `REQ-NONE`.
- `VERSION` is written by the finish; `pyproject.toml`'s version follows it (`tools/sync_versions.py`).
- Test suites: [../testing_standard.md](../testing_standard.md).

Last reviewed: 2026-11-07 (hand edit: merged with `.claude/AGENTS.md`; job catalog, agent prompts and versioning
are left to their path-scoped rules).
