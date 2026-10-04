# Design

## Purpose

`little-workshop` is the single, self-hosted system that governs the full
software lifecycle — requirements through deployment and monitoring — for
every project in this ecosystem, sized for one PC and up to two developers.
It replaces the current arrangement of separate tools (`little-requirements`,
`little-versions`, `little-runner`, `bootstrap-little-project`) and the
super-repo's own informal tooling (`configure_submodules.sh`,
`stabilize_branches.py`/`branch_audit.py`, the hand-maintained
`docs/milestones.md` ecosystem table) with one system that absorbs each of
their responsibilities — rather than trading them for a third-party platform.

No free, self-hosted project combines requirements management, test
management, gitflow automation, real CI, a package registry, agentic
development, server management, and portfolio management at this scale (see
the standalone FOSS survey this design follows: the closest single products —
Tuleap, GitLab CE — cover the ALM half and still leave agentic development,
server lifecycle management, and this ecosystem's specific safety
guarantees, unaddressed). Assembling that combination from five or six
separate third-party services would be a net increase in operational surface,
not a reduction. `little-workshop` is smaller than that alternative because
it inherits, rather than reinvents, the exact mechanisms this ecosystem
already proved out: `little-runner`'s worktree-based zero-downtime deploys,
confirm-gated self-restart, and issue-to-agent pipeline; `little-versions`'
precondition-checked Gitflow orchestration; `little-requirements`' cascading
requirement/test-result model.

## Revision note (this round)

The first design round described `little-workshop` as one process with
internal modules — a code boundary per concern, sharing one Python process
and one database. That model does not hold up against a second, harder set
of constraints added afterward: crash isolation, independent live updates,
per-feature enable/disable for a genuinely lightweight footprint, and
inter-feature boundaries that behave like real component interfaces, not
just tidy file layout. A single process cannot give you any of those —
a crash anywhere takes down everything, "updating one part without
interrupting others" is not a thing a single interpreter can do safely, and
"disable a part" doesn't save any memory or attack surface if its code is
still loaded into the one process that's already running.

This round replaces "modules in one process" with **pillars**: independent
processes, each with its own storage and its own declared interface,
supervised by a small, rarely-changing **Core**. Nothing about the *goals*
in the first round changes — the same capabilities are still being absorbed
from the same five tools — only how they're separated and run. Where this
document's Core Concepts and Data Flow sections below describe pillars, they
supersede the first round's "Module" concept.

## Goals

- **Absorb, don't replace-with-peers.** `little-requirements`,
  `little-versions`, `little-runner`, `bootstrap-little-project`, and
  `little-projects`' informal registry/policy role become pillars of one
  system, not five programs that happen to be developed together. See
  `architecture.md`'s feature-mapping table for exactly which piece of each
  tool becomes which pillar.
- **One job model for every kind of work**, coordinated by a dedicated
  Orchestration pillar rather than reimplemented per feature. Requirement
  changes, version bumps, test runs, builds, package publishes, server
  deploys, and agentic development sessions are all `Job`s dispatched to
  whichever pillar owns that capability.
- **Pipelines as first-class, inspectable objects**, connecting Jobs across
  one pillar or several into a DAG — replacing the hand-run, hand-sequenced
  "ecosystem milestone" pattern in today's `docs/milestones.md`.
- **Documentation policy as executable rules**, owned by a dedicated Policy
  & Compliance pillar that evaluates the eight root policy documents'
  checkable rules at defined checkpoints, gathering facts from other
  pillars through their own declared interfaces rather than reaching into
  their storage.
- **Real separation between features**, so that a change to one pillar
  cannot silently break another, a crash in one pillar cannot take down the
  rest, a pillar nobody is using can be switched off and cost nothing while
  off, and a pillar can be rebuilt and relaunched with a newer version while
  every other pillar keeps working, uninterrupted, on whatever it was doing.
- **Claude CLI as a first-class execution target for agentic development** —
  generalized from `little-runner`'s GitHub-issue-only trigger to any Job
  source, while keeping every safety property that mechanism already earned
  in production: the agent never touches branches when a Job specifies them,
  a run is never auto-finished without an explicit request, and every run is
  watchable and steerable live via Remote Control.
- **Bootstrapping and the project registry as one pillar's job**, closing
  the exact seam `bootstrap-little-project`'s own design document calls out
  as deliberately left to manual follow-up, because a single new project's
  scope couldn't reach the shared files those touch.
