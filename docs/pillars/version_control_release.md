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
