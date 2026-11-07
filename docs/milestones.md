# Milestones

The work sequence: one row per milestone with its status. A milestone is done once it is documented, its
requirements are in the Requirements pillar and the register (`docs/requirements.md`), implemented, covered
by automated tests, and those tests pass. What is built is described in `docs/architecture.md` and the pillar
specifications (`docs/pillars/`); the requirements say what each milestone delivered. Earlier sections told
each completed milestone's history; that history is in git (before 2026-11-07) and in the requirements.

Work asked for but not yet refined or scheduled is in the project's backlog, which Registry & Portfolio keeps
([Backlog](pillars/registry_portfolio.md#backlog-2026-10-04)): the Dashboard's Backlog page, or
`lws project backlog list --project-id little-workshop`.


## Overview

| ID | Objective | Depends on | Status |
|---|---|---|---|
| M1 | Core + Orchestration | — | Done |
| M2 | Registry & Portfolio — bootstrap path | M1 | Done |
| M3 | Version Control & Release | M1, M2 | Done |
| M4 | Deployment & Servers | M1, M2 | Done |
| M5 | Self-redeploy (reflexive) | M4 | Done |
| M6 | Agentic Development | M1, M2, M3, M4 | Done |
| M7 | MCP Server — minimal | M2, M3, M4, M6 | Done |
| M8 | Registry & Portfolio — import path | M2, M6 | Done |
| M9 | Orchestration — scheduled tasks | M1 | Done |
| M10 | Requirements & Quality | M1, M2, M4 | Done |
| M11 | Build & Packages | M1, M2, M4 | Done |
| M12 | Policy & Compliance | M1, M4, M6, M10 | Done |
| M13 | Populate the policy catalog | M12 | In progress — tool built, 1/74 entries activated as a real proof; see `docs/requirements.md` M13 |
| M14 | Dashboard | M2–M13 (whatever exists by then) | Done — Overview/Projects/Jobs/Policy panels; see `docs/pillars/dashboard.md` |
| M15 | Self-hosting / dogfooding | M2, M3, M10, M12 | Done — registered, requirements migrated, first framework-policy draft; see `docs/requirements.md` M15 |
| M16 | Full functional parity with the absorbed tools | M1–M14 (whichever tool each gap belongs to) | Done (2026-09-17) — every real gap in all five absorbed tools closed (`docs/requirements.md`'s M16 follow-up); the handful of items left un-ported (`.lrun/config.toml`, `little-requirements`' client-config model, `lock`/`unlock`) are documented, deliberate architectural non-gaps, not deferred work |
| M17 | Ecosystem cutover | M15, M16 | In progress — `little-mindcheck` is the pilot (registered, requirements data migrated, real `deploy`/`stop`/`status` verified against the actual app, 2026-09-17); a new `deployment.set_manifest` capability and a real dispatch-timeout fix were required along the way (`docs/requirements.md`'s M17 section) |
| M18 | Claude usage — quick wins | M6, M7, M9 | Done (2026-10-04) — run usage stored, `agent.query` cache fix, `workshop_job_wait`, redeploy and wait routes; see `docs/requirements.md` |
| M19 | Claude usage — collection (the `usage` pillar) | M18 | Done (2026-10-04) — built and unit-tested, not yet deployed; see `docs/pillars/usage.md` |
| M20 | Claude usage — findings | M19 | Done (2026-10-04) — built and unit-tested, not yet deployed; see `docs/pillars/usage.md` |
| M21 | Claude usage — automation mining and workshop triggers | M20 | Done (2026-10-04) — built and unit-tested, not yet deployed; see `docs/pillars/usage.md` and `docs/pillars/orchestration.md` |
| M22 | Claude usage — token-aware operation | M19 | Done (2026-10-04) — built and unit-tested, not yet deployed |
| M23 | Image Generation (the `generation` pillar): a ComfyUI queue | M7, M14 | Built 2026-10-23; removed 2026-11-07 (the role moves to little-render) |

## M13 — Populate the policy catalog

Distinct from building the pillar (M12): running the ~50 entries in
`docs/pillars/policy_catalog.md` — confirmed good, tier already
decided — through the real four-gate pipeline one at a time, including
the two-part secret-detection mechanism
(`secret-entry-point-tagged`/`secret-scan-best-effort`) resolved this
round. This is real, individual authoring work per entry, not a bulk
import — each one earns its `policies` row the same way any future
policy will.

## M17 — Ecosystem cutover

Every other project moves off `lreq`/`lvx`/`lrun`/`little-pypi` onto `lws`, one project at a time, verified
against the same policies before and after. Uninstalling the old tools is never part of a milestone: it is the
maintainers' manual decision. Pilot `little-mindcheck` (started 2026-09-17): registered, requirements migrated,
`deploy`/`stop`/`status` verified. Still open there: its daily-use scripts (`lrun server ...`,
`tools/report_test_results.sh`) and the `little-requirements` pytest plugin, which needs a marker-to-requirement
mapping before `lws requirements import-junit` can replace it; that work happens on a branch in its own repo.
