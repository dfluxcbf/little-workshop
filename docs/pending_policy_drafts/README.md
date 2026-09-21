# Pending policy drafts

**Not policies.** Nothing in this directory has any effect on anything —
these are Gate 1-3 outputs from `policy.author` (EARS-compliance,
mechanical-enforceability, and conflict checks, plus a generated test
script), held here purely for human review before Gate 4. `policy.author`
never saves anything itself; the only path that ever writes a real
`policies` row is `policy.activate`, and only with an explicit
`approved: true` (`docs/pillars/policy_compliance.md`'s "stays strictly
proposals until Gate 4").

A file here is committed for reviewability (so it's readable as a normal
diff on GitHub, not a local temp path), not because it's binding. Once a
draft is reviewed and approved, activate it for real and delete the file
from here in the same change:

```bash
lws policy activate docs/pending_policy_drafts/<file>.json
git rm docs/pending_policy_drafts/<file>.json
```

If a draft is rejected instead, just delete the file — nothing to undo,
since nothing was ever saved.

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
