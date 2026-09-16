# Milestones

Follows the super-repo's [documentation policy](../../docs/policies/documentation_policy.md)
milestone rules, adapted for this project's own kept-not-mandated version
and requirements conventions (`docs/version_policy.md`,
`docs/requirements_policy.md`): each milestone is worked on its own
short-lived branch (plain `git`, not `lvx`), and is only done once
documented, its requirements are created/updated in `docs/requirements.md`
(not an `lreq` database), implemented, covered by automated tests, and
those tests pass. Per that same policy, a milestone's own requirements are
drafted as its *first* real task, not pre-work owed to it — this document
states each milestone's objective and dependencies, not its requirements.

This is the full sequence to reach a working implementation of everything
`docs/design.md`/`docs/architecture.md`/`docs/pillars/*.md` currently
define — laid out once, in dependency order, rather than one milestone at
a time. Later milestones may still reshape earlier ones' fine detail (the
same "the docs are the record, corrected as understanding improves"
discipline this project already applies to itself); the *sequence* and
*scope* below is the actual plan.

## Overview

| ID | Objective | Depends on | Status |
|---|---|---|---|
| M1 | Core + Orchestration | — | Done |
| M2 | Registry & Portfolio — bootstrap path | M1 | Done |
| M3 | Version Control & Release | M1, M2 | Done |
| M4 | Deployment & Servers | M1, M2 | Done |
| M5 | Self-redeploy (reflexive) | M4 | Done |
| M6 | Agentic Development | M1, M2, M3, M4 | Done |
| M7 | MCP Server — minimal | M2, M3, M4, M6 | Done |
| M8 | Registry & Portfolio — import path | M2, M6 | Done |
| M9 | Orchestration — scheduled tasks | M1 | Done |
| M10 | Requirements & Quality | M1, M2, M4 | Done |
| M11 | Build & Packages | M1, M2, M4 | Done |
| M12 | Policy & Compliance | M1, M4, M6, M10 | Not started |
| M13 | Populate the policy catalog | M12 | Not started |
| M14 | Dashboard | M2–M13 (whatever exists by then) | Not started |
| M15 | Self-hosting / dogfooding | M2, M3, M10, M12 | Not started |
| M16 | Compatibility shims | M1–M14 (whichever tool each shim forwards to) | Not started |
| M17 | Ecosystem cutover | M15, M16 | Not started |
| M18 | Retire the absorbed tools | M17 | Not started |

Everything below M1 is new to this pass — laid out in one sitting so the
dependency graph is visible up front, not discovered one milestone late.
**M16–M18 were missing from the first pass entirely** — every milestone
through M15 makes little-workshop internally complete, but none of them
address the actual point of the project: the other real projects in this
ecosystem still depend on `lreq`/`lvx`/`lrun`/`little-pypi` until
something migrates them off. Caught on review, not found by re-reading
`docs/design.md`'s own Purpose section closely enough the first time.

### Cross-cutting, every pillar milestone (M2–M14)

Two things every one of M2 through M14 delivers, not called out
per-milestone below to avoid repeating the same line thirteen times:

- **Grows the `lws` CLI** with that pillar's own subcommands (see
  `docs/architecture.md`'s new CLI entry) — the same incremental-growth
  treatment already stated for MCP Server in M7, now explicit for the
  CLI too, and started earlier than M7 since `lws` exists from M1.
- **Writes that pillar's own `docs/pillars/<name>.md`**, the same
  treatment `docs/pillars/policy_compliance.md` and
  `agentic_development.md` already have — a milestone isn't documented,
  per this file's own inherited definition-of-done, if the survey-level
  treatment in `design.md`/`architecture.md` is all that exists once the
  pillar is real.

## M1 — Core + Orchestration

The substrate every pillar depends on, built and proven before any real
pillar exists.

- **Core**: `manifest.toml`, pillar registry, `systemd --user` lifecycle
  per enabled pillar, health probing (5s timeout, 10 retries, each
  pillar's own internal report — not a bare ping), crash-relaunch (10
  tries, 1 minute apart, then **faulted**), reconnect-not-relaunch on
  Core's own restart.
