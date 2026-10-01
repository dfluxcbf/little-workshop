# Governance

Source: `docs/governance_policy.md` (little-workshop's constitution).

## Principles

- **Independence.** little-workshop does not require another `little-*` project's tooling,
  database or process policy for its own governance: no `lreq`/`bundle-littles/req_db`, no
  `lvx`/`.lvx/config.json`. "Independent" means *not required to depend on*, not *forbidden from
  using*. Good ideas from the superseded tools are kept (two-branch model, sequential requirement
  ids, the `docs/` habit).
- **Evolution.** A rule may be improved, but at any moment the written rule is the rule in force,
  not an aspiration. Every change to a rule is **deliberate** (something was learned),
  **documented in the file itself** at the point of change (never only in a commit message or chat),
  and **dated**.
- **Self-application.** Whatever little-workshop enforces on other projects must already be true of
  little-workshop itself first. little-workshop is registered as a project in its own Registry and
  is its own first user of each pillar.

## Who is bound by what

| Project | Governance today |
|---|---|
| little-workshop | Its own `docs/*_policy.md` files; exempt from the super-repo requirements and version policies |
| little-mindcheck | Uses little-workshop's Requirements and Version pillars (migrated from `lreq`/`lvx` and uninstalled 2026-09-22 per its own policy files; `.lvx/config.json` kept as little-workshop's file format) |
| little-home, little-meals, little-spectrum | Their `docs/requirements_policy.md` / `version_policy.md` still describe `lreq` / `lvx` tooling; `lreq`/`lvx` are not on `PATH` and `bundle-littles` no longer exists beside the checkouts |
| ethereal-lands | Managed from WSL by little-workshop; has no `requirements_policy.md` or `version_policy.md`, but has a `VERSION` and a `.lvx/config.json` (`master`/`dev`, no version hooks) |

Project folders record the details. When a project's own policy file and the live system
disagree (for example, `little-meals` still describing `lreq`), verify against the live system
before relying on either; the maintenance job flags the mismatch.

## Policy lifecycle (Policy & Compliance)

- A policy is one EARS statement plus a generated test script, per scope (`all_projects` or
  `project`), with **gate points** (`branch_finish`, `release`, `package_publish`) where failure
  blocks the action.
- New policies pass four gates in `policy.author`: EARS compliance, mechanical enforceability,
  conflict check, then human activation. Drafts are decided in the Dashboard, never auto-approved
  (`docs/pending_policy_drafts/README.md`).
- **History-based checks judge only the newest commit or tag, never the whole past.** A past mistake
  must not fail a policy forever.
- Framework policies are hard rules while in force; changing one is development of little-workshop
  itself and follows the evolution principle above.

## Discrepancy notes

- `docs/governance_policy.md` states "There is no `.lvx/config.json`" for little-workshop. The
  checkout has a tracked `.lvx/config.json` and `.lvx/state.json` (written by `lws version init`,
  with `tools/on_version_bump.py` in `version_hooks`). The governance policy's own 2026-09-17
  amendment reads that rule as a past fact, not a standing invariant; its text has not been updated.
- `docs/version_policy.md` says `commit-bumps-version` and `branch-commit-dev-suffix` are scoped to
  little-workshop; the live attachment is wider ([policy_register.md](policy_register.md)).

## Jobs

User-triggered jobs are a catalog (`src/dashboard/job_catalog.py`): project, then job, then typed
arguments. Never free-form and never drafted by Claude. In this checkout (branch `dev`) the catalog
holds `build`, `run_tests` and `release`; see [README.md](README.md#maintenance) for the job that
maintains this folder.

Last reviewed: 2026-10-01.
