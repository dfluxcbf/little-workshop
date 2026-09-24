# Orchestration

## Description

Orchestration is the substrate every other pillar's mutating work runs
through: a `Job` (`id`, `type`, `target_pillar`, `project_id`, `params`,
`status`, `queue_key`, `depends_on`, `created_by`, `result`, `error`) is
the one unit of work this whole system recognizes, and Orchestration is
the pillar that turns a queued Job into a real dispatched call against
whichever pillar owns it, tracks its outcome, and settles anything that
depended on it. It is Core's oldest sibling — built alongside Core in M1,
before any pillar existed with work worth dispatching — and, unlike every
other pillar, it is required: little-workshop has no meaningful "off"
switch for it.

Absorbs `little-runner`'s per-repo queue/slot logic, generalized from
one project's server-deploy queue to every job type and every pillar.

## Purpose & goal

"Every unit of work happens once, in the right order, and its outcome is
recorded — regardless of which pillar performs it" (`docs/architecture.md`'s
Components table). Two failure modes this exists to rule out: a mutation
running twice because two callers raced each other, and a mutation's
outcome being unknown because nothing recorded what happened once it was
dispatched.

## Responsibilities

- **The Job model itself** — the one shape every caller (CLI, MCP,
  Dashboard, another pillar) creates work through. `POST /v1/jobs`
  inserts a Job and immediately attempts to dispatch it; `GET /v1/jobs`
  and `GET /v1/jobs/{id}` are the read side.
- **Dispatch** (`orchestration/dispatch.py`) — `POST /v1/jobs/<type>` on
  the target pillar's own socket, resolved through Core's registry, never
  hardcoded. A `200` response with `outcome: succeeded` settles the Job
  immediately; `outcome: failed` settles it as failed; `outcome: accepted`
  leaves it `running` for a long-running pillar-side task (an agent
  session, a self-redeploy) to settle later via its own callback,
  `POST /v1/jobs/{id}/complete`. A pillar that's unreachable or disabled
  leaves the Job `queued` (unreachable) or fails it outright (disabled/
  unknown), rather than blocking the caller.
- **The `queue_key` mutex** — two Jobs sharing a `queue_key` never run
  concurrently; the second stays `queued` until the first settles
  (`succeeded`/`failed`/`cancelled`). Every pillar that needs "one
  operation at a time per project" (Version Control & Release's gitflow
  commands, Deployment & Servers' deploys, Agentic Development's
  `agent.run_task`) gets it for free by setting `queue_key=project_id` —
  nothing pillar-specific was built for this.
- **`depends_on` resolution and cascade failure** (`orchestration/queue.py`)
  — a Job with unset dependencies never dispatches; a Job whose dependency
  failed or was cancelled fails too, recursively through a multi-level
  chain, on every dispatch pump rather than only when someone asks.
- **Pipelines** (`orchestration/pipelines.py`, `POST /v1/pipelines`,
  `POST /v1/pipelines/{name}/trigger`) — a named, topologically-ordered
  set of Job templates that expands into real Jobs with `depends_on`
  wired between them on trigger. Built as general-purpose infrastructure
  in M1; no pillar has a concrete use for it yet as of M8.

- **Job history cleanup** (`GET`/`POST /v1/job-cleanup`, 2026-09-21) — the
  Dashboard's "Delete old jobs". `GET` previews how many finished jobs each
  age (in hours) would remove; `POST {"older_than_hours": N}` deletes them and
  their `job_runs`. Only **finished** jobs (`succeeded`, `failed`,
  `cancelled`, `stopped`) last updated before the cutoff are eligible; a
  running, queued or awaiting-confirmation job is never touched however old.
  Also kept, regardless of age: a job an unfinished job still `depends_on`
  (the queue reads its status), a scheduled task's `last_job_id`, and every
  job of a pipeline run that is not wholly eligible — a pipeline run is
  removed whole (its row too) or kept whole. The response says how many were
  deleted and how many eligible jobs were kept for those reasons. Direct
  endpoints rather than a Job type, so cleaning up never records a job of
  its own; deliberately no MCP tool or CLI command — it is a human action in
  the Dashboard.

## Scheduled tasks (`docs/milestones.md` M9)

A `ScheduledTask` is a durable definition, distinct from a `Job`, that
produces a real Job on its own timing — the identical creation shape any
caller uses today, with `created_by: "scheduler"`, the placeholder
caller role `docs/design.md`'s dispatch diagram already named. No pillar
needs its own scheduling mechanism for a periodic version of what it
already does (periodic tests, periodic builds, periodic policy audits,
periodic `dev`→`master` releases) — it's just a `ScheduledTask` naming
that pillar's existing Job type.

- **Schedule kinds**: `once` (a specific date/time) or `recurring` (a
  set of weekdays plus one time-of-day) — deliberately not full cron.
  A `once` task is deleted as soon as it is spent — fired on time, or
  its missed firing triggered or dismissed — so the scheduled-task
  lists only ever show tasks that can still fire or still need an
  answer; its Job stays in history. Orchestration also deletes, at
  startup, any spent `once` task an older version left behind
  (2026-09-24).
