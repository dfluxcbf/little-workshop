# Version Control & Release

## Description

Version Control & Release is Gitflow as a pillar: precondition-checked
branch operations (`feature`/`improvement`/`hotfix`/`release`/`support`
start/finish), release-candidate cut/list/promote, version bumping, and
per-project hook execution — absorbing `little-versions` wholesale. It
holds no database of its own; git itself, in the target project's own
repository, is the store. `.lvx/config.json`'s on-disk format is kept
byte-compatible with the standalone tool it replaces on purpose, so every
already-`lvx`-managed project in this ecosystem needs no migration once
the ecosystem cutover (M17) happens.

## Purpose & goal

"Branches and versions are always in a known-safe, consistent state —
never by hand, never inconsistently" (`docs/architecture.md`). Every
operation is precondition-checked before it touches git — a
`feature_start` on the wrong base branch, or a `release_finish` with no
matching tag, fails cleanly with a reason rather than leaving the
repository in a half-finished state.

## Responsibilities

- **Topic branches** (`feature`/`improvement`) — start from the
  integration branch, finish by rebase + squash to one `feat (N): name`
  (or `impr (N): name`) commit, merge `--no-ff`, patch-version bump,
  delete the branch. `--continue`/`--abort` resume or cancel a finish
  paused by a real rebase conflict.
- **Hotfixes** — start from the production branch, finish by merging into
  production, tagging, patch-version bump, and back-merging into the
  integration branch.
- **Releases** — start from the integration branch; cut/list/promote
  release-candidate tags (`v<version>-rc.N`); finish by merging the
  promoted candidate into production, tagging `v<version>`, and
  back-merging into the integration branch.
- **Support branches** — `support/<version>` from a matching production
  tag, for maintaining an older release line. `support_finish` is
  deliberately policy-driven and always refuses rather than picking a
  merge target on its own — support history must never silently diverge
  from the primary lines.
- **Config and hooks** — `GET /v1/config` (the effective, per-repo-plus-
  framework-defaults branch-type policy: base branch, merge targets,
  staleness exemption, issue-automation eligibility), `.sh`/`.py` version
  hooks run after every bump.
- **`GET /v1/status/{kind}`** — metadata for whichever branch kind
  (`feature`/`improvement`/`hotfix`/`release`) is currently checked out.
- **Standalone issue linking** (`version.issue_set`/`issue_unset`,
  `GET /v1/issues`) — added 2026-09-17 closing M16's `little-versions`
  parity gap: view or change a branch's linked issue number after the
  fact, not just once at its own `*_start` time. Reuses the same
  `issue_automation` flag `branch_type_policy()` already carries per
  branch type (`feature`/`improvement`/`hotfix`) as the single source of
  truth for which branch types can be linked at all, rather than a
  second, separately maintained list.
