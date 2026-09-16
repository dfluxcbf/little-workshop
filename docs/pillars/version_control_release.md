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
