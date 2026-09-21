# Version policy

**little-workshop is exempt from the super-repo's
[version policy](../../docs/policies/version_policy.md)** — see
`docs/governance_policy.md` for why, and `docs/README.md`'s "Exemption:
little-workshop" for where the super-repo itself sanctions this. This file
states little-workshop's own version policy in full; it does not point back
to the root document, because it is not bound by it.

The exemption is about **tooling**, not ideas. Branch model and version
numbering below are imported from `little-versions` (`docs/target_design.md`,
`docs/release-policy.md`) — the design little-workshop's own Version Control &
Release pillar absorbs — plus one rule `little-versions` never defined
(per-commit versions on working branches). What little-workshop does not do is
depend on the `lvx` tool or its `.lvx/config.json`.

## Branch model (from little-versions)

- `master` — production. Always releasable.
- `dev` — integration. Work lands here before release.
- `feature/<name>`, `improvement/<name>` — branched from `dev`, merged back
  into `dev` (`--no-ff`).
- `release/<version>` — cut from `dev`, merged into `master` and back into
  `dev`.
- `hotfix/<name>` — branched from `master`, merged into `master` and `dev`.
- `support/<version>` — maintenance of an older release line.

Merges use plain `git merge --no-ff`. No tool is required to create, merge or
delete these branches.

## Version numbering

`VERSION`, a plain-text file at the repository root, is the single source of
truth. Format is Semantic Versioning plus two pre-release signals:

| Form | Example | Meaning |
|---|---|---|
| `M.m.p` | `1.4.2` | Stable release |
| `M.m.p-dev.N` | `1.5.0-dev.3` | Development snapshot |
| `M.m.p-rc.N` | `1.5.0-rc.2` | Release candidate, on a release branch |

Precedence: a stable version outranks its own pre-releases; `dev` ranks below
`rc`; then the counter decides. Counters restart at `1` whenever the base
version changes.

### Bumps by stage (from little-versions)

| Stage | Bump |
|---|---|
| feature / improvement **finish** | patch on `dev`, dev suffix reset to `1` |
| release **start** | sets the stable base on the release branch |
| release **candidate cut** | `-rc.N` only (major/minor/patch unchanged) |
| release **finish** | minor on `master`; `dev` gets the next patch with `-dev.1` |
| hotfix **finish** | patch on `master` |
| feature/improvement/hotfix/release/support **start**, candidate promote | none |

Production tags are annotated `v<M.m.p>`, RC tags `v<M.m.p>-rc.N`; a tag exists
exactly once and only on production history.

## Every commit bumps the version (new)

`little-versions` bumps only at stage boundaries, so a working branch could
carry many commits that all claim the same version. That is no longer allowed.

1. **Every commit shall bump `VERSION`** — the new commit's version must have
   higher precedence than its parent's. This applies to every branch.
2. **On a working branch** (anything other than `master`/`dev`, and other than
   `release/*`, `hotfix/*` and `support/*`, whose versions the stage table
   above already defines), each commit's `VERSION` is `<M.m.p>-dev.N`:
   - the **first** commit of the branch is the *next patch* after the version
     of the commit it branched from, with `N = 1` — from `0.1.0` that is
     `0.1.1-dev.1`; from a `dev` line at `0.1.1-dev.1` it is `0.1.2-dev.1`;
   - every later commit keeps that base and increments `N` by exactly one
     (`0.1.1-dev.2`, `0.1.1-dev.3`, …).
   The next-patch base is what the feature-finish bump lands on, so the branch
   version and the `dev` version after merging agree.
3. **Version-bearing metadata follows `VERSION`.** `tools/sync_versions.py`
   writes it into `pyproject.toml` in PEP 440 form (`0.1.1-dev.2` →
   `0.1.1.dev2`) and must be run whenever `VERSION` changes; never edit that
   field by hand.

### How this is enforced

Two Policy & Compliance policies (`docs/pillars/policy_catalog.md`, section C),
scoped to little-workshop and gating `branch_finish`:

- `commit-bumps-version` — rule 1;
- `branch-commit-dev-suffix` — rule 2.

Their test scripts live in `tools/policy_scripts/` and compare **only the newest
commit with its immediate parent**. History is never re-audited: a mistake made
in the past does not fail the policy forever — it only means the next commit has
to be right. When the parent's own version is malformed, the newest commit is
required to be well-formed on its own and nothing more.

## Branch and merge notes

- `master` and `dev` both exist and are pushed to
  `github.com/dfluxcbf/little-workshop`.
- little-runner's cross-repo branch audit (per the root
  [branching policy](../../docs/policies/branching_policy.md)) still
  observes this repository for basic git hygiene (stray branches, detached
  HEAD, extra worktrees) — read-only, imposing no rule on it.
- This section will be rewritten once little-workshop manages its own branches
  through its Version Control & Release pillar end to end — its own first user,
  per the self-application principle in `docs/governance_policy.md`.
