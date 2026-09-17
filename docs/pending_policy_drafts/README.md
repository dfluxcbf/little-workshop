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

_(none currently)_

## Rejected drafts

| File (removed) | Topic | Rejected | Why |
|---|---|---|---|
| `independence-no-lvx-config.json` | `independence-no-lvx-config` | 2026-09-17 | Not a policy candidate at all, by direct decision: "no `.lvx/config.json`" was a one-time fact about the `little-versions` migration, not an ongoing invariant to check — `docs/governance_policy.md`'s independence principle means "not required to depend on," never "must never contain." Once the migration is complete, whether `.lvx` exists is simply irrelevant — nothing reads it either way. See `docs/requirements.md`'s M15 follow-up section and `docs/governance_policy.md`'s own 2026-09-17 amendment. |
