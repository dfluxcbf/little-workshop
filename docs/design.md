# Design

## Purpose

`little-workshop` is the single, self-hosted system that governs the full
software lifecycle — requirements through deployment and monitoring — for
every project in this ecosystem, sized for one PC and up to two developers.
It replaces the current arrangement of separate tools (`little-requirements`,
`little-versions`, `little-runner`, `bootstrap-little-project`) and the
super-repo's own informal tooling (`configure_submodules.sh`,
`stabilize_branches.py`/`branch_audit.py`, the hand-maintained
`docs/milestones.md` ecosystem table) with one daemon that absorbs each of
their responsibilities as an internal module sharing one process, one job
model, one database, and one enforced policy set — rather than trading them
for a third-party platform.

No free, self-hosted project combines requirements management, test
management, gitflow automation, real CI, a package registry, agentic
development, server management, and portfolio management at this scale (see
the standalone FOSS survey this design follows: the closest single products —
Tuleap, GitLab CE — cover the ALM half and still leave agentic development,
server lifecycle management, and this ecosystem's specific safety
guarantees, unaddressed). Assembling that combination from five or six
separate third-party services would be a net increase in operational surface,
not a reduction — five things to run, patch, and reconcile instead of one.
`little-workshop` is smaller than that alternative because it inherits, rather
than reinvents, the exact mechanisms this ecosystem already proved out:
`little-runner`'s worktree-based zero-downtime deploys, confirm-gated
self-restart, and issue-to-agent pipeline; `little-versions`' precondition-
checked Gitflow orchestration; `little-requirements`' cascading
requirement/test-result model.

## Goals

- **Absorb, don't replace-with-peers.** `little-requirements`,
  `little-versions`, `little-runner`, `bootstrap-little-project`, and
  `little-projects`' informal registry/policy role become modules of one
  daemon (`lws serve`), not five programs that happen to be developed
  together. See `architecture.md`'s feature-mapping table for exactly which
  piece of each tool becomes which module.
- **One job model for every kind of work.** Requirement changes, version
  bumps, test runs, builds, package publishes, server deploys, and agentic
  development sessions are all `Job`s. Today these five responsibilities use
  five different ad hoc execution mechanisms — a pytest plugin, a git-
  mutating CLI, a GitHub poller with per-repo state files, a filesystem
  publish flow, and no mechanism at all for sequencing across tools. One job
  model means triggering, queuing, monitoring, retrying, and reviewing the
  result of *anything* the system does works the same way, once.
- **Pipelines as first-class, inspectable objects.** A `Pipeline` connects
  `Job`s (a requirement check, a test run, an agentic implementation step, a
  build, a deploy) into a DAG that can span one project or several. This
  replaces the hand-run, hand-sequenced "ecosystem milestone" pattern in
  today's `docs/milestones.md` ("one feature branch per affected submodule
  plus a matching branch in this repo" — currently tracked by a human
  re-reading a table) with something the system runs and reports on itself.
- **Documentation policy as executable rules, not prose someone has to
  remember to re-check.** The eight root policy documents this ecosystem
  already lives by (structure, documentation, version, requirements, build,
  MCP, automation, branching) become a `PolicyRule` set evaluated at defined
  checkpoints. The markdown stays as the human-readable rationale *behind*
  each rule; it stops being the enforcement mechanism, because today nothing
  actually stops a project from drifting except a human (or Claude, following
  the `new-project-checklist` skill) re-reading five documents by hand.
- **Claude CLI as a first-class execution target for agentic development** —
  generalized from `little-runner`'s GitHub-issue-only trigger to any job
  source (a ticket, a pipeline node, a scheduled policy failure), while
  keeping every safety property that mechanism already earned in production:
  the agent never touches branches when a job specifies them, a run is never
  auto-finished without an explicit request, and every run is watchable and
  steerable live via Remote Control.
- **Bootstrapping and the project registry as one operation and one table.**
  `bootstrap-little-project`'s own design document lists "register the
  submodule" and "wire the requirements database" as *deliberate* non-goals,
  because a single new project's scope can't reach the shared files those
  touch (`.gitmodules`, `bundle-littles/BUILD.bazel`). Once there is one
  shared registry instead of N repo-local configs plus a hand-copied Bazel
  block, that seam closes by construction.
- **A real, if intentionally small, CI and test-sandboxing story.** Every
  current tool explicitly disclaims this: `little-runner`'s design.md states
  "not a general CI system — no test matrix, no artifact publishing, tests
  still run locally / via Bazel"; `little-requirements`' design.md states
  "pytest is currently the only supported automatic reporting mechanism."
  `little-workshop` runs tests in an isolated worktree (the exact mechanism
  `little-runner` already built for zero-downtime server deploys, applied to
  test execution instead) and ingests results via JUnit-XML beyond pytest —
  both of these are `little-requirements`' *own* already-designed, never-built
  M8/E6 milestones, now actually implemented.
