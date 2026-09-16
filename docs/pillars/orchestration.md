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
