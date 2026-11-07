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
- `feature/<name>`, `improvement/<name>` — branched from `dev`, squash-merged
  back into `dev`: one commit, no merge commit (amended 2026-10-07; it was `--no-ff`).
- `release/<version>` — cut from `dev`, merged into `master` and back into
  `dev`.
- `hotfix/<name>` — branched from `master`, merged into `master` and `dev`.
- `support/<version>` — maintenance of an older release line.

A hotfix or a release merges with plain `git merge --no-ff`; a feature or improvement with
`git merge --squash` (amended 2026-10-07). No tool is required to create, merge or
delete these branches.

## Version numbering

`VERSION`, a plain-text file at the repository root, is the single source of
truth. Format is Semantic Versioning plus two pre-release signals:

| Form | Example | Meaning |
|---|---|---|
| `M.m.p` | `1.4.2` | Stable release |
| `M.m.p-dev.N` | `1.5.0-dev.3` | Development snapshot (only on branches cut before 2026-11-07) |
| `M.m.p-rc.N` | `1.5.0-rc.2` | Release candidate, on a release branch |

Precedence: a stable version outranks its own pre-releases; `dev` ranks below
`rc`; then the counter decides. Counters restart at `1` whenever the base
version changes.

### Bumps by stage (from little-versions)

| Stage | Bump |
|---|---|
| feature / improvement **finish** | next patch on `dev`, written once by the finish |
| release **start** | sets the stable base on the release branch |
| release **candidate cut** | `-rc.N` only (major/minor/patch unchanged) |
| release **finish** | none on `master` (it keeps the released version); `dev` gets the next patch |
| hotfix **finish** | patch on `master` |
| feature/improvement/hotfix/release/support **start**, candidate promote | none |

Production tags are annotated `v<M.m.p>`, RC tags `v<M.m.p>-rc.N`; a tag exists
exactly once and only on production history.

## Working branches leave the version alone (2026-11-07)

`feature/` and `improvement/` commits never touch `VERSION` or the files that carry it: the finish
computes the next patch after `dev`'s version and writes it once, with the version hooks
(`tools/on_version_bump.py` writes `pyproject.toml` and `MODULE.bazel`, PEP 440 form for a
pre-release). This replaced the per-commit `-dev.N` rule and its two policies
(`commit-bumps-version`, `branch-commit-dev-suffix`), whose bumps conflicted in every rebase. A
branch whose commits still carry `X.Y.Z-dev.N` lands on `X.Y.Z`. `version-format` still checks the
file at the finish.

## Branch and merge notes

- `master` and `dev` both exist and are pushed to
  `github.com/dfluxcbf/little-workshop`.
- little-runner's cross-repo branch audit (per the root
  [branching policy](../../docs/policies/branching_policy.md)) still
  observes this repository for basic git hygiene (stray branches, detached
  HEAD, extra worktrees) — read-only, imposing no rule on it.
- little-workshop's own branches, finishes and releases go through its Version Control & Release pillar
  (`workshop_version_*`), its own first user per the self-application principle in
  `docs/governance_policy.md`.
