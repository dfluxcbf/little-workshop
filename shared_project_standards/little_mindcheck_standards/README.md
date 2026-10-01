# little-mindcheck standards

Checkout: `/home/crist/little-projects/little-mindcheck`. Repository
`git@github.com:dfluxcbf/little-mindcheck.git`. A check-in PWA: Python, FastAPI backend,
Bazel, wheel packaging.

## Governing documents in the project

`README.md`, `docs/design.md`, `docs/architecture.md`, `docs/milestones.md`,
`docs/requirements_policy.md`, `docs/version_policy.md`.

## Project conventions

This is the model project for the shared standards: it has already **migrated off `lreq` and `lvx`**
(2026-09-22) onto little-workshop.

- Requirements and test coverage are managed through the Requirements pillar
  (`lws requirements ...`); the database is never edited directly. History: 23 requirements
  (`REQ-000000001`-`REQ-000000023`, plus `REQ-000000032`) in suite `m2-v2-pwa`, migrated 2026-09-22.
- Gitflow is managed through `lws version ...`, working against `.lvx/config.json` (production
  `master`, integration `dev`).
- Version propagation: `.lvx/config.json` registers `tools/on_version_bump.py` in `version_hooks` and
  `.githooks/pre-commit` runs `lws version sync-version-files` on `feature/*` and `improvement/*`
  branches (checked 2026-10-01); `version-hook-installed` passes (latest recorded outcome).

## Documentation to reconcile

`docs/version_policy.md` says "No version hook script yet"; `tools/on_version_bump.py` exists and is
registered in `.lvx/config.json`. The project has no `CLAUDE.md`.

## Policies

17 of 19 policies gate this project, all except `back-merge-no-ff` and `hotfix-propagates-by-merge`
([../policy_register.md](../policy_register.md)). No policy is scoped to it alone.

Last reviewed: 2026-10-01.
