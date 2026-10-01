# little-home standards

Checkout: `/home/crist/little-projects/little-home`. Repository `git@github.com:dfluxcbf/little-home.git`.
A **TypeScript** project: SvelteKit full-stack web app, `vitest` specs under `src/src/`, a `db/`
folder and a `docker-compose.yml`, built with Bazel.

## Governing documents in the project

`README.md`, `docs/design.md`, `docs/architecture.md`, `docs/milestones.md`,
`docs/requirements_policy.md`, `docs/version_policy.md`.

## Project conventions

- Registered requirements project id `little-home-reqs`, one test suite per milestone
  (`M1 - Foundations` through `M7 - First launch`).
- It has `.lvx/` and `.reqs/` and its policy files describe `lreq`/`lvx`. As with little-meals, the
  shared standards treat little-workshop's pillars as the system of record; verify and reconcile.
- It is a Node project, so pytest-specific rules do not apply: the requirement-marker policies and
  `python-test-coverage` skip it (no `pyproject.toml`); requirement tagging uses its own `vitest`
  convention.
- Its server runs on demand; down is normal.
- Version propagation: `.lvx/config.json` registers `tools/on_version_bump.py` in `version_hooks` and
  `.githooks/pre-commit` runs `lws version sync-version-files` on `feature/*` and `improvement/*`
  branches (checked 2026-10-01); `version-hook-installed` passes (latest recorded outcome).

## Documentation to reconcile

`docs/version_policy.md` says "No version hook script yet", but `tools/on_version_bump.py` exists and
is registered in `.lvx/config.json`; `docs/requirements_policy.md` and `docs/version_policy.md` still
describe `lreq` / `lvx`. The live configuration is authoritative. The project has no `CLAUDE.md`.

## Policies

17 of 19 policies gate this project, all except `back-merge-no-ff` and `hotfix-propagates-by-merge`
([../policy_register.md](../policy_register.md)). No policy is scoped to it alone.

Last reviewed: 2026-10-01.