- **Self-restart with a newer version of itself** — inheriting, not
  reinventing, `little-runner`'s already-proven confirm-gated, cgroup-aware,
  worktree-based deploy mechanism (its M14/M16/M17/M18 milestones), applied
  reflexively to `little-workshop`'s own daemon unit. This matters more here
  than it did for `little-runner`, because `little-workshop` is now the *only*
  thing standing between "everything" and "nothing runs."
- **Stay small.** Single SQLite database, single daemon process, in-process
  job workers, no message broker, no container orchestration, no
  multi-tenant auth — sized for one PC and up to two people, deliberately,
  not for hypothetical growth. See `architecture.md`'s "Technical decisions"
  for each rejection and why.

## Non-goals

- **Not multi-tenant, not a hosted platform.** There is no user/role/
  permission model beyond "is this one of the (at most two) people configured
  to use this" — the same actor-allowlist model `little-runner` already uses,
  now the whole system's access-control story, not extended with roles or
  scopes.
- **Not a general-purpose ALM product** competing with Tuleap/OpenProject/
  GitLab CE. It exists specifically because none of those combine this
  ecosystem's exact capability set as one free, self-hosted system at this
  scale — not because they're deficient at what they *do* cover.
- **Not a distributed system.** One host, one daemon, one database. Scaling
  beyond a single PC is out of scope, and nothing in this design is shaped to
  make that transition easier later — that would be paying a real design cost
  now for a need that doesn't exist.
- **Not a replacement for Git, Bazel, `gh`, or Claude Code.** Exactly as
  `little-versions`/`little-runner` do today, `little-workshop` orchestrates
  these tools; it does not reimplement version control, builds, GitHub's API,
  or the coding agent itself.
- **Not an implementation, yet.** This document and `architecture.md` are the
  settled design for this round. No `src/` exists beyond the standard
  scaffold, and no milestone is defined — implementation planning is a
  deliberately separate, later round.

## Core concepts

- **Module** — a code boundary (a Python subpackage) inside the one
  `little-workshop` process, not a separate deployable. Requirements,
  versioning, testing, build/packaging, deployment, agentic development,
  project registry/bootstrap, tickets/portfolio, policy, job engine, and
  pipeline engine are each one module. See `architecture.md`'s Components
  table.
- **Job** — the unit of work every module produces: `{id, type, project_id,
  params, status, queue_key, depends_on, created_by, result, log_ref}`. Every
  operation any absorbed tool performs today — `requirement.create`,
  `version.feature_finish`, `test.run_suite`, `build.run`, `package.publish`,
  `server.deploy`, `agent.run_task`, `policy.check`, `project.bootstrap`, and
  so on — is a Job type. A worker pool executes ready jobs (dependencies
  satisfied) respecting a per-`queue_key` mutex — a direct generalization of
  `little-runner`'s existing "one issue at a time per repository" rule from
  *one specific operation* to *any* git-mutating job on a project.
- **Pipeline / PipelineRun** — a `Pipeline` is a named, versioned DAG of Job
  templates with parameter binding and conditional edges (on success/
  failure/always); a `PipelineRun` expands that template into concrete Jobs at
  trigger time, wiring their `depends_on` edges. A pipeline node's
  `project_id` can differ from the pipeline's own, which is what makes a
  pipeline spanning several submodules possible — the DAG *is* Job dependency
  edges; nothing new is needed at the execution layer to support it.
- **PolicyRule** — a predicate plus a message, grouped by policy area
  (structure/documentation/version/requirements/build/mcp/automation/
  branching), evaluated at a defined checkpoint (pre-job, pre-merge/pre-
  release as a pipeline gate node, or on a schedule). Seeded from this
  ecosystem's eight existing root policy documents; the markdown remains the
  authoritative *rationale* for each rule, not the check itself. A small
  number of existing rules (automation never merges/deletes/finishes on its
  own initiative; the agent never touches branches when a job specifies them)
  are hard-coded invariants in the agentic-development and job-engine modules
  rather than user-editable `PolicyRule`s, because they are safety properties
  this ecosystem depends on, not conventions someone might reasonably want to
  loosen.
- **Managed Process** — the one abstraction behind both "a project's server"
  and "a Claude CLI agent instance": a unit name, a start command, a health
  probe, and a log reference, started/stopped/restarted through one code
  path regardless of which kind of process it is. `little-workshop`'s own
  daemon is a Managed Process too, which is what makes self-restart-with-a-
  newer-version a instance of the general mechanism rather than a special
  case.
- **Project registry entry** — one row per submodule (name, repo path,
  GitHub remote if any, current version mirrored from the git-committed
  `VERSION` file, policy-compliance summary, active jobs) plus one row for
  the super-repo container itself (flagged `is_container: true`, since the
  branching policy already audits it like a project despite it having no
  `VERSION`/`.lvx/config.json` of its own). This is the system of record
  `docs/README.md` and `docs/milestones.md` perform by hand today.
- **Ticket** — a title/description/status record, optionally scoped to a
  project or ecosystem-wide, optionally linked to a milestone or requirement
  IDs, and optionally mirrored to a GitHub issue when a project has a GitHub
  remote. This is the concern `little-tickets` was scaffolded for but never
  defined a purpose for — see `architecture.md`'s feature mapping.
