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
| M12 | Policy & Compliance | M1, M4, M6, M10 | Done |
| M13 | Populate the policy catalog | M12 | In progress — tool built, 1/61 entries activated as a real proof; see `docs/requirements.md` M13 |
| M14 | Dashboard | M2–M13 (whatever exists by then) | Done — Overview/Projects/Jobs/Policy panels; see `docs/pillars/dashboard.md` |
| M15 | Self-hosting / dogfooding | M2, M3, M10, M12 | Done — registered, requirements migrated, first framework-policy draft; see `docs/requirements.md` M15 |
| M16 | Full functional parity with the absorbed tools | M1–M14 (whichever tool each gap belongs to) | Done (2026-09-17) — every real gap in all five absorbed tools closed (`docs/requirements.md`'s M16 follow-up); the handful of items left un-ported (`.lrun/config.toml`, `little-requirements`' client-config model, `lock`/`unlock`) are documented, deliberate architectural non-gaps, not deferred work |
| M17 | Ecosystem cutover | M15, M16 | In progress — `little-mindcheck` is the pilot (registered, requirements data migrated, real `deploy`/`stop`/`status` verified against the actual app, 2026-09-17); a new `deployment.set_manifest` capability and a real dispatch-timeout fix were required along the way (`docs/requirements.md`'s M17 section) |

Everything below M1 is new to this pass — laid out in one sitting so the
dependency graph is visible up front, not discovered one milestone late.
**M16–M17 were missing from the first pass entirely** — every milestone
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

**A concrete, already-backed panel**: policy management — every policy
listed in its natural-language `ears_text` form with a checkbox for its
`enabled` toggle, a per-policy gate-point selector (the six categories,
multi-select or none), a raw view/edit of the generated `test_script`
(feeding straight into `policy.activate`, Gate 4's own human-review
step — never a bypass of it), and a per-project waiver
grant/revoke/list view with its required `reason` shown inline. The
entire backend for this (`policy.configure`, `policy.waiver_grant`/
`revoke`, `GET /v1/policies/gating-for-job`) already exists as real
Pillar Interface calls and `workshop_policy_*` MCP tools — this
milestone is the UI surfacing what's already there, not new mechanism.

**Delivered (2026-09-16)**: `src/dashboard/` — a real FastAPI app bound
to a Unix domain socket like every other pillar, server-rendering
Overview/Projects/Jobs panels plus the policy-management panel above in
full (now ten gate-point categories, not six — this milestone landed
after that expansion). The generated `test_script` is read-only in the
panel itself (editing is still `lws policy activate`'s own job, per
Gate 4); every other panel beyond these four is real future work, added
the same additive way whenever a pillar's dashboard need becomes
concrete — see `docs/pillars/dashboard.md`'s own "Left open." Proven
end-to-end for real: the policy panel's enable/disable, gate-point, and
waiver grant/revoke controls each drive a real Job against real
Registry, Orchestration, Agentic Development, and Policy & Compliance
units, and every panel degrades to a visible "unreachable" banner
(never a 500) when the pillar behind it is down.

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

**Delivered (2026-09-16)**: little-workshop registered itself (name
`little-workshop`) via a real `project.import` against its own real
checkout; all 146 `docs/requirements.md` entries migrated into real
Requirements & Quality `Requirement` rows (16 real `Milestone` rows, one
per section, `status` mapped `done`→`published`/`not done`→`draft`);
one framework-policy candidate (formalizing the independence
principle's "no `.lvx/config.json`" rule) drafted through the real
pipeline against the real Claude CLI. **Deliberately not done**: Gate 4
activation of that draft (left for a maintainer's own review — this
project's own Gate 4 discipline, `docs/requirements.md` REQ-000000133,
applies to little-workshop's own framework policies exactly as it does
to anyone else's, self-application principle in action); the evolution
principle wasn't formalized this pass (no mechanically-checkable core
was found that wasn't either trivial or already covered by this
document's own "Amendments" convention — a real Gate-2-shaped judgment
call, not an oversight); version tracking stays on plain `git`, at the
maintainers' own discretion per this section's own text above. A real,
previously-invisible defect surfaced along the way and is tracked as
`docs/open_questions.md`'s `OQ-14`: `orchestration/dispatch.py`'s fixed
20-second dispatch timeout is too short for some real (non-fake-stub)
Claude CLI calls, intermittently misreporting a real Gate-pipeline call
as "pillar unreachable."

## M16 — Full functional parity with the absorbed tools

**Corrected 2026-09-17** (this section previously described "compatibility
shims" — a thin forwarder per tool, kept for backward-compatible command
names). That approach is rejected: the five absorbed tools (`lreq`,
`lvx`, `lrun`, `little-pypi`, `bootstrap-little-project`) are never made
to forward into `lws`, and `lws` never calls into them. Each stays
exactly what it already is, running its own original, unmodified code,
for the entire transition — no shim, no shared code path, no command
name kept alive as a pass-through.

What M16 actually is: closing the real functional gaps a direct,
command-by-command survey found between each old tool's own CLI surface
and what its corresponding pillar(s) implement today (the full survey:
`docs/requirements.md`'s M16 follow-up section). `little-versions` is
closed (standalone `issue list/set/unset`, 2026-09-17). `little-pypi` is
closed on its dev-convenience half (`pip`/config-file helpers,
2026-09-17); its storage half was already close. `bootstrap-little-project`
is closed (2026-09-17): `project.bootstrap` now creates the real GitHub
labels the old tool did and runs a real cross-pillar `version.init` call
(plus the git add/commit/checkout-dev/merge dance) so a freshly
bootstrapped project is left gitflow-ready, matching what the old tool's
own `create` always did. One gap was deliberately deferred rather than
closed: `.lrun/config.toml` is a host-local file (never committed,
written under `~/.config/little-runner/`) with no clear pillar owner and
no clear future pillar-equivalent yet — a low-value, awkward fit that
doesn't belong to `project.bootstrap`'s own job. `little-requirements`
is closed (2026-09-17): `export`, `import`, `report --md/--csv/--json-file`,
and `test-structure-create` — all four pure CLI-side additions, no new
Pillar Interface route. `set-verdict` needed no new code: `result
record` already writes `source="manual"`, giving it full parity
already. Three real gaps stay deliberately un-ported, each for its own
architectural reason: `workspace`/`install`/`update` (a client-side
`.reqs`/`database.conf` config-file model with no equivalent in a
server-authoritative daemon — the same shape of gap as
`bootstrap-little-project`'s own `.lrun/config.toml`); `project`
create/delete/list/get (superseded outright — this pillar never owned
"project," Registry & Portfolio already does); `lock`/`unlock`/
`unlock-server` (moot once a single daemon process is the only writer).
`little-runner` was the one tool left, and the original survey
(`docs/requirements.md`'s REQ-000000161) called it "not close" — true
for two of its concepts, but it undercounted the rest: `docs/pillars/agentic_development.md`'s
own poller/GitHub-adapter/session-tracking machinery already gave
`issue run/finish` and the actor-lookup-and-dispatch half of `session
start/stop/list` full parity by the time this pass ran, simply never
checked off. What this pass (2026-09-17) actually closed: `ci
run/status` as a new `agent.ci_check` Job (resolve a pull request's real
head, run the real sandboxed suite Requirements & Quality already
provisions for any commitish, post the outcome as a real GitHub commit
status), `queue show/release` as pure `lws`-side additions over
Orchestration's own existing primitives (no new pillar code), and
`branch-audit run` as a new read-only `version.branch_audit` Job on
Version Control & Release (naming/stale-merged/abandoned/detached-HEAD/
stray-worktree classification over one project's real branches, reusing
that pillar's own branch-type taxonomy and git primitives rather than
reimplementing them; `lws version branch-audit --all` loops the Job over
Registry & Portfolio's own project list for the old tool's ecosystem-wide
sweep, since that's this daemon's actual authority for "every project" —
never merges, deletes, commits, or pushes, per the original tool's own
rule). `stabilize run` is also closed (2026-09-17), as a new, genuinely
mutating `version.stabilize` Job — **correcting an earlier
mischaracterization**: this document previously described it as "a
repeat-until-green agent-driven CI-fix loop," which was wrong; the real
tool's own docstring says outright what it does — commit pending
changes, bring the integration branch up to date with `origin`, finish
the currently-checked-out feature/improvement/hotfix branch (delegating
to this pillar's own already-existing `version.<type>_finish` Job
handlers, never a second implementation), push, and delete already-
merged branches. `execute=false` (the default, matching the original
tool) is a dry run — every step planned and reported, nothing on disk or
`origin` actually changes; `lws version stabilize --all` loops the Job
over Registry & Portfolio's own project list for the old tool's
ecosystem-wide sweep.

`session start/stop/list`'s ad-hoc, human-driven half — the very last
open item across all five tools — is also closed (2026-09-17), as
`agent.session_start`/`session_stop` on Agentic Development. It had
been deferred on the reasoning that it was "host-process machinery ...
with no equivalent in this daemon's own Job model" — that didn't hold
up once actually attempted: Deployment & Servers already launches and
tracks long-running background processes started once and stopped
later, and M5's self-redeploy worker already established the
`systemd-run --user --collect` detached-dispatch pattern this reuses.
The genuinely new piece was only the PTY itself — `agent.run_task`'s own
Remote Control usage is headless — which `dtach` supplies, kept alive
in its own `systemd --user` *scope* so it survives this pillar's own
unit restarting, `little-runner`'s own hard-earned finding ported
directly. **Every real gap across all five absorbed tools is now
closed** — M16 is done.

Each tool's gaps are closed independently, one pillar's worth of real
implementation work at a time, never a redesign of what `lws`'s own
subcommands already look like — the goal is that `lws` can do everything
the old tool could, not that it mimics the old tool's own command
spelling.

## Dashboard redesign (2026-09-21)

Not a numbered milestone: the M14 dashboard, rebuilt around managing
projects rather than listing pillars, in `little-runner`'s own visual
design, installable on Android and usable from a phone, with Claude
Code sessions launchable per project. See `docs/pillars/dashboard.md`
and `docs/requirements.md`'s REQ-000000193–197.

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
applications (`little-meals`, `little-mindcheck`) — and M16: cutover
means a project's owner actually switches what they type/script (the old
command, then the equivalent `lws` one), which only makes sense once
`lws` genuinely does everything the old tool did for that project's own
usage. The old tool keeps running, untouched, alongside `lws` for as
long as its owner wants — cutover is "we now use `lws` for this," never
"the old tool stopped working."

Sequencing within this milestone (project order, one at a time vs.
batched, rollback plan per project) is implementation planning for when
this milestone actually starts, not decided here. **Explicitly not part
of this milestone, or any milestone**: uninstalling the five old tools.
That's a manual, confidence-gated decision for little-workshop's own
maintainers to make once every milestone is done, a first release
exists, and `lws` has been personally validated through real use — never
an automated step this project's own tooling performs on its own
(`docs/architecture.md`'s CLI section).

**Started 2026-09-17, pilot: `little-mindcheck`** (chosen for having the
most complete existing setup — `.lvx`, `.reqs`, and `.lrun/config.toml`
all present — the fullest test of the parity surface at once). Registered
via a real `project.import` against its real, untouched checkout;
existing `little-requirements` data (23 published requirements, all
passing) migrated into Requirements & Quality, `done_count` matching the
old tool's own computed state exactly; real `deploy`/`stop`/`status`
verified against the actual app (a real build, a real listening server,
a real Tailscale mapping, then a real, clean teardown back to not-
running). Getting this far required little-workshop's own first real
production bootstrap on this machine (Core had only ever run inside test
fixtures before) and surfaced two real gaps closed along the way, not
planned for in advance: `deployment.set_manifest` (nothing could
previously write a project's own server manifest at all) and a dispatch-
timeout fix for `deployment.deploy` (the same `OQ-14` class of defect —
a real `bazel run //:install` genuinely exceeds the 20-second default on
a cold cache). Full detail: `docs/requirements.md`'s M17 section.

Still open for `little-mindcheck`'s own cutover: switching its daily-use
scripts (README's `lrun server up/down/status` lines, `tools/report_test_results.sh`,
and `pyproject.toml`'s `little-requirements` pytest-plugin dependency,
which actively auto-records requirement test results on every real test
run today — replacing that live mechanism needs a real plan, not just a
rename, since the tests use `@pytest.mark.requirement(...)` markers
rather than requirement IDs embedded in test names, and `lws
requirements import-junit`'s own regex-based correlation doesn't see
markers at all without a generated test-node-id-to-requirement mapping
file). Work for this happens on a dedicated branch in `little-mindcheck`'s
own repo, merged to `dev` then `master` only once confirmed working —
never in place, per this milestone's own explicit instruction for the
pilot.
