# little-spectrum standards

Checkout: `/home/crist/little-projects/little-spectrum`. Repository
`git@github.com:dfluxcbf/little-spectrum.git`. A C++ audio project: a Spotify virtual endpoint proxy
(capture, fan-out, delay and FFT branches, render), built with Bazel and a legacy `CMakeLists.txt`.

## Governing documents in the project

`docs/design.md`, `docs/architecture.md`, `docs/milestones.md`, `docs/index.md` (requirement catalog),
`docs/interfaces.md`, `docs/driver_and_endpoint.md`, `docs/requirements_policy.md`,
`docs/version_policy.md`, `NEXT_STEPS.md`.

## Project conventions

- `docs/index.md` is the canonical requirement catalog, with topic-prefixed ids (`RQ-VE-*`,
  `RQ-DLY-*`, `RQ-TEL-*`, `RQ-LFC-*`, `RQ-ERR-*`). These are **not** `REQ-#########` ids: no test is
  tagged with them yet, and they cannot gain a custom prefix. Backfilling means creating each
  requirement in the Requirements pillar to get a real id, tagging the test, and keeping the catalog
  number as a cross-reference.
- Production branch `master`, integration `dev` (`.lvx/config.json`).
- Version propagation: `.lvx/config.json` registers `tools/on_version_bump.py` in `version_hooks` and
  `.githooks/pre-commit` runs `lws version sync-version-files` on `feature/*` and `improvement/*`
  branches (checked 2026-10-01); `version-hook-installed` passes (latest recorded outcome).
- `CMakeLists.txt` is legacy; per the build policy it is marked deprecated in `architecture.md` once
  Bazel covers the same build.
- As a compiled project its Bazel artifact targets are `//:exe` rather than `//:wheel`.
- Its architecture documents use mermaid diagrams.

## Documentation to reconcile

`docs/version_policy.md` says no version hooks are configured (`version_hooks: []`); the config now
lists `tools/on_version_bump.py`. `docs/requirements_policy.md` and `docs/version_policy.md` still
describe `lreq` / `lvx`. The project has no `README.md`; its instructions are in `.claude/AGENTS.md` (2026-10-01).

## Policies

Only 3 policies gate this project: `commit-bumps-version`, `branch-commit-dev-suffix`,
`version-hook-installed`. The other 14 are attached with gating off
([../policy_register.md](../policy_register.md)).

Last reviewed: 2026-10-01 (hand edit 2026-10-01: instructions moved to `.claude/AGENTS.md`).
