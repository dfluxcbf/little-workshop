# Pending policy drafts

**Not policies.** A file here is a Gate 1-3 output from `policy.author` (EARS-compliance,
mechanical-enforceability and conflict checks, plus a generated test script), kept as a reviewable
record until a person decides it. Nothing here has any effect.

**Approval happens in the app.** Import the files and review them in the Dashboard (Policies →
"awaiting your approval"), where each draft can be read in full, given gate points and projects, and
approved or rejected:

```bash
lws policy draft import docs/pending_policy_drafts/*.json
```

Import is idempotent (a topic already pending is refreshed; one that already has a policy is
skipped). A decided draft's file is deleted: the decision lives in the app.

**Active policies are code, not drafts.** Every active policy is a script in
`tools/policy_scripts/` with its rule, scope and gate points in `tools/policy_scripts/policies.json`;
`python3 tools/sync_policies.py` compares them with the live pillar and `--apply` replaces what
differs. An approved draft's script moves there.

## History

The 13 catalog candidates imported on 2026-09-21 from the base projects (`little-versions`,
`little-requirements`, `little-runner`, `little-pypi`, `bootstrap-little-project`), plus
`python-test-coverage` and `wheel-filename-has-version`, were all approved and activated; their draft
files were removed on 2026-10-02, when every policy's script moved into `tools/policy_scripts/`. Two
open caveats from their review were resolved then: `back-merge-no-ff` and `hotfix-propagates-by-merge`
had been text scans of the project's source (and failed on text that forbids merging by hand); they now
judge git history.

## Rejected drafts

| File (removed) | Topic | Rejected | Why |
|---|---|---|---|
| `independence-no-lvx-config.json` | `independence-no-lvx-config` | 2026-09-17 | Not a policy candidate at all, by direct decision: "no `.lvx/config.json`" was a one-time fact about the `little-versions` migration, not an ongoing invariant to check — `docs/governance_policy.md`'s independence principle means "not required to depend on," never "must never contain." Once the migration is complete, whether `.lvx` exists is simply irrelevant — nothing reads it either way. See `docs/requirements.md`'s M15 follow-up section and `docs/governance_policy.md`'s own 2026-09-17 amendment. |