- **Eight operations**: `add`, `edit`, `remove` (hard-delete the
  definition; cancels a pending firing or stops a running one first;
  Jobs it already spawned stay in Orchestration's history, untouched),
  `trigger` (create a Job from the template right now, out of band — the
  schedule's own `next_run_at` is unaffected), `cancel` (skip one
  specific pending, not-yet-triggered firing — for `recurring`, only
  that one occurrence; for `once`, equivalent to `remove`), `stop`
  (interrupt a Job that is currently *running*, scheduled-originated or
  not), `postpone` (retarget one pending firing to a different
  date/time, without moving the recurring baseline), and `queue` (bound
  concurrency — see below).
- **`stop`: declared, with a probing fallback.** A pillar's own code is
  the primary source of truth for which of its Job types support a
  `stop` hook — the fast path: Orchestration can reject a `stop` call
  against an undeclared-as-stoppable type immediately, no network round
  trip, and report a task's stoppability in listings without asking
  anyone. A Job type that was never declared either way is *not*
  therefore assumed unstoppable, though — Orchestration still attempts
  the real `POST /v1/jobs/{id}/stop` call, and the target pillar's own
  response (stopped, or "not stoppable") is authoritative. Declaration
  is the fast, common path; probing is the correctness fallback for
  anything a pillar's author didn't get around to declaring.
- **`queue` generalizes `queue_key`**, rather than adding a second,
  parallel concurrency mechanism: `queue_key` gains a configurable
  *capacity* (default 1, so every existing caller's behavior — and M1's
  original mutex semantics — is unchanged), instead of a strict
  one-at-a-time mutex only. Managing a schedule's concurrency is then
  just: set the capacity on the `queue_key` its Jobs use.
- **The tick**: one in-process background thread inside Orchestration,
  waking every 10 minutes to dispatch anything due — not a per-task
  `systemd` timer. `recurring` schedules are interpreted in the host's
  own local time; little-workshop runs on one household's machine, not
  across timezones, so there's no per-task timezone field to get wrong.

### Missed firings — little-workshop is locally hosted, and can be down

A live tick always fires a due task within 10 minutes, so a
`next_run_at` still in the past is only possible right at Orchestration's
own **startup** — proof the process was down through the moment it
should have fired, not a bug in the tick itself. This check happens
once, at startup, never on the routine 10-minute tick.

A `recurring` task can have missed several occurrences during a longer
outage (three days down is three missed nightly runs) — only the most
recent one is worth anything; running all three back to back is never
useful. On startup, a task found overdue has its `next_run_at` advanced
to the correct next *future* occurrence per its own recurrence rule, and
the single most recent missed timestamp is recorded as
`missed_run_at` — a pending decision, not an automatic action either
way.

Resolving it reuses the same two verbs already defined above, rather
than inventing new ones: `trigger` fires the missed run's Job right now
and clears `missed_run_at`; `cancel` dismisses it without firing.
Whichever pillar/UI surfaces this (`lws schedule list` for now; Dashboard
once M14 exists) shows `missed_run_at` as a pending question, not a
silent gap.

If neither happens before the task's own next *regular* occurrence
fires for real, that firing clears `missed_run_at` on its way — a stale
question about a run from days ago is moot the moment this cycle's own
run actually happens, exactly as if the question had been answered "no."
A `once` task has no next occurrence to supersede it this way, so a
missed `once` task's `missed_run_at` stays pending indefinitely until a
human actually answers it — there is no automatic path that ever clears
it on its own. Either answer spends it, and it is deleted.

## Interfacing

Orchestration is the one pillar every mutating caller goes through — it
has no domain data of its own to protect, only the Jobs table recording
what every *other* pillar was asked to do.

- **Called by**: `lws` (`cli.client.create_job`), the MCP Server
  (`mcp_server.client.create_job`), and — eventually — Dashboard, for
  every mutation. Also called directly by a pillar's own dispatched
  worker reporting completion (M5's self-redeploy worker, M6's
  `agent.run_task` background thread) via `POST /v1/jobs/{id}/complete`.
- **Calls**: whichever pillar a Job's `target_pillar` names — Registry &
  Portfolio, Version Control & Release, Deployment & Servers, Agentic
  Development so far — resolving that pillar's socket through Core's
  registry (`common.pillar_socket`) each time, never caching or
  hardcoding a path.
- **Discovered through**: Core, the same as every pillar-to-pillar call
  in this system — Orchestration's own socket is just another entry in
  Core's registry, which is how `mcp_server.client` and M5's dispatched
  workers find it.
- **Never calls**: Registry & Portfolio to resolve a project's path —
  that's each *target* pillar's own job (`common.registry_client`),
  not something Orchestration does on a caller's behalf. Orchestration
  only ever forwards `project_id` as an opaque field in the Job payload.
- **Calls, once M9 lands**: whichever pillar owns a `ScheduledTask`'s
  `target_pillar`, on `stop`, if that pillar declares a `stop` hook for
  the Job type in question — the same discovery-through-Core, no-shared-
  import discipline as every other pillar-to-pillar call.
