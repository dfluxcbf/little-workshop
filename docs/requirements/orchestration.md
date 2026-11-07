# Requirements: Orchestration

Part of little-workshop's requirements register; conventions and the other parts are in [../requirements.md](../requirements.md). Sections keep the order and headings of the round that added them.

## M1 — Core + Orchestration

| ID | Requirement | Status |
|---|---|---|
| REQ-000000001 | `manifest.toml` loads pillar enabled-flags and crash/health-probe settings, with documented defaults when the file is missing. | done |
| REQ-000000005 | Health probing uses a 5-second timeout and up to 10 retries against a pillar's own `/v1/health` report, not a bare ping. | done |
| REQ-000000006 | A crashed pillar is relaunched for up to 10 consecutive tries, and is marked **faulted** (and never retried again automatically) once that limit is exceeded. | done |
| REQ-000000007 | A pillar confirmed healthy after a relaunch has its crash counter reset. | done |
| REQ-000000009 | The `Job` model carries every field `docs/design.md` specifies (`id, type, target_pillar, project_id, params, status, queue_key, depends_on, created_by, result`) and the full six-value status enum. | done |
| REQ-000000010 | Two Jobs sharing a `queue_key` never both run at once; the mutex frees once the holder reaches a terminal status. | done |
| REQ-000000011 | A Job with unmet `depends_on` stays queued until every dependency succeeds. | done |
| REQ-000000012 | A Job whose dependency failed or was cancelled is itself failed automatically, cascading through multi-level dependency chains. | done |
| REQ-000000013 | `Pipeline`/`PipelineRun`: a named, versioned DAG of Job templates expands into concrete, dependency-linked Jobs at trigger time, rejecting cyclic or unknown-node definitions. | done |
| REQ-000000014 | Orchestration dispatches a ready Job to its `target_pillar`'s Pillar Interface over that pillar's Unix socket, recording the outcome in `job_runs`. | done |
| REQ-000000015 | Dispatch to a disabled/unknown pillar fails the Job immediately; dispatch to an enabled-but-unreachable pillar leaves the Job queued for retry. | done |
| REQ-000000016 | Orchestration's Pillar Interface exposes Job and Pipeline creation/inspection endpoints (`/v1/jobs`, `/v1/pipelines`, `/v1/pipelines/{name}/trigger`) plus a completion callback for long-running Jobs. | done |
| REQ-000000017 | The minimal `lws` CLI supports `core status` and `job create`/`list`/`status`, talking to the daemons over their Unix sockets. | done |
| REQ-000000018 | `lws core status`/`job list`/`job status` fall back to direct manifest/database reads when the relevant daemon isn't running, rather than failing outright. | done |
| REQ-000000019 | The project-files shared access library enforces filename-encoded write-ownership (`<name>.<owner-tag>.<format>`): any pillar may read, only the owning pillar may write. | done |
| REQ-000000020 | Log files are trimmed by a size-based rolling window (oldest lines dropped first), never a full-file rotation. | done |

## M2 — Registry & Portfolio (bootstrap path)

| ID | Requirement | Status |
|---|---|---|
| REQ-000000031 | Orchestration dispatching a real `project.bootstrap` Job to Registry & Portfolio running as a genuine `systemd --user` unit produces an actual git repository on disk — proven end-to-end, not mocked. | done |

## M5 — Self-redeploy (reflexive)

| ID | Requirement | Status |
|---|---|---|
| REQ-000000058 | A dispatched worker that completes on behalf of an accepted (`outcome: "accepted"`) Job reports back to Orchestration's `/v1/jobs/{id}/complete` callback — the first real exercise of that previously-unused async-Job completion path from M1. | done |

## M6 — Agentic Development

| ID | Requirement | Status |
|---|---|---|
| REQ-000000068 | Concurrency is the same `queue_key` mutex Orchestration already provides (M1) — `lws agent run-task` sets `queue_key=project_id`, so a project never runs two agent sessions at once; nothing new was built for this. | done |
| REQ-000000069 | Because a real agent session can run for up to an hour, `agent.run_task`'s Job handler dispatches the actual work to a background thread and returns `accepted` immediately, reporting completion back to Orchestration afterward — the same async-Job pattern M5's self-redeploy worker uses, now exercised for the reason design.md's Job concept named it for in the first place. | done |