- **`version.branch_audit`** (`version/branch_audit.py`) — added
  2026-09-17 closing M16's `little-runner` parity gap (`lrun branch-audit
  run`): a read-only branch-hygiene classifier over one project's real
  branches, ported from the ecosystem's own root `branch_audit.py`
  (naming, stale-merged, abandoned, detached-HEAD, stray-worktree
  violations). Never merges, deletes, commits, or pushes anything — the
  same `docs/policies/branching_policy.md` rule the original tool states
  outright ("Automation never merges, deletes, or force-pushes a branch
  on its own initiative"). Scoped to one project's own checkout, unlike
  the original tool's own ecosystem-wide sweep — this daemon's actual
  authority for "every project" is Registry & Portfolio's project list,
  so `lws version branch-audit --all` loops this Job over it client-side
  rather than this pillar walking every submodule directly. The old
  tool's GitHub tracking-issue sync (opening/closing a persistent
  `branch-hygiene`-labeled issue per repo) is left un-ported: reporting
  only, matching this Job's own read-only scope — automating a GitHub
  side effect from a read-only audit was judged a separate decision, not
  bundled into closing the parity gap.
- **`version.stabilize`** (`version/stabilize.py`) — added 2026-09-17
  closing M16's last real `little-runner` parity gap (`lrun stabilize
  run`), ported from the ecosystem's own root `stabilize_branches.py`.
  Unlike `branch_audit`, this genuinely mutates: fetches `origin`, brings
  the integration branch up to date via a real fast-forward-only merge
  (a genuine divergence reports `needs_manual_resolution`, never a
  force-merge), commits pending changes, finishes the currently-checked-
  out feature/improvement/hotfix branch by calling this pillar's own
  already-existing `cmd_topic_finish`/`cmd_hotfix_finish` directly
  (never a second implementation), pushes, and deletes already-merged
  remote/local branches (skipping `dev`/`develop`/`master`/`main` and
  `release/*`/`support/*`, exactly like the original). `execute=false`
  is the default, a dry run — every step planned and reported, nothing
  mutates until `execute=true` is passed explicitly, matching the
  original tool's own dry-run-first design. `lws version stabilize
  --all` loops the Job over Registry & Portfolio's own project list, the
  same pattern `branch_audit` already established. This is the first Job
  on this pillar that pushes to a remote at all — every other Job here
  deals in local git state only, leaving publishing to whichever caller
  owns that concern elsewhere (Agentic Development's own `_publish`); a
  narrow, deliberate exception scoped to this one Job, not a general new
  capability.
- **Release job** (`version.release`, 2026-09-24; `src/version/release_job.py`):
  the Dashboard's "Release" catalog job — releases the latest committed
  integration branch in one Job. Conditions are verified before any branch is
  created, with the checkout on the integration branch (a clean checkout is
  required; one on another branch is switched back to it afterwards):
  - the release must merge into production without conflicts (a dry-run
    `git merge-tree`), always;
  - **minimum delta** (lines added + removed since the last production tag)
    and **minimum patch bumps** (patch versions the integration branch moved
    on since the last release — the line a release starts is `.1`, so the
    count is its patch minus one, or minus production's own patch before any
    release), combined with **and** or **or**;
  - optionally **all tests pass** and **all requirements pass** — one run of
    every test suite at the integration commit serves both (failed/error
    tests fail it; skips don't); every *published* requirement must then be
    done on the Definition-of-Done report, drafts ignored — and **all
    policies pass**: every enabled policy associated with the project runs
    (`policy.check_project`), and a fail blocks unless gating is off for the
    project. Tests and policies only run once the cheap checks hold.
  Then `release start`, a `CHANGELOG.md` section on the release branch (the
  integration branch's first-parent subjects since the last tag, `chore`/
  back-merges left out), `release finish`. Last, the relaunch is queued as
  Jobs that depend on this one, so they start only once it has succeeded: a
  running app is relaunched on the branch it runs (`deployment.deploy`); for
  little-workshop itself (the project at `LITTLE_WORKSHOP_REPO_DIR`) every
  enabled pillar is redeployed at the integration branch's new tip, one after
  another, `deployment`/`orchestration` last (their own confirm gates apply).
  It answers `accepted` and runs on a background thread (tests and policy
  checks take minutes), reporting back by job id; every check's outcome is
  in the Job's result, refused or not. Gated like `release_finish`
  (`release`, `branch_finish`).
- **Release finish back-merges production** (2026-09-24): the integration
  branch now merges production (not the release branch) after the release,
  so it also carries the minor bump, finish-state and version-hook commits
  made on production after the release merge. Before, those stayed
  production-only and every next release conflicted on `VERSION`,
  `.lvx/state.json` and hook-written files.
- **Pre-commit hook only on working branches** (2026-09-24): the installed
  hook calls `lws version sync-version-files` only on `feature/*` and
  `improvement/*` (the prefixes are read at install time) — a sync queued
  behind a Release Job holding the project's queue would otherwise run
  later, on whatever branch is checked out by then.
- **Pre-commit sync runs in the CLI, not as a Job** (2026-09-24):
  `lws version sync-version-files` now syncs the checkout it is run in,
  synchronously, without the daemon (`--project-id` is still accepted).
  As a Job it queued behind the project's `queue_key` — held by the very
  `agent.run_task` making the commit — so it ran only after the run ended:
  the commits carried no bump and the leftover staged VERSION blocked the
  run's own finish ("Working tree is dirty"). REQ-000000264.
- **Finish resolves version-only rebase conflicts** (2026-09-24): every
  working branch's first commit bumps VERSION (and the files version_hooks
  write it into), so two branches cut from the same dev tip always
  conflicted there once the first had finished, and the second finish
  stopped for a person. The finish rebase now resolves a conflict hunk
  whose sides differ only by each commit's own VERSION string (taking
  upstream's; finish rewrites it anyway) and takes the replayed commit's
  `.lvx/state.json`; only those, and only inside conflict hunks — any other
  conflict still stops the finish (`gitops.resolve_version_only_conflicts`,
  the one exception to M3's never-auto-resolve). `docs/requirements.md` is
  `merge=union` in `.gitattributes`, since branches only append to it.
  REQ-000000265.
- **Bookkeeping commits cite `REQ-NONE`** (2026-09-24): version bumps,
  finish/release state, release candidates, changelogs and issue links end
  with a `REQ-NONE` paragraph (`gitops.no_requirement`), since a version hook
  can make them touch `src/` (little-home's `src/package.json`) and
  `commit-cites-requirement` judges every commit that does. Merge commits
  carry real changes and never cite it. `stabilize`'s "commit pending
  changes" does not either: what it commits is unknown. REQ-000000266.
- **A finish lands on the branch's own patch** (2026-09-24): a working
  branch's commits already carry the next patch (`X.Y.Z-dev.N`, per
  `branch-commit-dev-suffix`), and the finish then bumped the patch again,
  so every finish skipped one (0.2.6 → 0.2.8) and the Release job's
  "minimum patch bumps" counted each finish twice. The finish now lands on
  `X.Y.Z`; a branch whose VERSION has no -dev suffix still gets the next
  patch, and the result is always above what the integration branch
  carried before the merge (`workflows.finished_version`). REQ-000000269.
- **The pillar's own git calls skip the repository's hooks** (2026-09-24):
  once the pre-commit sync ran synchronously (REQ-000000264), it also ran
  on the finish's own squash commit, which sits on the integration tip
  after its `reset --soft` and so looked like a branch's first commit:
  it bumped to the next patch again (0.2.15 instead of 0.2.14). Every git
  call the pillar makes now runs with `core.hooksPath=/dev/null`
  (`gitops.run`); the hook stays for commits an agent or a person makes.
  REQ-000000270.

## Interfacing

- **Calls**: Registry & Portfolio — every job resolves its `project_id`
  into a real repository path via `common.registry_client`, discovered
  through Core, before touching git at all (`version/resolve.py`).
  Nothing else; git itself (real subprocess calls, `version/gitops.py`)
  is the only other thing this pillar talks to.
- **Called by**: Agentic Development, as a synchronous subroutine call
  (not routed through Orchestration a second time) — `new`-mode branch
  creation calls this pillar's own `version.<type>_start` Job directly
  over its Pillar Interface, and `agent.run_task` validates a requested
  `BranchingSpec` against this pillar's live `GET /v1/config` rather than
  reading `.lvx/config.json` a second, parallel way. Also called by
  `lws`, the MCP Server, and Orchestration (dispatching every
  `version.*` Job type), and, in the future, Policy & Compliance
  (checking whether a proposed action's branch type is even eligible for
  automation). Once Orchestration's scheduled tasks (`docs/milestones.md`
  M9) exist, a recurring `ScheduledTask` naming `version.release_start`/
  `version.release_finish` is how a periodic `dev`→`master` release gets
  built — nothing in this pillar itself needs to change for that.
- **Discovered through**: Core, like every pillar.