- **Adapter** — an optional, per-project attachment point for an external
  system: GitHub (issue/PR sync, `gh`-backed, unchanged mechanism from
  `little-runner`), Tailscale (`tailscale serve`, unchanged mechanism), or
  Claude CLI (the agentic-development execution target). "Optional" matters
  concretely: a project with no GitHub remote (`little-mapgen`, today
  explicitly exempted from `little-runner`'s automation) simply never
  attaches the GitHub adapter — everything else still works for it.

## Data flow

### Job lifecycle (the shape every module's work takes)

```
caller (CLI / MCP tool / dashboard / scheduler / pipeline expansion)
   │
   ▼
Job created  {type, project_id, params, queue_key, depends_on}
   │
   ▼
Job Engine: ready? (depends_on satisfied, queue_key free)
   │  no  → stays queued
   ▼  yes
pre-job PolicyRule check for this Job's project/type
   │  fails (required rule)   →  Job = failed, reason = policy violation
   ▼  passes
dispatch to a worker (pooled) or a detached process (execution_mode=detached,
   e.g. an agentic-dev session someone is watching live)
   │
   ▼
module-specific execution (the same code that used to be lreq/lvx/lrun/
   little-pypi/bootstrap-little-project's own CLI logic, now a method call
   inside one process instead of a subprocess to a sibling tool)
   │
   ▼
Job = succeeded | failed, result recorded, queue_key released
   │
   ▼
Job Engine: does releasing this queue_key make another queued Job ready?
   │  yes → dispatch it (the queue "pumps itself", same mechanism
   │        little-runner's repo_state.py already uses for one repository's
   │        issue queue, generalized to every queue_key)
   ▼  no
(nothing further; caller/dashboard/MCP reads the Job's final state)
```

### Pipeline expansion (the "ecosystem milestone" pattern, run by the system)

```
Pipeline definition (YAML): nodes = Job templates + project_id + params,
                             edges = depends_on / on_success / on_failure
   │  triggered (manually, on schedule, or by another Job's completion)
   ▼
PipelineRun created
   │
   ▼
for each node: instantiate a concrete Job, project_id from the node
   (may differ per node — this is what makes a pipeline cross-project),
   wire depends_on from the node's edges
   ▼
Job Engine executes the expanded Jobs exactly as in the lifecycle above —
   no separate pipeline-execution code path; the DAG is Job dependency edges
   ▼
PipelineRun status = derived from its Jobs' statuses (succeeded only if every
   required node succeeded; a failed node's on_failure edges may still run,
   e.g. a cleanup or notification step)
```

### Agentic development job (Claude CLI as execution target)

```
Job{type=agent.run_task, project_id, params={task, branching_spec}}
   │
   ▼
resolve workspace per branching_spec (new | existing | auto) — identical
   model to little-runner's BranchingSpec today
   │
   ▼
launch `claude -p --remote-control <name> --output-format json
   --permission-mode bypassPermissions --add-dir <root>` as a detached
   process, named for Remote Control (user can watch/steer live)
   │
   ├─ HARD RULE (not a PolicyRule, not overridable): if branching_spec is
   │  new/existing, the agent is forbidden from running lvx/git branch/
   │  git checkout/git merge — the branch is already checked out before the
   │  agent starts, exactly as little-runner enforces today
   ▼
agent writes a result file outside the workspace (status +
   background/goal/technical_decisions/executed_tasks/test_results)
   │
   ▼
change report rendered → posted wherever the task originated (a ticket, a
   mirrored GitHub issue, the dashboard) and used as the pull-request body
   │
   ├─ finish requested (explicit: "finish when done" + status=done, or a
   │  later /finish) → version.feature_finish Job (never automatic otherwise)
   ▼
Job = succeeded, awaiting finish, or failed — same three outcomes
   little-runner's issue flow reports today
```

### Self-restart with a newer version of itself

```
Job{type=server.deploy, project_id=little-workshop, branch=B}
   │
   ▼
is the calling process running inside the target unit's own cgroup?
   (little-runner's existing ServerFlow._inside_unit_cgroup check,
   generalized to every Managed Process, not special-cased to one project)
   │  yes → hand off to a detached dispatch job in a brand-new systemd unit
   │        (systemd-run --user --unit=..., survives the target dying
   │        underneath it) instead of running inline
   ▼
git worktree add --detach <commitish>       -- old daemon still serving
   install(cwd=worktree)                    -- build-before-stop
   confirm_timeout_seconds set (it is, for little-workshop's own manifest,
     same as little-runner's today) → mark deploy pending, arm a systemd
     --user timer watchdog
   down(old daemon unit) → launch(new daemon, cwd=worktree)
   git worktree remove --force
   │
   ├─ confirmed within the window (dashboard / CLI)  → settle
   └─ window elapses first → rollback to the last confirmed commit via the
      same build-before-stop sequence, targeting an exact commit SHA
```
