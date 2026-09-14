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
  pillar's code or process being touched. There are ten: Orchestration,
  Policy & Compliance, Registry & Portfolio, Requirements & Quality, Version
  Control & Release, Build & Packages, Deployment & Servers, Agentic
  Development, Dashboard, and MCP Server. See `architecture.md`'s Components
  table for each one's goal, what it absorbs, and its interface summary.
- **Core** — the one thing that is *not* a pillar and cannot be disabled: a
  small, rarely-changing process-supervision layer that starts every enabled
  pillar, tracks whether each is alive, relaunches a crashed one immediately
  from its last known-good build (no rebuild, no confirm gate — a crash
  needs speed), and refuses to start a disabled one at all. Core holds no
  domain data and makes no domain decisions; the less it does, the less
  often it needs to change, and the less often it needs to change, the more
  reliably everything that depends on it stays up.
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
  created_by, result}`. Every operation any absorbed tool performs today —
  `requirement.create`, `version.feature_finish`, `test.run_suite`,
  `build.run`, `package.publish`, `server.deploy`, `agent.run_task`,
  `policy.check`, `project.bootstrap` — is a Job type, dispatched over that
  type's target pillar's own Pillar Interface. A long-running Job (an
  agentic-dev session someone is watching) doesn't block Orchestration
  waiting for a synchronous reply: the target pillar accepts the Job and
  reports completion back through its own callback, the same "queue pumps
  itself" shape `little-runner`'s repository-slot logic already uses,
  now crossing a process boundary instead of staying in-process.
- **Pipeline / PipelineRun** — a named, versioned DAG of Job templates,
  expanded into concrete Jobs at trigger time. A node's target pillar and
  `project_id` can differ from other nodes in the same pipeline, which is
  what makes a pipeline spanning several submodules — or several pillars —
  possible.
- **PolicyRule** — a predicate plus a message, owned by the Policy &
  Compliance pillar, evaluated by asking the relevant pillars' own
  interfaces for the facts a rule needs (never by reading another pillar's
  storage directly). Seeded from this ecosystem's eight existing root
  policy documents, which remain the human-readable rationale, not the
  enforcement mechanism. A small number of existing rules — automation never
  merges/deletes/finishes on its own initiative; the agent never touches
  branches when a Job specifies them — are hard-coded invariants inside the
  Orchestration and Agentic Development pillars themselves, not user-editable
  `PolicyRule`s, because they're safety properties this ecosystem depends
  on, not conventions someone might reasonably want to loosen.
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
                                queue_key, depends_on}
   │
   ▼
Orchestration: ready? (depends_on satisfied, queue_key free)
   │  no  → stays queued
   ▼  yes
Policy & Compliance: pre-job check for this Job's type/project
   (Policy asks the target pillar's PI for whatever facts it needs — it
    never opens that pillar's database)
   │  fails (required rule)  →  Job = failed, reason = policy violation
   ▼  passes
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
