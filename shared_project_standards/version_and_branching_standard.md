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
| `M.m.p-dev.N` | `1.5.0-dev.3` | Development snapshot (branches cut before 2026-11-07) |
| `M.m.p-rc.N` | `1.5.0-rc.2` | Release candidate, only on `release/*` |

Precedence: stable outranks its own pre-releases; `dev` ranks below `rc`; then the counter. Counters
restart at `1` when the base changes. `master` always holds a stable version.

### Bumps by stage

| Stage | Bump |
|---|---|
| feature / improvement finish | next patch on `dev`, written once |
| release start | sets the stable base on the release branch |
| release candidate cut | `-rc.N` only |
| release finish | none on `master` (it keeps the released version); `dev` gets the next patch |
| hotfix finish | patch on `master` |
| starts, candidate promote | none |

### Working branches leave `VERSION` alone (2026-11-07)

`feature/*` and `improvement/*` commits never change `VERSION` or the files that carry it; the finish
writes the next patch once and runs the version hooks, which write it into the other files
(little-workshop: `tools/on_version_bump.py`, PEP 440 form such as `0.1.1.dev2` for a pre-release);
never edit those fields by hand. Where another file carries the version, a propagation script is
registered (`version-hook-installed`). `release/*`, `hotfix/*`, `support/*`, `dev` and `master` get
their `VERSION` from the finish commands, whose commits run with hooks off.

`lws version install-hooks` (and `lws version init`) installs one hook, `.githooks/commit-msg`: a local
scan (`lws policy scan-secrets`, no daemon or Docker) that refuses a commit adding a secret, and lets
the commit through with a warning when `lws` is missing. Every other policy gates the branch finish.

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

Last reviewed: 2026-11-07 (hand edit: the attachment note now matches `docs/version_policy.md`); before that 2026-10-24.