- **A real, if intentionally small, CI and test-sandboxing story** — every
  current tool explicitly disclaims this today. `little-workshop` runs tests
  in an isolated worktree and ingests results via JUnit-XML beyond pytest —
  both `little-requirements`' own already-designed, never-built M8/E6
  milestones, now actually implemented.
- **Two distinct, appropriately-scoped recovery mechanisms**: a crash gets
  the fastest possible relaunch of the last known-good build (Core's job);
  a planned upgrade gets a build-before-stop, confirm-gated redeploy that
  never touches any other pillar's running process (the Deployment & Servers
  pillar's job) — inheriting, not reinventing, `little-runner`'s already-
  proven mechanism for exactly this.
- **Stay small.** Every pillar's own storage is one small SQLite file; there
  is no message broker, no container orchestration, no multi-tenant auth,
  and a pillar nobody enabled starts no process and uses no resources at
  all — sized for one PC and up to two people, deliberately, not for
  hypothetical growth.

## Non-goals

- **Not multi-tenant, not a hosted platform.** There is no user/role/
  permission model beyond "is this one of the (at most two) people configured
  to use this" — the same actor-allowlist model `little-runner` already uses,
  now the whole system's access-control story.
- **Not a general-purpose ALM product** competing with Tuleap/OpenProject/
  GitLab CE. It exists specifically because none of those combine this
  ecosystem's exact capability set as one free, self-hosted system at this
  scale.
- **Not a distributed system**, despite being multiple processes. Every
  pillar runs on the same PC, communicating over local Unix-domain sockets —
  "independent component" describes the boundary discipline, not a claim
  that this scales past one host. Nothing here is shaped to make that
  transition easier later.
- **Not a replacement for Git, Bazel, `gh`, or Claude Code.** Exactly as
  `little-versions`/`little-runner` do today, `little-workshop` orchestrates
  these tools; it does not reimplement version control, builds, GitHub's
  API, or the coding agent itself.
- **Not an implementation, yet.** This document and `architecture.md` are
  the settled design for this round. No `src/` exists beyond the standard
  scaffold, and no milestone is defined — implementation planning is a
  deliberately separate, later round.

## Core concepts

- **Pillar** — an independently deployable unit of the system, focused on
  one stated goal, owning its own storage exclusively, and exposing exactly
  one way in: its declared **Pillar Interface**. A pillar is one OS process
  (a `systemd --user` unit, the same primitive `little-runner` already uses
  for every project server) that can be started, stopped, crash-relaunched,
  upgraded, and — critically — **disabled entirely**, without any other
  pillar's code or process being touched. There are eleven: Orchestration,
  Policy & Compliance, Registry & Portfolio, Requirements & Quality, Version
  Control & Release, Build & Packages, Deployment & Servers, Agentic
  Development, Dashboard, MCP Server, and Usage & Efficiency (added 2026-10-04:
  it records and interprets what every Claude session on this machine uses, see
  `pillars/usage.md`). See `architecture.md`'s Components
  table for each one's goal, what it absorbs, and its interface summary.
- **Core** — the one thing that is *not* a pillar and cannot be disabled: a
  small, rarely-changing process-supervision layer that starts every enabled
  pillar, tracks whether each is alive, relaunches a crashed one immediately
  from its last known-good build (no rebuild, no confirm gate — a crash
  needs speed), and refuses to start a disabled one at all. Core holds no
  domain data and makes no domain decisions; the less it does, the less
  often it needs to change, and the less often it needs to change, the more
  reliably everything that depends on it stays up.
  **Core's own crash recovery is not self-supervision** — a crashed process
  cannot relaunch itself. It's the identical `systemd --user
  Restart=on-failure` primitive Core configures for every pillar, now
  configured on Core's own unit too; Core doesn't get a special, more
  fragile mechanism for itself. What *is* Core's own responsibility is what
  happens next: because every pillar is started via `systemctl --user start
  <pillar>`, each becomes a child of the systemd user session, never of
  Core — so Core dying, and later restarting, never touches them. On
  restart, Core does not assume it needs to (re)start everything; it
  reconstructs its live registry by asking systemd which pillar units are
  already active and probing each one's own health endpoint, and only
  starts what's actually missing. Reconnecting to what's already running
  is the default outcome of a Core restart; relaunching is only what
  happens to whatever genuinely isn't there.
- **Pillar configuration** — every pillar's own settings live at
  `~/.config/little-workshop/<pillar>/`, exclusively that pillar's, never
  touched by another. This is distinct from a pillar's own database (its
  *operational* data — jobs, rule definitions, requirement records) under
  `~/.local/state/little-workshop/pillars/<pillar>.db`; configuration and
  operational data are different things kept in different places. Core is
  not a pillar, so it has no `<pillar>/` subdirectory of its own — its
  bootstrap manifest (the pillar list, enabled flags, crash-backoff
  settings) lives directly at `~/.config/little-workshop/manifest.toml`,
  beside the ten pillar directories rather than inside one.
  The manifest's `[core] workshop_repo_dir` names this project's own git
  checkout; Core hands it to every pillar it launches as
  `LITTLE_WORKSHOP_REPO_DIR` (and Deployment carries it on to pillars it
  redeploys), because a pipx-installed pillar cannot find the checkout by
  itself and pillar redeploys build from it.
- **Project files** — the one place pillars actually share a directory:
  `~/.config/little-workshop/projects/<project>/`, one such directory per
  project this system manages. Unlike pillar configuration, this directory
  is not exclusively any one pillar's, so ownership is encoded in the
  filename itself: `<name>.<owner-tag>.<format>` (e.g.
  `retention.policy.json`, `manifest.deployment.toml`) — the second-to-last
  extension names the one pillar allowed to *write* that file; the last
  extension is just its serialization format, free per file. Any pillar may
  *read* any project file regardless of owner tag; only the owning pillar
  may *write* it. A pillar may own as many distinctly-named project files as
  it needs. See `architecture.md`'s "Configuration & project data" for the
  full tag table and how this is actually enforced.
- **Pillar Interface (PI)** — the *only* way one pillar reaches another: a
  small versioned HTTP API bound to a Unix domain socket (not a shared
  Python import, not a database file another pillar also opens). A pillar
  publishes its interface's schema; every caller — another pillar, the
  Dashboard, the MCP server — depends on that schema, never on the serving
  pillar's internals. Versioned paths (`/v1/...`, `/v2/...`) let a pillar
  run two interface versions at once during a migration window, so
  upgrading one pillar's interface never forces every caller to upgrade in
  lockstep.
- **Job** — the unit of work Orchestration coordinates: `{id, type,
  target_pillar, project_id, params, status, queue_key, depends_on,
  created_by, result}`. `status` is one of `queued | running |
  pending_confirmation | succeeded | failed | cancelled` —
  `pending_confirmation` is a gated Job waiting on a human response to a
  failing policy check before it's allowed to proceed at all; see
  `docs/pillars/policy_compliance.md`'s "Action-gating checks." Every
  operation any absorbed tool performs today —
  `requirement.create`, `version.feature_finish`, `test.run_suite`,
  `build.run`, `package.publish`, `server.deploy`, `agent.run_task`,
  `policy.check`, `project.bootstrap` — is a Job type, dispatched over that
  type's target pillar's own Pillar Interface. A long-running Job (an
  agentic-dev session someone is watching) doesn't block Orchestration
  waiting for a synchronous reply: the target pillar accepts the Job and
  reports completion back through its own callback, the same "queue pumps
  itself" shape `little-runner`'s repository-slot logic already uses,
  now crossing a process boundary instead of staying in-process.
- **ScheduledTask** (`docs/milestones.md` M9) — a durable definition,
  owned by Orchestration, distinct from a Job: `{id, name, job_template,
  schedule, state, next_run_at, missed_run_at}`. `job_template` is a
  Job's own creation payload (`type`, `target_pillar`, `project_id`,
  `params`, `queue_key`, `depends_on`); `schedule` is `once` (a specific
  date/time) or `recurring` (a set of weekdays plus one time-of-day, in
  the host's own local time — not stored per-task, since this system
  runs on one household's machine) — deliberately not full cron. A
  single in-process background thread inside Orchestration wakes every
  10 minutes and dispatches anything due, creating a real Job from
  `job_template` with `created_by: "scheduler"` — no per-task `systemd`
  timer. Eight operations: `add`/`edit`/`remove`/`trigger`/`cancel`/
  `stop`/`postpone`/`queue` — see `docs/pillars/orchestration.md` for
  what each means precisely. `stop` is declared per Job type by the
  owning pillar (the fast path — most Job types, a fast synchronous git
  sequence say, have no safe mid-flight interruption point and are
  correctly declared unstoppable), falling back to actually attempting
  the call and trusting the target pillar's own answer for anything
  left undeclared. `queue` generalizes `queue_key` from a strict mutex
  into a configurable-capacity pool (default 1, so every existing
  caller's behavior is unchanged) rather than adding a second
  concurrency mechanism alongside it.

  **Missed firings**: since little-workshop is locally hosted and can
  legitimately be down, a `next_run_at` still in the past is only
  possible at Orchestration's own *startup* (a live tick always catches
  a due task within 10 minutes) — checked once there, never on the
  routine tick. A `recurring` task that missed several occurrences during
  a longer outage collapses to just the most recent one: `next_run_at`
  advances to the correct next future occurrence, and that single most
  recent miss is recorded as `missed_run_at`, a pending decision rather
  than an automatic action either way. `trigger` resolves it by firing
  the catch-up Job and clearing the field; `cancel` dismisses it without
  firing; the task's own next *regular* occurrence firing for real also
  clears it, unasked — a stale question is moot once this cycle's run
  has already happened. A `once` task has no next occurrence to
  supersede it this way, so its `missed_run_at`, if ever set, stays
  pending until a human actually answers it.
- **Pipeline / PipelineRun** — a named, versioned DAG of Job templates,
  expanded into concrete Jobs at trigger time. A node's target pillar and
  `project_id` can differ from other nodes in the same pipeline, which is
  what makes a pipeline spanning several submodules — or several pillars —
  possible.
- **Policy** (design placeholder `PolicyRule`, built as `Policy` —
  `docs/pillars/policy_compliance.md` is authoritative) — an EARS-form
  requirement plus a real, sandboxed pytest script, owned by the Policy &
  Compliance pillar, checked by asking the relevant pillars' own
  interfaces for the facts it needs (never by reading another pillar's
  storage directly). Seeded from this ecosystem's eight existing root
  policy documents, which remain the human-readable rationale, not the
  enforcement mechanism. Carries `enabled` (a toggle, no re-authoring)
  and `gate_points` (which of six action categories it gates on
  failure, user-chosen) — refined 2026-09-16, and again 2026-09-21: the
  user **selects which projects a policy applies to** and switches
  gating on or off per project (a check with gating off still runs and
  is recorded for analysis, it just cannot block) — which replaced the
  per-project waiver. A small number of existing rules —
  automation never merges/deletes/finishes on its own initiative; the
  agent never touches branches when a Job specifies them — are
  hard-coded invariants inside the Orchestration and Agentic Development
  pillars themselves, not user-editable `Policy` rows at all, because
  they're safety properties this ecosystem depends on, not conventions
  someone might reasonably want to loosen or waive.
- **Managed Process** — the abstraction the Deployment & Servers pillar uses
  for anything long-running it's responsible for: a unit name, a start
  command, a health probe, a log reference. A project's server, a Claude CLI
  agent instance, and — for planned (non-crash) upgrades — every other
  pillar of `little-workshop` itself are all Managed Processes, started/
  stopped/restarted/redeployed through one code path.
- **Project registry entry**, **Ticket**, **Adapter** — unchanged from the
  first design round in meaning; they now live inside the Registry &
  Portfolio pillar (registry entries, tickets) and are used by whichever
  pillar needs an external system (GitHub, Tailscale) rather than being
  their own pillar, since neither is a feature with its own goal — they're
  integration glue a feature pillar reaches for.

## Data flow

### Cross-pillar job dispatch (the shape every pillar's work takes)

```
caller (CLI / MCP tool / Dashboard / scheduler / pipeline expansion)
   │
   ▼
Job created in Orchestration  {type, target_pillar, project_id, params,
                                queue_key, depends_on, created_by}
   │
   ▼
Orchestration: ready? (depends_on satisfied, queue_key free)
   │  no  → stays queued
   ▼  yes
Policy & Compliance: does this Job's type declare a gate-point category
   (build-install, server-launch, deploy/redeploy, release, branch-finish,
   project-removal)? Most don't, and skip straight to dispatch. If it
   does, every currently-winning, *enabled*, *non-waived* policy gating
   that category — a Job can now be gated by more than one at once —
   gets checked (Policy asks the target pillar's PI for whatever facts
   each check needs; it never opens that pillar's database)
   │  no matching category, or every governing policy passes/is not applicable
   ▼  → dispatch proceeds
   │  fails  →  Job = pending_confirmation; user warned immediately;
   │            confirmed within the window → dispatch proceeds anyway;
   │            window elapses → Job = failed, action never happens.
   │            See `docs/pillars/policy_compliance.md`'s "Action-gating
   │            checks" for the full flow — this replaces this diagram's
   │            earlier, simpler "fails → Job = failed" outcome.
   ▼
Orchestration calls target_pillar's Pillar Interface (POST /v1/jobs/<type>)
   over that pillar's Unix domain socket
   │
   ├─ target pillar down/crashed  →  Job = blocked, retried once Core
   │  reports the pillar healthy again — no other pillar is affected
   ├─ target pillar disabled      →  Job = failed, reason = pillar disabled
   │  (a clear, immediate answer — never a hang waiting for something
   │  that was never going to start)
   ▼  target pillar healthy
pillar executes (synchronously for a quick op, or accepts the Job and
   reports completion later via its own callback, for a long one)
   │
   ▼
Job = succeeded | failed, recorded in Orchestration; releasing this
   queue_key may make another queued Job ready (the queue pumps itself)
```

`scheduler` above is no longer just a placeholder caller name:
`docs/milestones.md`'s M9 gives Orchestration a `ScheduledTask` concept
(`add`/`edit`/`remove`/`trigger`/`cancel`/`stop`/`postpone`/`queue`, over
a `once` or weekday-`recurring` schedule) that creates exactly this
shape of Job, on its own timing, with `created_by: "scheduler"` — see
`docs/pillars/orchestration.md`.

### Crash recovery (Core's job — fast, dumb, no rebuild)

```
systemd --user detects pillar-X.service exited unexpectedly
   │
   ▼
Restart=on-failure (systemd's own first line of defense — free, per unit,
   exactly how every project server already runs today)
   │
   ▼
Core: increment pillar-X's crash counter; if under the backoff threshold,
   confirm the relaunch happened and mark pillar-X healthy again once its
   health probe responds
   │
   ▼
every other pillar: unaffected. A Job that was mid-flight to pillar-X
   is the only thing that noticed anything — it now retries, once pillar-X
   reports healthy, per the dispatch diagram above.
```

### Planned upgrade (Deployment & Servers pillar's job — smart, confirm-gated)

```
Job{type=deployment.redeploy_pillar, target_pillar=X, commit=C}
   │
   ▼
git worktree add --detach C            -- pillar X's OLD process keeps
   install(cwd=worktree)                  serving every other pillar's
                                           calls throughout this step
   │
   ▼
confirm_timeout_seconds set (default for Core and Deployment & Servers
   themselves; opt-in for the rest) → mark deploy pending, arm a systemd
   --user timer watchdog
   │
   ▼
stop pillar-X's old unit → launch pillar-X's new unit from the worktree
   │
   ├─ confirmed within the window (Dashboard / CLI)      → settle
   └─ window elapses first → rollback to the last confirmed commit via the
      same build-before-stop sequence, targeting an exact commit SHA
   │
   ▼
git worktree remove --force
```

No other pillar's process is stopped, restarted, or even paused by any step
above — that is the concrete meaning of "update one part without
interrupting others." Redeploying Core or the Deployment & Servers pillar
itself additionally uses the cgroup-aware dispatch trick `little-runner`
already proved (a detached sibling `systemd-run --user` unit, so the
in-flight redeploy job survives the very unit it's replacing dying
underneath it) — the same "am I inside the unit I'm about to stop" check
`little-runner`'s own `ServerFlow._inside_unit_cgroup` performs today,
generalized from one project to every pillar.

### Core's own recovery — reconnect, don't relaunch

```
little-workshop-core.service exits unexpectedly
   │
   ▼
systemd --user Restart=on-failure  -- the same primitive protecting every
   pillar, just configured on Core's own unit too. Every pillar keeps
   running throughout: each is systemd's child, not Core's, so nothing
   about Core dying or restarting ever reaches them.
   │
   ▼
Core (new process) reads manifest.toml  -- the pillar list and enabled
   flags, unaffected by the crash because it's a file, not memory
   │
   ▼
for each ENABLED pillar:
   systemctl --user is-active <pillar>.service ?
      │ yes → probe the pillar's own health endpoint over its socket
      │         │ healthy   → register it in the live registry as-is
      │         │             (RECONNECTED — nothing about it changes)
      │         └ no reply  → treat as crashed, relaunch it (fast path,
      │                       no rebuild, same as any pillar crash)
      └ no  → start it fresh (systemctl --user start <pillar>.service)
for each DISABLED pillar: confirm its unit is not running; do nothing else
   │
   ▼
Core is caught up. A Core crash-and-restart, and a first boot, run the
   exact same sequence -- the only difference is how many pillars were
   already found running when it got there.
```

This is what "self-recoverable, without losing all the other pillars"
actually means in practice: Core's recovery is ordinary OS-level process
supervision, applied to itself with no special case; the part worth
designing was making sure a restarted Core rejoins the system it left
rather than resetting it.

**Health probes are a real report, not a bare ping** (resolved
2026-09-15): each pillar is responsible for its own internal health
analysis — checking its own subsystems, not just "am I still a process" —
and returns the full result when Core probes it, over the same live
connection every other pillar-to-pillar call uses. Core waits up to
**5 seconds** per probe, retrying up to **10 times** before treating a
nominally-running pillar as unhealthy.

**Crash-loop bounds** (resolved 2026-09-15): Core relaunches a crashed
pillar immediately, up to **10 consecutive tries, 1 minute apart**. If
the 10th attempt still fails, Core stops trying automatically and marks
that pillar **faulted** — a state distinct from disabled (chosen) or
merely crashed (recovering) — and surfaces this to the user through the
same channel a failing policy check uses (see
`docs/pillars/policy_compliance.md`'s "Informing the user immediately"),
since a faulted pillar is exactly the kind of thing nothing should
quietly stop trying to fix without someone finding out.

### Cross-pillar configuration change

```
Pillar B needs a setting that lives in a project file owned by Pillar A
   (e.g. Deployment & Servers needs Policy & Compliance to widen a
   confirm-timeout exception for one project)
   │
   ▼
Pillar B does NOT write project/<name>/setting.policy.json itself -- its
   own client library refuses any write whose owner tag isn't its own
   │
   ▼
Pillar B calls Pillar A's Pillar Interface (its normal, versioned API --
   configuration files are never how pillars talk to each other; PIs are)
   │
   ▼
Pillar A validates the request against its own rules, decides, and -- if
   it agrees -- performs the write to its own owned file itself
   │
   ▼
Pillar B (and everyone else) reads the updated file directly, as always --
   read access to any project file is universal regardless of owner tag
```

A pillar's project files may be *read* by anything; changing one is always
a request to its owner through that owner's interface, never a direct
write by the requester — the same "one writer, many readers" discipline
this ecosystem already applies to `little-requirements`' database, now
generalized to every pillar's project-scoped files.

### Bringing a project under management: bootstrap vs. import

Registry & Portfolio is the main responsible pillar for a project's
identity and lifecycle — not everything about a project, which stays
deliberately distributed across every other pillar, but whether it's
registered at all. Two different paths reach the same outcome:

```
NEW project                              EXISTING project
   │                                          │
   ▼                                          ▼
project.bootstrap pipeline                a Claude CLI session (human-
   (deterministic: scaffold, git             interactive, or Agentic
   init, lvx init, dev branch --             Development's own internal
   the bootstrap-little-project              dispatch -- the same
   sequence, now one pipeline               mechanism either way) drives
   instead of a separate tool)              the import through Registry &
   │                                         Portfolio's MCP-exposed entry
   │                                         point: inspect the existing
   │                                         repository, bring it into
   │                                         conformance with framework
   │                                         policy, work that genuinely
   │                                         needs judgment a fixed
   │                                         pipeline can't supply
   │                                          │
   └──────────────────┬───────────────────────┘
                       ▼
   Registry & Portfolio registers the project: a `projects` row +
   a `~/.config/little-workshop/projects/<project>/` directory --
   the row is what "this project exists" means; the directory is a
   consequence of that, never a parallel source of truth
```

There is no separate "import mode" to build as its own mechanism: a
human's interactive Claude Code session and Agentic Development's own
`agent.run_task` both reach little-workshop's MCP server the identical
way — the same relationship this very ecosystem's `little-requirements`
and `little-versions` MCP tools already have with any Claude session that
attaches them. Import is that mechanism, pointed at a pre-existing
repository instead of a blank one.
