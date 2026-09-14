# Architecture

See `docs/design.md` for the concepts behind these components. This document
is the settled design for this round; nothing below is implemented yet (see
`docs/milestones.md`).

## Components

One process (`lws serve`, a `systemd --user` unit — the same primitive
`little-runner` already uses), organized as modules — code boundaries, not
separate deployables:

| Module | Responsibility | Absorbs |
|---|---|---|
| `job_engine` | Job CRUD, the `queue_key` mutex, worker pool (pooled + detached execution modes), the self-pumping queue. The one thing every other module calls into instead of managing its own concurrency. | little-runner's `repo_state.py` queue/slot logic, generalized |
| `pipeline_engine` | Pipeline definitions (YAML), PipelineRun expansion into Jobs, DAG status derivation. | The ecosystem-milestone pattern in root `docs/milestones.md` |
| `policy` | `PolicyRule` storage and evaluation at pre-job/pre-merge/scheduled checkpoints; the "doctor"-style report. | The 8 root policy docs' checkable rules; `little-runner`'s `doctor.py`; `branch_audit.py`; `new-project-checklist`'s manual Step 4 table |
| `requirements` | Project→Suite→Requirement→TestResult hierarchy, draft/published lifecycle, the Use-Case/Milestone axis, Markdown/CSV reporting. | `little-requirements` wholesale, including its own planned-not-built M8 |
| `testing` | Sandboxed test execution (worktree default, Docker optional), JUnit-XML ingestion beyond pytest, on-demand test runs. | `little-requirements`' pytest plugin (kept); its planned E6/M8-9 (built, this time) |
| `versioning` | Gitflow primitives: branch taxonomy, precondition checks, stage/version-suffix modeling, version hooks. | `little-versions` wholesale |
| `build_ci` | `build.run`/`test.run_suite` job types, fork-safety-gated PR testing, on-demand and per-PR triggers. | `little-runner`'s `ci.py`, generalized from "disclaimed non-goal" to a real module |
| `packages` | PEP 503 index + `.vsix` hosting, always-on (no more 5-minute session compromise — the daemon is already persistent). | `little-pypi` wholesale |
| `deployment` | Managed Process lifecycle (start/stop/restart/health), worktree-based zero-downtime deploys, confirm-gated rollback, Tailscale mapping. | `little-runner`'s server-lifecycle mechanics, generalized to cover Claude CLI agent instances too, not only project servers |
| `agentic_dev` | `agent.run_task` Job type: Claude CLI execution, BranchingSpec resolution, result-file → change-report rendering, Remote Control session naming. | `little-runner`'s `issue_flow.py`/`branching.py`/`report.py`/`screenshots.py`/`sessions.py` |
| `project_registry` | The `projects` table (incl. the super-repo container row), `project.bootstrap` pipeline, submodule registration, requirements-DB wiring. | `bootstrap-little-project` wholesale; `little-projects`' informal registry role; `configure_submodules.sh`; the requirements-DB wiring step `bootstrap-little-project`'s own docs list as a deliberate non-goal |
| `tickets` | Ticket records, optional project/milestone/requirement linkage, optional GitHub-issue mirroring. | `little-tickets`' undefined purpose; `docs/milestones.md`'s ecosystem table; ad hoc GitHub issues as today's de facto tracker |
| `adapters/github` | `gh`-backed issue/PR sync, per-project, optional. | `little-runner`'s `github.py` + poller, unchanged mechanism |
| `adapters/tailscale` | `tailscale serve` mapping for the dashboard and any Managed Process. | `little-runner`'s existing pattern, unchanged mechanism, now implemented once |
| `web` | Dashboard (FastAPI/uvicorn), extending `little-runner`'s dark "mission control" design system with new panels (Requirements, Pipelines, Job history, Policy compliance, Project registry, Tickets). | `little-runner`'s `web/` |
| `mcp` | One MCP server process, registered at user scope, namespaced tools per module (`lws_requirement_*`, `lws_version_*`, `lws_test_*`, `lws_job_*`, `lws_pipeline_*`, `lws_policy_*`, `lws_project_*`, `lws_ticket_*`, `lws_package_*`, `lws_server_*`, `lws_agent_*`). | `little-requirements`' and `little-versions`' MCP tool surfaces; closes `little-versions`' own documented gap where its MCP module doesn't actually live in its source tree |
| `cli` | `lws` — argparse front-end, one function per subcommand, no logic of its own (mirrors `little-runner/cli.py`'s own stated pattern). Talks to the daemon over a local socket for anything needing shared state; reads state files/DB directly for pure reads when the daemon isn't running (mirrors `lrun status`'s existing fallback). | `lreq`/`lvx`/`lrun`/`little-pypi`/`bootstrap-little-project` CLIs |

## Data model

One SQLite database, `~/.local/state/little-workshop/workshop.db`, WAL mode.
Table groups (illustrative, not final DDL — schema design is implementation
work, out of scope this round):

- `projects` — registry entry per submodule + the super-repo container row.
- `requirements`, `test_suites`, `test_results`, `use_cases`, `milestones` —
  scoped by `project_id`; supersedes N separate
  `bundle-littles/req_db/<project>_reqs.db` files.
- `jobs`, `job_runs` — the unified job model; supersedes little-runner's
  several state-file directories (`issues/`, `restarts/`, `repos/`,
  `deploys/`, `deploy_confirm/`) as one queryable table set.
- `pipelines`, `pipeline_runs` — DAG templates and their expansions.
- `policy_rules`, `policy_check_results` — seeded from the 8 root policy
  docs; evaluation history.
- `servers`, `deployments`, `deploy_confirmations` — Managed Process state.
- `packages` — metadata for `little-pypi`'s filesystem-backed blob storage
  (the blobs themselves stay on disk; only metadata + session state move
  into the DB).
- `tickets` — project-scoped or ecosystem-wide, with optional GitHub mirror
  state.
- `events` — append-only audit log: every state change, what triggered it.

What stays outside the database, deliberately: git repositories and worktrees
themselves (git remains the actual VCS — nothing here reimplements it),
package blobs, run logs (large text; kept as files under
`~/.local/state/little-workshop/logs/`, exactly little-runner's existing
pattern, referenced from `jobs.log_ref` rather than stored inline), and
screenshots. The git-committed `VERSION` file in each project remains the
single source of truth for that project's version; the `projects` table's
version column is a cached read, never the authority — a fresh clone or a
hand-edited `VERSION` is never contradicted by stale DB state.

## Feature mapping

| Absorbed tool | Becomes | What changes |
|---|---|---|
| `little-requirements` | `requirements` + `testing` modules | Same model (Project→Suite→Requirement→TestResult, draft/published), plus its own planned M8 (Use Cases & Milestones) and E6 (on-demand testing, JUnit-XML beyond pytest) actually built. Data moves from N per-project SQLite files into one DB, `project_id`-scoped. |
| `little-versions` | `versioning` module | Same Gitflow primitives and precondition checks (`LittleVersionsAPI`'s role), called in-process instead of via a subprocess to a sibling CLI. Fixes the documented gap where its MCP surface doesn't live in its own source tree — one MCP server, one source of truth. |
| `little-runner` | `job_engine`, `pipeline_engine`, `agentic_dev`, `deployment`, `build_ci`, `adapters/github`, `adapters/tailscale`, `web` | Its stated non-goals ("not a general CI system," "not an interactive UI [beyond GitHub]") are lifted — testing and the dashboard become real, first-class capabilities instead of disclaimed scope. Its trigger source generalizes from GitHub-issue-only to any Job source. Every safety property (branching control, one-flow-per-project, confirm-gated self-restart, cgroup-aware dispatch) is carried over unchanged. |
| `little-pypi` | `packages` module | Same PEP 503 + `.vsix` model; the 5-minute-session compromise is dropped since the daemon is already persistent — this was only ever a workaround for `little-pypi` being a separate lightweight tool without one. |
| `bootstrap-little-project` | `project_registry` module (`project.bootstrap` pipeline) | Its own two deliberate non-goals — registering the submodule, wiring the requirements DB — stop being non-goals, because both now touch one shared table instead of shared files a single-project-scoped tool couldn't reach. |
| `little-projects` (informal role) | `project_registry` + `policy` modules | `configure_submodules.sh`, `stabilize_branches.py`/`branch_audit.py`, and the hand-maintained `docs/milestones.md` ecosystem table become scheduled/on-demand Jobs and registry rows with full history, instead of scripts whose output lives only in a terminal. The super-repo itself keeps existing as the physical git container — this is not a claim that the container repository goes away, only that its *active* tooling role migrates in. |
| `little-tickets` | `tickets` module | Its own docs currently say "purpose not yet defined" — this is that purpose: the source of record for portfolio/ecosystem tracking that `docs/milestones.md` and ad hoc GitHub issues currently perform by hand, including for projects with no GitHub remote (`little-mapgen`), which can't participate in the issue-driven workflow today. |

## Technical decisions

- **Single SQLite database, no Postgres/Redis/message broker.** At one PC
  and up to two developers, SQLite's WAL mode handles concurrent readers
  fine, and there is no concurrent-write load this system will ever see that
  a single-writer lock can't absorb. One file is also one thing to back up,
  inspect, and move — a real win over today's N separate
  `bundle-littles/req_db/*.db` files, which can't be joined for an
  ecosystem-wide query without custom scripting. This explicitly rejects the
  heavier candidates the standalone FOSS survey turned up (Celery+Redis,
  Postgres-backed ALM platforms) as solving a scale problem this system
  doesn't have.
- **In-process async worker pool, not an external job queue.** The "unified
  job model" the design calls for is an abstraction (one `Job` type per
  operation, one queue-key mutex scheme), not a requirement for a broker.
  RQ/Huey/Celery were genuinely considered (see the prior FOSS research) and
  rejected for the same reason as above: they solve distribution and
  durability-under-broker-crash problems that don't exist when the queue and
  its one consumer live in the same process on the same PC.
- **No multi-tenant auth/RBAC.** Tailscale network identity plus the existing
  actor-allowlist model (owner + configured `allowed_actors`) is the entire
  access-control story — unchanged from what `little-runner` already does,
  now the whole system's model instead of one module's. Two developers means
  "is this one of us," not "what can this role see."
- **Worktree-based deployment, not container-first.** Every existing project
  already deploys as a bare process via `systemd --user`; adopting a
  Docker-first model (the pattern the FOSS survey's Coolify/CapRover
  candidates use) would be a regression for the sole benefit of matching an
  unneeded PaaS convention. Docker stays available as an *opt-in* sandbox
  backend for test isolation, not the default deploy model.
- **One daemon, not microservices.** Modules are code boundaries, not
  separate deployables — appropriate because there is no scaling or
  fault-isolation need at this scale, and a single process is what makes
  "restart itself with a newer implementation of itself" a already-solved
  problem (`little-runner`'s own mechanism, inherited) rather than a
  distributed-coordination problem across N services.
- **CLI as a thin client, not reimplemented logic per surface.** Directly
  carries forward `little-versions`' own stated principle — CLI and MCP sit
  on top of one API layer, never duplicating logic per surface — from one
  module to the whole system.
- **GitHub and Tailscale stay optional, per-project adapters, not assumed
  universal.** This fixes today's actual asymmetry: `little-mapgen` has no
  GitHub remote and is explicitly exempted from `little-runner`'s automation
  as a result. Making the adapter attachment explicit (rather than implicit
  in "every project has a GitHub remote") means a project without one loses
  exactly one feature, not silent breakage.
- **A small number of safety rules are hard-coded, not `PolicyRule`s.**
  "Automation never merges/deletes/finishes on its own initiative" and "the
  agent never touches branches when a job specifies them" are invariants in
  the `job_engine`/`agentic_dev` modules' own code, not entries in the
  user-editable policy table — these are safety properties this ecosystem's
  production use already depends on, not conventions a future edit to the
  policy table should be able to loosen by accident.

## Dependencies

Not finalized — implementation work. Expected to mirror the absorbed tools'
existing choices where they already fit the constraints here: Python ≥3.11
stdlib-first (matching `little-runner`'s and `bootstrap-little-project`'s
existing "no third-party runtime dependency" preference), FastAPI/uvicorn for
the dashboard and MCP-adjacent HTTP surface (matching `little-requirements`'
and `little-runner`'s existing choice), `git`/`gh`/`claude`/`systemctl`/
`systemd-run`/`bazel`/`tailscale` as external, already-required host tools
(unchanged from today).

## Build

Not applicable yet — no `src/` beyond the standard scaffold this round (see
`docs/design.md`'s non-goals). Once implementation begins, this project
follows the root [build policy](../../docs/policies/build_policy.md) like
every other submodule: Bazel, `//:wheel` for the Python package, `//:install`
via `pipx`, `//:test` for the test suite. No `//:vsix`/`//:exe` are
anticipated (no VS Code extension or native binary planned).

## Additional folders

None beyond the standard layout, planned so far. If a dashboard frontend ends
up needing its own build step distinct from the Python package, that would
be called out here when it's actually added — not speculated on now.
