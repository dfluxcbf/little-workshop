# Version and branching standard

Sources: `docs/version_policy.md`, `docs/pillars/version_control_release.md`, the agent branching
prompts, and the policies listed in [policy_register.md](policy_register.md).

## Branch model

| Branch | Purpose |
|---|---|
| `master` | Production. Always releasable |
| `dev` | Integration. Work lands here before release |
| `feature/<name>`, `improvement/<name>` | From `dev`; squash-merged back into `dev` (one commit, no merge commit) |
| `release/<version>` | Cut from `dev`; merged into `master` and back into `dev` |
| `hotfix/<name>` | From `master`; merged into `master` and `dev` |
| `support/<version>` | Maintenance of an older release line |

- A hotfix or a release merges with `git merge --no-ff`; a feature or improvement is squash-merged. A hotfix reaches the integration branch **by merge only**; it is never
  copied commit by commit.
- An agent task on a milestone uses `feature/`; every other task uses `improvement/`. The agent
  never commits on the integration branch, never merges, never force-pushes, and never pushes:
  little-workshop pushes the branch and opens the pull request.
- Every registered project has a `.lvx/config.json` naming `master` as production and `dev` as
  integration (read by `lws version ...`); `version_hooks` lists the propagation scripts, which is
  where `version-hook-installed` looks.

## `VERSION`

A plain-text file at the repository root is the single source of truth.

| Form | Example | Meaning |
|---|---|---|
| `M.m.p` | `1.4.2` | Stable |
| `M.m.p-dev.N` | `1.5.0-dev.3` | Development snapshot |
| `M.m.p-rc.N` | `1.5.0-rc.2` | Release candidate, only on `release/*` |

Precedence: stable outranks its own pre-releases; `dev` ranks below `rc`; then the counter. Counters
restart at `1` when the base changes. `master` always holds a stable version.

### Bumps by stage

| Stage | Bump |
|---|---|
| feature / improvement finish | patch on `dev`, dev suffix reset to `1` |
| release start | sets the stable base on the release branch |
| release candidate cut | `-rc.N` only |
| release finish | minor on `master`; `dev` gets the next patch with `-dev.1` |
| hotfix finish | patch on `master` |
| starts, candidate promote | none |

### Every commit bumps `VERSION` (little-workshop, 2026-09)

1. Each commit's `VERSION` has higher precedence than its parent's.
2. On a working branch (not `master`, `dev`, `release/*`, `hotfix/*`, `support/*`) the first commit
   is the **next patch** of the branch point with `-dev.1`; later commits keep the base and add one
   to `N`.
3. Files that carry the version follow `VERSION`: run the project's sync script (little-workshop:
   `tools/sync_versions.py`, writing PEP 440 form such as `0.1.1.dev2` into `pyproject.toml`); never
   edit the field by hand. Where another file carries the version, the VERSION auto-sync pre-commit
   hook is installed and a propagation script registered (`version-hook-installed`).

Two hooks are installed by `lws version install-hooks` (and `lws version init`). `.githooks/pre-commit`
runs `lws version sync-version-files` on `feature/*` and `improvement/*` branches only and never blocks.
`.githooks/commit-msg` runs the policies gating `commit` (`commit-bumps-version`,
`branch-commit-dev-suffix`, `version-format`, `commit-cites-requirement`) against the commit being made
and refuses it when one fails, printing the fix; when little-workshop is unreachable it warns and lets
the commit through, and the same policies still gate the branch finish. `release/*`, `hotfix/*`,
`support/*`, `dev` and `master` get their `VERSION` from the finish commands, whose commits run with
hooks off.

Documented as a little-workshop rule (`docs/version_policy.md`, which names `commit-bumps-version`
and `branch-commit-dev-suffix` as scoped to little-workshop), but the live system also attaches both
with gating on to little-home, little-meals, little-mindcheck and little-spectrum (see
[policy_register.md](policy_register.md)); ethereal-lands is attached to neither. The project
folders say what each project's own documents claim.

## Tags

- Production: annotated `v<M.m.p>`, once, on production history only.
- Release candidate: annotated `v<M.m.p>-rc.N` on the release branch, `N` starting at 1 and rising by
  one.

## Merge commit subjects

On the integration or production branch, a merge commit's first line is
`feat (<issue number or -1>): <title>`, `impr (<issue number or -1>): <title>` or
`fix (<issue number or -1>): <title>`. Task commit prefixes in this repo follow the same
`feat:` / `impr:` / `fix:` types.

## Release job conditions (Dashboard "Release")

Minimum delta in changed lines, minimum patch bumps (combined with and/or), all tests pass, all
requirements pass, all policies pass. A running app is relaunched when the release finishes.

## Repository hygiene

- Remotes are SSH URLs without embedded credentials (`ssh-remotes-no-embedded-credentials`).
- Stray branches, detached HEAD and extra worktrees are observed read-only by the cross-repo
  branch audit.

Last reviewed: 2026-10-07 (hand edit: features and improvements are squash-merged; source docs/version_policy.md).
