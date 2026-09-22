# Pending policy drafts

**Not policies.** Nothing in this directory has any effect on anything —
these are Gate 1-3 outputs from `policy.author` (EARS-compliance,
mechanical-enforceability, and conflict checks, plus a generated test
script), kept as reviewable records.

**Approval happens in the app, not here.** Import the files into
little-workshop and review them in the Dashboard (Policies → "awaiting your
approval"), where each draft can be read in full, given gate points and
projects, then approved or rejected:

```bash
lws policy draft import docs/pending_policy_drafts/*.json
```

Import is idempotent (a topic already pending is refreshed; one that
already has a policy is skipped). Each file may carry `source`, `notes`
(review notes shown on the draft) and `concerns` (true = flagged "check
notes") alongside the `policy.author` fields. There is no CLI or MCP command
that approves a draft — `lws policy activate` remains the older, direct
Gate 4 for a single file, but drafts are meant to be decided in the app.
A decided draft's file can be deleted; the decision lives in the app.

## Current drafts

**Imported 2026-09-21 from the base projects** (`little-versions`, `little-requirements`,
`little-runner`, `little-pypi`, `bootstrap-little-project`) — 13 catalog candidates run through
Gates 1-3 with `lws policy catalog run`. **None is activated, and none has any gate points**: every
draft is framework tier, all-projects scope, no gating, so activating one as-is would only *audit*.
Decide scope and gate points at review time (`--gate-points`). "Refined" means Gate 1 rewrote the
EARS text — compare it with the catalog's wording before approving. The project-wide principle that a
history-based check judges **only the newest commit/tag, never the whole past**, is not built into
Gate 3; the notes flag the drafts that break it.

| File | Target pillar | Gate 1 refined the text | Review notes |
|---|---|---|---|
| `version-format.json` | `version` | yes | Reads the root `VERSION` only; single-line semver with optional `-dev.N`/`-rc.N`. Straightforward. |
| `production-version-stable.json` | `version` | yes | Reads files only. Straightforward. |
| `rc-suffix-only-on-release-branches.json` | `version` | no | Reads `VERSION` and the checked-out branch only. Straightforward. |
| `production-tag-format.json` | `version` | yes | **Audits every production tag in history** — a single old badly-named tag would fail it forever. Needs narrowing to the newest tag before activation (the project's own rule: judge only the newest). |
| `production-tags-on-production-history.json` | `version` | yes | Newest production tag only, reachable from the production branch. Matches the newest-only rule. |
| `release-candidate-tag-format.json` | `version` | no | **Audits every RC tag across every base version** (format and counter continuity) — fails forever on an old mistake. Needs narrowing to the newest candidate vs. its predecessor. |
| `back-merge-no-ff.json` | `version` | no | Gate 3 turned this into a **static scan of the project's source/docs text**, not a history check — a weak proxy for the stated rule. Review whether it should be dropped or rewritten against merge commits. |
| `hotfix-propagates-by-merge.json` | `version` | yes | Same as `back-merge-no-ff`: scans project source and docs for the word "cherry-pick". Weak proxy; review or drop. |
| `merge-commit-message-format.json` | `version` | yes | **Narrowed from the catalog's original wording** after Gate 2 rejected it as unenforceable (needed the branch type and an issue lookup). Now judges the newest merge commit's subject shape only. |
| `requirement-marker-registered.json` | `requirements` | no | File scan of pytest config; no git. Straightforward. |
| `requirement-marker-id-format.json` | `requirements` | yes | Scans test files for `requirement("...")` markers. Format only — cannot verify the ID exists in the requirements record. |
| `issue-form-installed.json` | `agent` | no | Checks the file exists. Only meaningful for projects that use issue-driven automation; the script decides that itself — review how. |
| `ssh-remotes-no-embedded-credentials.json` | `registry` | yes | Reads remote URLs; flags HTTPS remotes and embedded credentials. Review whether HTTPS-without-credentials should be allowed. |


## Rejected drafts

| File (removed) | Topic | Rejected | Why |
|---|---|---|---|
| `independence-no-lvx-config.json` | `independence-no-lvx-config` | 2026-09-17 | Not a policy candidate at all, by direct decision: "no `.lvx/config.json`" was a one-time fact about the `little-versions` migration, not an ongoing invariant to check — `docs/governance_policy.md`'s independence principle means "not required to depend on," never "must never contain." Once the migration is complete, whether `.lvx` exists is simply irrelevant — nothing reads it either way. See `docs/requirements.md`'s M15 follow-up section and `docs/governance_policy.md`'s own 2026-09-17 amendment. |

**Added 2026-09-21:** `wheel-filename-has-version` (target pillar `build`, suggested gate point
`package_publish`) — every `py_wheel` declares a version and every built wheel is named
`<name>-<version>-<tags>.whl`. Script: `tools/policy_scripts/wheel_filename_has_version.py`.

**Gate points and narrowing, 2026-09-22**: every pending draft now carries a gate point
(or `[]` for audit-only, deliberately — see below), reviewed against `docs/architecture.md`'s
ten action categories and this project's own `commit-bumps-version`/`branch-commit-dev-suffix`
precedent (`branch_finish`). `production-tag-format` and `release-candidate-tag-format` were
also narrowed to judge only the newest tag (of the current release line, for RCs) rather than
auditing full tag history, so one old mistake never fails them forever — the same principle
`commit-bumps-version` already applies to commits.

**Update 2026-09-22**: every pending draft now has a real gate point (none left
audit-only), at the user's request. Two of them still carry an open caveat worth reading
before approving:
- `back-merge-no-ff`, `hotfix-propagates-by-merge` (`branch_finish`): their own review notes
  flag the test script as a weak static-text-scan proxy standing in for a real git-history
  check that was never built. Gating on a weak proxy risks blocking real work for the wrong
  reason — consider rewriting or dropping the mechanism, gate point aside.
- `issue-form-installed` (`branch_finish` — corrected 2026-09-22 from an invalid
  `project_registration`, which only works on a framework-bound policy; see below): the script
  already reports `not_applicable` for a project that hasn't opted into issue-driven automation,
  so gating never blocks one that doesn't use it.
- `requirement-marker-id-format`, `requirement-marker-registered` (`branch_finish`): format-only
  checks that can't verify the referenced requirement actually exists — gated alongside this
  project's other pre-finish content checks (`commit-cites-requirement`).

**Correction 2026-09-22**: `project_registration` is only valid on a framework-bound policy
(`target_pillar == "policy"`) — a project-bound check has no project to inspect yet at the
moment of registration (a chicken-and-egg problem `src/policy/app.py`'s own
`_validate_gate_points_against_target` already guards against, per `docs/open_questions.md`'s
`OQ-15`). Two project-bound drafts had wrongly been given it: `issue-form-installed`
(target_pillar `agent`) and `ssh-remotes-no-embedded-credentials` (target_pillar `registry`).
The app itself refused approval with a clear error rather than silently accepting either; both
are now `branch_finish`.

A parsing bug in Gate 1 (`src/policy/gates.py`) leaked a model's own reasoning preamble into
`production-version-stable`'s stored `ears_text` when the reply didn't start with the marker
line; fixed (the marker is now found anywhere in the reply, taking the last one), and that
draft's `ears_text` was cleaned up.

**Projects affected** (`docs/pillars/dashboard.md`'s "Apply to" step) is chosen at approval time
in the Dashboard and isn't stored on a draft, so it isn't pre-filled here. Recommendation: apply
each policy to every registered project, gating on for little-home, little-meals,
little-mindcheck and little-workshop, gating off for little-spectrum (already known to be in a
bad state — still checked and recorded, never blocks).