## M7 — MCP Server (minimal)

| ID | Requirement | Status |
|---|---|---|
| REQ-000000080 | Proven end-to-end against a genuine `systemd --user` unit: a real `tools/call` JSON-RPC request, driven through `MCPServer.handle_request` exactly as the stdio loop would, bootstraps a real project via a real Orchestration-dispatched Job to Registry & Portfolio, and a second real `tools/call` lists it back — both discovered through a real Core registry, never hardcoded. | done |

## M9 — Orchestration (scheduled tasks)

| ID | Requirement | Status |
|---|---|---|
| REQ-000000085 | A `ScheduledTask` is a durable definition Orchestration owns, distinct from a `Job`: `{id, name, job_template, schedule, state, next_run_at, missed_run_at, last_job_id, created_at, updated_at}`, where `job_template` carries the identical creation shape (`type`, `target_pillar`, `project_id`, `params`, `queue_key`, `depends_on`) any caller already uses today. Firing it creates an ordinary Job with `created_by: "scheduler"`. | done |
| REQ-000000086 | Two schedule kinds, deliberately not full cron: `once` (a specific naive local date/time) and `recurring` (a set of weekdays, 0=Monday..6=Sunday, plus one `HH:MM` time-of-day) — computed by pure, I/O-free functions (`first_occurrence`, `next_occurrence_after`) in `src/orchestration/schedule.py`. | done |
| REQ-000000087 | Eight operations exposed on Orchestration's Pillar Interface: `add`, `edit` (a full `job_template`/`schedule` replacement when either is changed — no partial merge — recomputing `next_run_at` and clearing `missed_run_at`), `remove` (cancels a pending firing or stops a running one first, then hard-deletes the definition; Jobs it already spawned stay in Orchestration's history untouched), `trigger` (fires a Job from the template right now, out of band, leaving the schedule's own `next_run_at` untouched, and clears `missed_run_at`), `cancel` (clears `missed_run_at` if the task was pending on a missed firing, else skips just the next occurrence; refused for `once` tasks, which point at `remove` instead), `stop` (interrupts a currently-*running* Job, scheduled-originated or not — a general Job-level operation, not scoped to `ScheduledTask`), `postpone` (retargets one pending firing's date/time without moving the recurring baseline), and `queue` (reads/sets a `queue_key`'s concurrency capacity). | done |
| REQ-000000089 | Missed firings are detected only at Orchestration process startup (`startup_check`), never on the routine tick — a live tick always catches anything due within its own window. A `recurring` task with several missed occurrences during downtime collapses to just the most recent one. Resolution is via the existing `trigger`/`cancel` verbs; a `recurring` task's missed flag auto-clears on its own next regular firing, but a `once` task's missed flag never auto-clears — it persists until a human explicitly triggers or cancels it. | done |
| REQ-000000090 | The routine tick runs on an in-process daemon thread (`SchedulerThread`) on a 10-minute interval (`DEFAULT_TICK_INTERVAL_SECONDS = 600.0`, overridable only for tests via `LWS_SCHEDULER_TICK_SECONDS`, never set in production); `start()` always runs `startup_check` synchronously before the first tick. | done |
| REQ-000000091 | `queue` generalizes `queue_key` from a strict mutex into a configurable-capacity pool (`queue_capacities` table, default 1, so every existing caller's behavior is unchanged) instead of adding a second, parallel concurrency mechanism. | done |
| REQ-000000093 | Proven end-to-end against genuine `systemd --user` Orchestration and reference-pillar units: a scheduled task fires for real on the real tick loop; a missed firing is flagged across a real Orchestration process restart and resolved via a real `trigger`; `stop` genuinely interrupts a running Job on the real reference pillar; and a raised `queue_key` capacity allows real concurrent dispatch of two Jobs at once. | done |

## M10 — Requirements & Quality

| ID | Requirement | Status |
|---|---|---|
| REQ-000000097 | Eight requirement/suite operations exposed as Orchestration Job types (`requirements.create_test_suite`, `delete_test_suite`, `create_requirement`, `update_requirement`, `publish_requirement`, `delete_requirement`, `link_requirement_to_suite`, `unlink_requirement_from_suite`), each validating its own required params and failing cleanly (never a raw exception) for a missing `project_id`, an unknown requirement/suite id, or an edit attempted on a published requirement. | done |
| REQ-000000102 | Periodic test execution ("run this project's suite every night") needed no mechanism of this pillar's own: it is just an Orchestration `ScheduledTask` (M9) whose `job_template` names `requirements.run_tests` — proving the M9 design's own claim that building scheduling once in Orchestration means later pillars never need their own scheduling mechanism. | done |

## M11 — Build & Packages

| ID | Requirement | Status |
|---|---|---|
| REQ-000000111 | Periodic builds needed no mechanism of this pillar's own — the same story as M10's periodic tests: just an Orchestration `ScheduledTask` (M9) whose `job_template` names `build.run`. | done |

## M12 — Policy & Compliance

| ID | Requirement | Status |
|---|---|---|
| REQ-000000125 | Periodic policy verification needed no mechanism of this pillar's own — the same story as M10's periodic tests and M11's periodic builds: just an Orchestration `ScheduledTask` (M9) whose `job_template` names `policy.check_all`. | done |

## M16 follow-up — `little-runner`, the last absorbed tool (2026-09-17)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000175 | **`lrun queue show/release`, closed, as `lws job queue show/release`** — pure `lws`-side additions needing no new pillar code: `show` lists Orchestration's own running+queued Jobs (`GET /v1/jobs?status=`) grouped by `queue_key`; `release` stops the running holder of a given `queue_key` (the existing `POST /v1/jobs/{id}/stop`) and pumps Orchestration (`POST /v1/pump`) so the next queued Job dispatches immediately rather than waiting for the next tick. | done |

## Workbench usability: ordering, workspace session, reporting, bootstrap, scheduling (2026-09-21)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000209 | **Delete jobs older than 12 hours.** When the user chooses 12 hours in Delete old jobs, the Orchestration pillar shall delete every finished job older than 12 hours and keep those that active work still depends on. | done |

## Policy sandbox isolation and change-driven checking (2026-09-23)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000243 | **Orchestration's own dispatch timeout for a policy check is configured above the sandbox's own ceiling.** Where a Job type genuinely runs longer than Orchestration's 20-second default dispatch timeout, `orchestration/dispatch.py`'s per-Job-type override table (`JOB_TYPE_TIMEOUTS`, the same mechanism already used for `policy.author`, `policy.activate` and `deployment.deploy`) shall carry a value safely above that Job type's own real ceiling — `policy.check_project`'s is set to 1860s, 60s above `policy/sandbox.py`'s own 1800s sandbox-check ceiling, so a real (non-skipped) check is never misreported as "target pillar unreachable" and requeued while its Docker container is still legitimately running. | done |

## Policy check dispatched asynchronously (2026-09-23)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000244 | **A real policy check never blocks the Policy pillar's own request handling.** Where `policy.check_project` must actually run the sandbox (the unchanged-fingerprint skip does not apply), the Policy pillar shall dispatch that check to a background thread and return `accepted` immediately, reporting the eventual verdict back to Orchestration's `/v1/jobs/{id}/complete` callback — the same async-completion pattern `agent.run_task` and the self-redeploy worker already use. A skipped (unchanged) check stays fully synchronous, since nothing in that path can block. This keeps `/v1/health`'s responsiveness to Core's own probe decoupled from how long any individual check takes, closing the gap that let a single slow check leave the pillar looking crashed. | done |

## Release-time double-dispatch fixed (2026-09-23)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000251 | **Every declared-unstoppable Job type gets a dispatch timeout that actually covers its real work.** Where a Job type is in `DECLARED_UNSTOPPABLE_JOB_TYPES` (the maintainer's own "real, possibly-slow, never safely interruptible" set — project bootstrap/import/remove, every version.* branch/release/support mutation), Orchestration's dispatch timeout for it shall be well above the plain 20s default. Root cause, reproduced live during this session's own dev-to-master release: `version.release_start` and `version.release_finish` both hit the 20s default, got marked "target pillar unreachable: timed out" and requeued while the first invocation was still genuinely running server-side, and were then re-dispatched a moment later — the retry landed mid-flight on the first invocation's own uncommitted git state ("Working tree is dirty" / "Must run from 'dev'. Current branch: 'release/0.1.1'"), a confusing but non-fatal symptom (the underlying release itself completed correctly both times; only the reported Job status was wrong). Every type in that set now gets 180s, reusing the existing set as the single source of truth rather than a second hand-picked list. | done |

## One-time scheduled tasks are deleted once spent (2026-09-24)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000258 | **A spent one-time scheduled task is deleted.** When a `once` scheduled task fires on schedule, or its missed firing is triggered or dismissed, Orchestration shall delete it, so the New task and Schedule job pages list only tasks that can still fire or still need a missed-run answer; recurring tasks and not-yet-fired `once` tasks are kept, and the Jobs a task spawned stay in history. At startup, Orchestration shall delete any spent `once` task left by an older version. | done |

## A settled policy-gated Job frees its queue (2026-09-24)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000268 | **A settled policy-gated Job frees its queue.** When a Job awaiting policy confirmation times out or is stopped, the Orchestration pillar shall settle it (a stopped one as cancelled) and shall dispatch whatever Job is ready behind it on the same queue key right away. | done |

## A waiting Job is answered at once (2026-09-24)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000271 | **A waiting Job is answered at once; a slow answer is not "unreachable".** When a new Job has to wait behind its policy checks or a busy queue key, the Orchestration pillar shall answer the request at once with the queued Job and its id, and the MCP server and lws shall report a pillar that took a request but has not answered in time as still working, never as unreachable. | done |

## Deciding gated jobs from the Dashboard (2026-10-01)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000291 | **Each gate action settles the job through Orchestration.** When the user chooses Confirm anyway or Cancel or Mark as failed on a Job awaiting confirmation, the Dashboard shall dispatch it or cancel it or settle it as failed through Orchestration and return to the page it came from with the outcome. | published |

## Policies as the hard layer of Claude instructions (2026-10-02)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000310 | **Every job has a checklist of its actions.** When a Job is created, the Orchestration pillar shall give it the ordered list of actions its type declares (one action for a type that declares none), keep each action's state (not started, running, succeeded, failed, needs attention), its runtime without time spent waiting for the user, and its expected runtime, the average of the last 50 successful runs of that action, and return the list with the Job. | draft |

## Claude usage, quick wins (M18, 2026-10-04)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000322 | **A caller waits for a Job in one call.** When a caller asks Orchestration to wait for a Job, the Orchestration pillar shall answer with the Job and its steps as soon as the Job has settled, is held in `pending_confirmation` or has a step waiting for the user's attention, and otherwise when the timeout the caller gave (120 seconds when none is given, never more than 300) has passed; the MCP server shall offer the wait as the read-only tool `workshop_job_wait` and the CLI as `lws job wait`. | draft |

## Claude usage, automation: mining and workshop triggers (M21, 2026-10-04)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000343 | **A trigger creates a Job when an event happens and never one that merges, finishes or deletes.** The Orchestration pillar shall keep triggers of the form event, filter and job template beside its scheduled tasks; shall refuse, when a trigger is created, a job template whose type merges, finishes, releases, removes or deletes; when an event matches an enabled trigger's event name and every filter value, shall create the template's Job with `created_by` set to `trigger:<id>`, with the event's project where the template names none and with `$event.<key>` parameter values replaced by the event's, through the same policy gate as any other Job; shall not let a Job a trigger created fire that same trigger; and shall stop a chain of triggers at a depth of three. | draft |
| REQ-000000344 | **Events come from Jobs settling and from the pillars that own them.** When a Job settles, the Orchestration pillar shall raise `job.settled` with the Job's type, project and status once per Job and only while a trigger exists, answering also to `job.succeeded` or `job.failed`, to `version.branch_finished` for a succeeded branch finish and to `version.release_finished` for a succeeded release; shall accept other events from the pillars through `POST /v1/events` while refusing its own event names there; and the Usage & Efficiency pillar shall report `claude.session_ended` and `usage.finding_opened` and the Registry & Portfolio pillar `registry.project_registered`, each best effort. | draft |

## Stuck jobs, force stop and incidents (2026-10-04)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000365 | **A running task can be stopped.** When Orchestration asks the Agentic Development pillar to stop a running `agent.run_task` Job, the pillar shall stop it: a run whose worker is gone is marked stopped at once, and a live run has every process of its Claude terminated and is marked stopped, its branch and anything committed on it left as they are. | draft |
| REQ-000000368 | **Unexpected behaviour becomes an incident.** When a Job stays running past its time limit or systemd restarts a pillar after it failed, the workshop shall record an incident: of kind "unexpected behaviour" from Orchestration naming the Job, its type and project and how long it ran, or of kind "crash" from Core with the last lines of the pillar's log; and the Incidents pages shall show each incident's kind. | draft |

## Live pages and Claude peek (2026-10-04)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000378 | **Orchestration publishes its changes.** The Orchestration pillar shall number every change of a Job, a Job step and a scheduled task, and shall answer a request for the changes after a given number as soon as there is one, or with none after the time the caller allows, saying when its numbering restarted. | draft |

## Reading only what is shown; Claude's short answers watched (2026-10-05)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000382 | **Jobs are listed as filtered summaries.** The Orchestration pillar shall keep with each Job a brief of its params and result, written with the Job (every value up to a small size; a larger one is only named as left out), and shall list Jobs as summaries when asked (the brief in place of the full params and result), filtered by status, type, project, creator, creation time and id, so that a caller reads only the Jobs and the parts of them it shows. | draft |

## Pillar crashes and the work they cut (2026-10-05)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000402 | **A dispatch that times out is an incident.** If a pillar takes a Job's dispatch request and does not answer within the dispatch timeout of the Job's type, then the Orchestration pillar shall record an incident of kind "unexpected behaviour" naming the Job type, the pillar and the timeout and warning that the requeued Job may run twice, at most once an hour for each Job type and pillar. | draft |
| REQ-000000403 | **A dispatch Orchestration never finished is settled when it starts.** When the Orchestration pillar starts, the Orchestration pillar shall fail each Job still running whose dispatch its previous process never finished with a reason saying that its outcome is unknown, fail the Jobs that depend on it and record one incident of kind "unexpected behaviour" listing them, and shall leave a Job that its pillar accepted to that pillar. | draft |
| REQ-000000406 | **A pillar redeploy is never dispatched twice.** The Orchestration pillar shall allow a deployment.redeploy_pillar dispatch up to 1800 seconds to answer so that a redeploy waiting behind the redeploys queued before it in Deployment & Servers is never requeued and run a second time. | draft |
| REQ-000000407 | **A dispatch the pillar dropped is settled at once.** If a pillar drops a Job's dispatch request without answering, then the Orchestration pillar shall fail the Job with a reason saying that whether the pillar did it is unknown and record an incident of kind "unexpected behaviour" naming the Job, instead of leaving it running until the six-hour timeout. | draft |

## Usage analysis: dispatch ceiling (2026-10-24)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000433 | **`usage.analyze` has a dispatch ceiling above Orchestration's 20-second default.** The Usage pillar runs the rollup, the detectors and the experiment measurements synchronously inside its request handler, so `orchestration/dispatch.py`'s `JOB_TYPE_TIMEOUTS` carries `usage.analyze` at 300s; with the default the Job was reported `DispatchTimeout`, requeued while still running and could run twice. | done |

## Phase 6: closing the gaps (2026-11-07)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000525 | **An idempotent Job cut off by an Orchestration restart runs again.** When Orchestration starts and finds a Job whose dispatch the previous process never finished, if the Job's type is idempotent (policy.check_project, policy.check_all, requirements.analyze_conflicts, usage.analyze: each only records a verdict or an analysis), then Orchestration shall requeue it with a run row saying why, leaving the Jobs that depend on it waiting, instead of failing it; a Job of any other type shall still be failed with its dependants and reported as before. | draft |
