# Requirements

little-workshop's own interim requirements register — see
`docs/requirements_policy.md` for why this exists instead of `lreq`: this
project is exempt from the root requirements policy, and tracks its own
requirements in this file until its Requirements & Quality pillar exists
to do it for real, at which point little-workshop becomes its own first
user of that pillar (the self-application principle,
`docs/governance_policy.md`).

## Conventions

- **ID format**: `REQ-` followed by 9 digits (`REQ-000000001`), matching
  this ecosystem's existing shape exactly — sequential, never reused,
  never chosen ahead of time, assigned in the order a requirement is
  actually created in this file.
- **One requirement, one row.** Text stays short enough to fit a table
  cell; anything that needs more explanation links to the
  `docs/`/`docs/pillars/` section that's actually authoritative for it,
  per this project's own "don't duplicate content across files" habit.
- **Status**: `draft` (mutable, the default) → `done` (implemented,
  tested, and the test confirmed passing — never marked done on
  implementation alone). Nothing in between; a requirement that's
  half-true isn't done.
- **Milestone**: which `docs/milestones.md` entry a requirement belongs
  to. A milestone's definition-of-done (per
  `docs/policies/documentation_policy.md` inherited via this project's
  own kept conventions) requires every one of its requirements at `done`.
- New requirements are created as part of planning a milestone's actual
  work, not invented speculatively ahead of need.

## M1 — Core + Orchestration

| ID | Requirement | Status |
|---|---|---|
| REQ-000000001 | `manifest.toml` loads pillar enabled-flags and crash/health-probe settings, with documented defaults when the file is missing. | done |
| REQ-000000002 | Core launches each enabled pillar as a transient `systemd --user` unit (`Restart=on-failure`), never an installed `.service` file. | done |
| REQ-000000003 | On Core's own startup, an already-running, healthy pillar is reconnected to, never relaunched. | done |
| REQ-000000004 | On Core's own startup, a disabled pillar that is still running is stopped; a missing, enabled, installed pillar is started fresh. | done |
| REQ-000000005 | Health probing uses a 5-second timeout and up to 10 retries against a pillar's own `/v1/health` report, not a bare ping. | done |
| REQ-000000006 | A crashed pillar is relaunched for up to 10 consecutive tries, and is marked **faulted** (and never retried again automatically) once that limit is exceeded. | done |
| REQ-000000007 | A pillar confirmed healthy after a relaunch has its crash counter reset. | done |
| REQ-000000008 | Core exposes its live pillar registry over its own Unix-domain-socket interface (`/v1/status`), for the CLI (and, later, Dashboard/MCP) to query. | done |
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
| REQ-000000021 | The whole path — Core launching a real `systemd --user` unit, health-probing it over a real socket, and Orchestration dispatching a real Job to it over its Pillar Interface — is proven against a minimal reference pillar, not mocked. | done |

## M2 — Registry & Portfolio (bootstrap path)

| ID | Requirement | Status |
|---|---|---|
| REQ-000000022 | `project.bootstrap` scaffolds the standard docs/src/tests layout, `git init`s on `master`, commits it, and creates a `dev` branch — without depending on `lreq`/`lvx`/`.lvx` (a bootstrapped project is little-workshop's own from birth). | done |
| REQ-000000023 | `project.bootstrap` optionally creates a GitHub repository (via the `gh` CLI or a token-based REST call) and pushes `master`/`dev`, mirroring `bootstrap-little-project`'s two backends — never exercised against the real GitHub API in this repo's own tests. | done |
| REQ-000000024 | A successful bootstrap registers the project in the `projects` table (the authoritative registry) and creates its project-files directory as a consequence of that registration, never the other way around. | done |
| REQ-000000025 | Bootstrapping a name that's already registered fails cleanly (Job outcome `failed`, no partial registry row) rather than corrupting the existing entry. | done |
| REQ-000000026 | `project.remove` hard-deletes the project's `projects` row and its own dependent rows (`project_set_members`, `tickets`) and its project-files directory, immediately, with no soft-delete or undo — leaving the actual git repository on disk untouched. | done |
| REQ-000000027 | `project_sets` supports creating a set, listing sets, and adding/removing project membership. | done |
| REQ-000000028 | `tickets` supports creating a ticket against a project, listing by project, and updating status — the source of record for portfolio tracking even for a project with no GitHub remote. | done |
| REQ-000000029 | Registry & Portfolio's Pillar Interface follows the Job-dispatch convention (`POST /v1/jobs/<type>`) for `project.bootstrap`/`project.remove`, and exposes direct read endpoints for `projects`/`project_sets`/`tickets` that don't need a Job's audit trail. | done |
| REQ-000000030 | The `lws` CLI supports `project bootstrap`/`list`/`remove`; `project list` falls back to a direct database read when the daemon is down, matching the established CLI fallback convention. | done |
| REQ-000000031 | Orchestration dispatching a real `project.bootstrap` Job to Registry & Portfolio running as a genuine `systemd --user` unit produces an actual git repository on disk — proven end-to-end, not mocked. | done |