- **Orchestration**: the `Job` model (`id, type, target_pillar,
  project_id, params, status, queue_key, depends_on, created_by,
  result`), dispatch over a pillar's Pillar Interface, the `queue_key`
  mutex, the self-pumping queue, `depends_on` resolution. **Also**
  `Pipeline`/`PipelineRun` (`docs/design.md`'s Core Concepts) — a named,
  versioned DAG of Job templates expanded into concrete Jobs at trigger
  time, missing from this milestone's first draft even though it's
  Orchestration's own table (`docs/architecture.md`'s Data model already
  lists `pipelines`, `pipeline_runs`) and needs no new execution-layer
  primitive beyond what dependency-linked Jobs already provide.
- **A minimal `lws` CLI** (`docs/architecture.md`'s new CLI entry) — just
  enough to exercise Core and Orchestration directly (`lws core status`,
  `lws job create/list/status`), since nothing else capable of driving
  them (Dashboard, MCP) exists yet and won't for several more
  milestones. Grows with every pillar from here on; see "Cross-cutting"
  above.
- **The project-files shared access library** (`docs/architecture.md`'s
  Configuration & project data) — the thing that actually enforces
  filename-encoded write-ownership (`<name>.<owner-tag>.<format>`) for
  every pillar from M2 onward. Belongs here, not in whichever pillar
  first happens to use it, because every pillar from M2 on needs the
  same enforcement and none of them should each reimplement it.
- **Shared infrastructure that has to start somewhere**: the size-based
  log-retention convention (`docs/open_questions.md` `OQ-4`) belongs
  here, since Core is the first thing that writes a log — implement it
  once as something every later pillar reuses, not once per pillar.

Proven end-to-end against one minimal reference/stub pillar built for
this milestone only (a trivial health endpoint + one no-op Job type) —
not a real pillar's own implementation.

## M2 — Registry & Portfolio (bootstrap path)

The pillar responsible for a project's identity and lifecycle, started
with the half that needs no other pillar: `project.bootstrap`
(deterministic scaffold → `git init` → `dev` branch, absorbing
`bootstrap-little-project` wholesale), the `projects` table (the
authoritative registry — a project-files directory follows from
registering a row, never the reverse), `project_sets`, and `tickets`.

The import path (Claude-CLI-driven, for an *existing* project) is
deliberately deferred to **M8**, once Agentic Development exists to
drive it — building it now would mean either stubbing the exact
capability M6 delivers, or blocking this milestone on a pillar three
slots later. Cascading project removal (`OQ-7`) is implemented here for
this pillar's own data; every other pillar implements the same
"purge my data for this project_id" endpoint as part of *its own*
milestone, not retrofitted later.

## M3 — Version Control & Release

Gitflow orchestration as its own pillar: precondition-checked branch/
version transitions (dirty tree, wrong base, unresolved conflict — refuse,
never auto-resolve), the squash-then-merge sequence, release-candidate
cut/list/promote, the `VERSION`-file-as-source-of-truth model with
version hooks. No database of its own — git remains the actual store, per
`docs/architecture.md`'s Data model.

The narrowest pillar with real capability, and the one least likely to
need revision once built — a deliberate choice to prove the "one pillar,
fully working, talking to Registry & Portfolio over a real Pillar
Interface" pattern before the more dependency-heavy pillars.

## M4 — Deployment & Servers

Managed Process lifecycle for *other* projects' servers: the manifest
(`manifest.deployment.toml`'s `[server]` shape, inherited from
`little-runner`), worktree-based build-before-stop deploys, confirm-gated
deploys for anything that could remove access to the system itself,
defence-in-depth stop. Also stands up **worktree provisioning as a
shared capability** — the thing `docs/pillars/policy_compliance.md`'s
test-script sandbox and Requirements & Quality's sandboxed test execution
both call into later (M10, M12) rather than each building their own.

Reflexive self-redeploy (Core, or Deployment & Servers redeploying
itself) is explicitly **not** in this milestone — see M5.

## M5 — Self-redeploy (reflexive)

The capability this project has described since its first design round —
Core, or any pillar, safely replacing itself with a newer build — built
as its own milestone because `little-runner`'s own history
(`docs/design.md`'s "Restart, incl. little-runner restarting itself")
shows this is genuinely harder than a normal deploy: the cgroup-aware
dispatch check ("am I inside the unit I'm about to stop"), the detached
sibling `systemd-run` unit that survives the target dying underneath it,
`-A`-flagged process matching so a restart command's own argv doesn't
match its own kill pattern, and session/state independence from whatever
unit launched them.

Placed right after M4 rather than at the end: every pillar built from
here on (M6 onward) benefits from being able to safely redeploy itself
mid-development, instead of that safety net arriving only once
everything else is already built.

## M6 — Agentic Development

Claude CLI as a first-class execution target: the `agent.run_task` Job
type, `BranchingSpec` (new/existing/auto, base validated before the agent
starts, the agent hard-forbidden from touching branches whenever
branching was specified), the completion marker
(`done`/`partial`/`failed`, missing always degrading to `partial`), two
review modes (manual, auto-approve), the seven-section change report
written outside the repository, one-task-per-project concurrency, Remote
Control. The GitHub adapter's two inherited techniques — actor lookup via
the issue timeline, cursor-file-based edge detection from polling — ship
here too, since this is the pillar that needs them.

Full spec already written: `docs/pillars/agentic_development.md`.

## M7 — MCP Server (minimal)

The proxy pattern established, not the final tool surface: expose
whatever pillars exist by this point (Registry & Portfolio, Version
Control & Release, Deployment & Servers, Agentic Development) as MCP
tools, registered at user scope per the root MCP policy. Placed here
deliberately early — once this exists, further implementation work on
this project can start using little-workshop's own tools reflexively
instead of raw CLI/API calls, the same bootstrapping value this whole
ecosystem already gets from `little-requirements`/`little-versions`'
MCP surfaces. Its tool coverage grows automatically as M8–M14 ship; none
of those are a reason to revisit this milestone itself.

## M8 — Registry & Portfolio (import path)

The other half of M2: an existing project brought under management
through a Claude CLI session — human-interactive or Agentic
Development's own internal dispatch, the identical mechanism either
way — via this pillar's MCP-exposed entry point. Inspects the existing
repository, brings it into conformance, then registers it exactly the
way `project.bootstrap` does. Depends on M6 for the agent execution
itself and M2 for the registration half it completes.

## M9 — Orchestration (scheduled tasks)

A `ScheduledTask` — a durable definition Orchestration owns, distinct
from a `Job` — that produces a real Job on its own timing, using the
identical creation shape (`type`, `target_pillar`, `project_id`,
`params`, `queue_key`, `depends_on`) any caller already uses today, with
`created_by: "scheduler"`. This is the "scheduler" caller role
`docs/design.md`'s cross-pillar dispatch diagram already named as a
placeholder, made real. Placed here, before Requirements & Quality,
Build & Packages, and Policy & Compliance, precisely because those three
pillars' own periodic-execution use cases (periodic tests, periodic
builds, periodic policy verification) all assume this capability already
exists — building it once in Orchestration means none of them need their
own scheduling mechanism.

**Two schedule kinds, deliberately not full cron**: `once` (a specific
date/time) and `recurring` (a set of weekdays plus one time-of-day).

**Eight operations**: `add`, `edit`, `remove` (hard-delete the
definition; cancels a pending firing or stops a running one first; Jobs
it already spawned stay in Orchestration's history, untouched), `trigger`
(create a Job from the template right now, out of band — the schedule's
own timing is unaffected), `cancel` (skip one specific pending, not-yet-
triggered firing), `stop` (interrupt a Job that is currently *running*
— scheduled-originated or not), `postpone` (retarget one pending firing
to a different date/time, without moving the recurring baseline), and
`queue` (bound how many Jobs run at once).

**`stop`: declared, with a probing fallback.** A Job type's owning
pillar is the primary source of truth for whether it declares a `stop`
hook (`agent.run_task` → kill the `claude` subprocess and mark the run
`stopped`; `deployment.deploy` → `systemctl stop` the in-progress unit)
— the fast path, letting Orchestration reject an undeclared-as-stoppable
type immediately, no network round trip. A type that was never declared
either way isn't therefore assumed unstoppable: Orchestration still
attempts the real call, and the target pillar's own response ("not
stoppable" or actually stopped) is authoritative. Most of Version
Control & Release's fast, synchronous git sequences have no safe
mid-flight interruption point and are correctly declared unstoppable
from the start.

**`queue` generalizes `queue_key`, rather than adding a second, parallel
concurrency mechanism**: `queue_key` gains a configurable *capacity*
(default 1, so every existing caller's behavior — and M1's original
mutex semantics — is unchanged), instead of a strict one-at-a-time
mutex only. Managing a schedule's concurrency is then just: set the
capacity on the `queue_key` its Jobs use.

**The tick**: one in-process background thread inside Orchestration,
waking every 10 minutes to dispatch anything due — not a per-task
`systemd` timer. `recurring` schedules are interpreted in the host's own
local time, with no per-task timezone field — this runs on one
household's machine, not across timezones.

**Missed firings, since little-workshop is locally hosted and can be
down**: a live tick always fires a due task within 10 minutes, so a
`next_run_at` still in the past is only possible at Orchestration's own
*startup* — checked once there, never on the routine tick. A `recurring`
task that missed several occurrences during a longer outage (three days
down is three missed nightly runs) collapses to just the most recent
one: `next_run_at` advances to the correct next future occurrence, and
that single most recent miss is recorded as `missed_run_at`, a pending
decision, never an automatic action either way. `trigger` resolves it
(fires the catch-up Job, clears the field); `cancel` dismisses it
without firing; the task's own next *regular* occurrence firing for real
also clears it, unasked. A `once` task has no next occurrence to
supersede it this way, so a missed one stays pending until a human
actually answers it — there's no automatic path that ever clears it on
its own.

Depends on M1 only — Orchestration itself, and nothing a scheduled Job's
own target pillar needs to have already built, since a `ScheduledTask`
just creates ordinary Jobs on a timer. Individual pillars opting into
`stop` support is incremental per pillar, not a blocking prerequisite
for this milestone itself.

## M10 — Requirements & Quality

`little-requirements`' full model, absorbed: Project→Suite→Requirement→
TestResult, draft/published lifecycle, plus its own never-built M8 (a
Use-Case/Milestone axis independent of test suites) and E6 (JUnit-XML
ingestion beyond pytest, on-demand testing) actually implemented this
time. Sandboxed test execution calls Deployment & Servers' worktree
interface (M4) rather than provisioning its own. Implements the
multi-pillar definition-of-done concept from
`docs/pillars/policy_compliance.md` ("Definition-of-done is multi-pillar,
and can conflict with a request") on its own side — the *conflict-with-a-
policy* half of that mechanism is M12's, once Policy & Compliance exists
to be the other party in that conversation. Periodic test execution
("run this project's suite every night") is just an Orchestration
`ScheduledTask` (M9) whose Job template names this pillar's own test-run
Job type — nothing pillar-specific needed for that.

## M11 — Build & Packages

`little-runner`'s disclaimed CI non-goal, made real: build/test Job
execution via Deployment & Servers' worktrees (M4), fork-safety-gated PR
testing. `little-pypi` absorbed wholesale for the package half: PEP 503 +
`.vsix` hosting, always-on now that the daemon is already persistent (the
5-minute-session compromise no longer applies). Periodic builds are the
same story as M10's periodic tests: an Orchestration `ScheduledTask` (M9)
naming this pillar's build Job type, not a mechanism of this pillar's own.

## M12 — Policy & Compliance

The full pillar, as already specified in
`docs/pillars/policy_compliance.md`: three rule tiers, scope/precedence
with `target_pillar` ownership, the four-gate authoring pipeline
(EARS-compliant, mechanically enforceable, non-conflicting, fully
human-approved), the test-script execution sandbox (Docker-based,
PI-only network, allowlisted read-only filesystem), action-gating
(extends Orchestration's Job model with the `pending_confirmation` state
and the fail-closed/10-minute-override flow), Remote Control as the
notification channel, and the definition-of-done/policy-conflict
refinement flow's other half (paired with M10). Depends on M6 (every
EARS-refinement, conflict-check, and script-generation call) and M4
(every test-script execution) — this is why it's this late, not because
the spec wasn't ready.

**Before or during this milestone**: `docs/open_questions.md`'s `OQ-12`
(does Agentic Development's review mode compose with action-gating on
the same Job) needs an actual answer — this milestone is where the
question stops being hypothetical.

Periodic policy verification (`policy.check_all` on a recurring cadence,
already named in `docs/pillars/policy_compliance.md`'s "on-demand/
scheduled audit" language) is an Orchestration `ScheduledTask` (M9) once
both pillars exist — not a scheduling mechanism this pillar builds for
itself.

## M13 — Populate the policy catalog

Distinct from building the pillar (M12): running the ~50 entries in
`docs/pillars/policy_catalog.md` — confirmed good, tier already
decided — through the real four-gate pipeline one at a time, including
the two-part secret-detection mechanism
(`secret-entry-point-tagged`/`secret-scan-best-effort`) resolved this
round. This is real, individual authoring work per entry, not a bulk
import — each one earns its `policies` row the same way any future
policy will.

## M14 — Dashboard

`little-runner`'s dashboard, generalized with panels for every pillar
that exists by this point. Visual identity already decided and ready to
consume directly: `presentations/assets/identity-tokens.css` (Ledger's
layout, Mission Control's exact color tokens, violet excluded). Placed
late deliberately — its value scales with how much there already is to
show, and none of the pillars above need it to function.

## M15 — Self-hosting / dogfooding

The point where this project's own self-application principle
(`docs/governance_policy.md`) stops being a stated intention and becomes
literally true: little-workshop registers itself as a project in its own
Registry & Portfolio (M2/M8), authors its first real framework policies
through the "little-workshop itself" tab — formalizing
`docs/governance_policy.md`'s independence/evolution/self-application
principles as actual EARS-and-script policies rather than prose only its
own maintainers read (M12/M13) — and migrates its own requirements from
this file's interim `docs/requirements.md` markdown into its own
Requirements & Quality pillar (M10), becoming that pillar's first real
user. Version tracking may migrate to its own Version Control & Release
pillar (M3) at the same time, at the maintainers' discretion — plain
`git` remains a legitimate choice per `docs/version_policy.md` even after
the pillar exists, since nothing in `docs/governance_policy.md` requires
dogfooding a capability the moment it's available, only that it's
available to dogfood.

## M16 — Compatibility shims

`docs/architecture.md`'s new CLI entry names this explicitly: once a
pillar's `lws` subcommand exists, the tool it absorbed — `lreq`, `lvx`,
`lrun`, `little-pypi`, or `bootstrap-little-project` — becomes a thin
forwarder to it, one shim per tool, each shippable the moment its own
pillar is far enough along (in practice, mostly after M2/M3/M4/M6/M10/M11
individually, not all five at once). Existing scripts, muscle memory,
and this very ecosystem's own root policies (which still say "every
submodule uses `lreq`/`lvx`") keep working unmodified during the
transition instead of every caller needing to switch the moment a
pillar ships.

Explicitly not a redesign of any of the five tools' command surfaces —
a shim's whole job is behaving identically to the tool it replaces from
the outside, while the real work happens in `lws` underneath.

## M17 — Ecosystem cutover

The actual point of this project, not just little-workshop's own
internal completeness: every other project in this ecosystem
(`little-meals`, `little-mindcheck`, `little-termstyle`, `little-spectrum`,
`little-tickets`, `little-mapgen`, `little-home`, and `bundle-littles`'
own requirements-DB wiring) currently depends on
`lreq`/`lvx`/`lrun`/`little-pypi` directly. Migrating each one, one
project at a time, verified against the same root policies before and
after the switch, is what actually fulfills `docs/design.md`'s stated
purpose ("governs the full software lifecycle... for every project in
this ecosystem") rather than only proving little-workshop can govern
itself (M15).

Depends on M15 — self-hosting is little-workshop trusting itself with
its own repository before it's trusted with a household's actual running
applications (`little-meals`, `little-mindcheck`) — and M16, since the
shims are what make each individual project's cutover a config change
instead of a rewrite of that project's own tooling assumptions.

Sequencing within this milestone (project order, one at a time vs.
batched, rollback plan per project) is implementation planning for when
this milestone actually starts, not decided here.

## M18 — Retire the absorbed tools

Once nothing in the ecosystem depends on `little-requirements`,
`little-versions`, `little-runner`, `little-pypi`, or
`bootstrap-little-project` — confirmed by M17 actually completing, not
assumed — those five repositories' own active development ends. What
happens to them physically (archived, marked read-only, deleted outright)
is a decision for whoever owns them at the time, not fixed now, far in
advance of anything that would make it real. The one thing worth stating
today: this milestone is the actual retirement, not a milestone about
planning to eventually consider retiring them — that planning is this
paragraph.
