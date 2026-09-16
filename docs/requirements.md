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

## M3 — Version Control & Release

| ID | Requirement | Status |
|---|---|---|
| REQ-000000032 | `version.init` detects or validates production/integration branches and writes `.lvx/config.json`, kept byte-compatible with `little-versions`' existing schema so every already-`lvx`-managed project in this ecosystem needs no migration. | done |
| REQ-000000033 | `feature`/`improvement` start creates `<prefix>/<name>` from the integration branch, refusing on a dirty tree unless forced. | done |
| REQ-000000034 | `feature`/`improvement` finish rebases onto the integration branch, squashes to one commit, merges with `--no-ff`, bumps the patch version, runs version hooks, and deletes the branch — refusing, never auto-resolving, on a real rebase conflict. | done |
| REQ-000000035 | A `feature`/`improvement` finish conflict is resumable: `--continue` (after resolving and staging) completes the merge; `--abort` restores the pre-finish state. Fixed a latent ordering bug found while porting: the saved finish-state file is the newest commit on the branch, so a conflict on any earlier commit means `git rebase --continue` must run *before* the state file is read back, not after. | done |
| REQ-000000036 | `hotfix` start creates `hotfix/<name>` from the production branch; finish merges into production, bumps the patch version, tags it, and back-merges into the integration branch — resumable the same way, via `git merge` rather than rebase (no state-file-ordering issue there). | done |
| REQ-000000037 | `release` start derives the stable version from `VERSION`, creates `release/<version>` from the integration branch. Release-candidate cut/list/promote: cutting tags `v<version>-rc.<n>` (auto-numbered), listing returns them in order, promoting records which RC is the release target. | done |
| REQ-000000038 | `release` finish merges into production (tagging the stable version, refusing if a promoted RC isn't reachable from HEAD — cleanly, not by crashing, for a promoted RC that was never actually cut), bumps the minor version, back-merges into the integration branch, and establishes the next patch line there. | done |
| REQ-000000039 | `support` start creates `support/<version>` from the matching production tag; finish always refuses — support history must never silently diverge from the primary lines, by policy, not by omission. | done |
| REQ-000000040 | `version.config_add_hook`/`remove_hook` manage the registered version-hook scripts (`.sh`/`.py` only); hooks run with `LVX_NEW_VERSION` set on every version bump. | done |
| REQ-000000041 | Every mutating command resolves its `project_id` to a repo path by calling Registry & Portfolio over a real Pillar Interface, its socket discovered through Core's registry — never a hardcoded path — and fails cleanly (never crashes) when Registry & Portfolio is unavailable. | done |
| REQ-000000042 | This pillar keeps no database of its own; git plus the target repo's own `.lvx/config.json`/`state.json` remain the entire store. | done |
| REQ-000000043 | Proven end-to-end: Registry & Portfolio and Version Control & Release both running as genuine `systemd --user` units, with Version resolving the bootstrapped project through Core's registry and running a real feature branch (start, a real commit, rebase, squash, merge, patch bump) via dispatched Orchestration Jobs. | done |

## M4 — Deployment & Servers

| ID | Requirement | Status |
|---|---|---|
| REQ-000000044 | `manifest.deployment.toml`'s `[server]` shape (name/command/port/match/install/timeouts/env/tailscale) is inherited from `little-runner`'s `[server]` config, stored as a project file (owner tag `.deployment`) instead of a shared per-tool config file. | done |
| REQ-000000045 | `deployment.deploy` builds and installs a commitish in a throwaway git worktree — never the project's own checkout — and only stops the currently running version and launches the new one once install succeeds; a bad build/install never takes a running server down. | done |
| REQ-000000046 | `deployment.stop` is defence-in-depth: `systemctl stop` the unit, then `pgrep`/`pkill -TERM` by the configured match string, escalating to `pkill -KILL` if the process hasn't cleared within `stop_timeout_seconds`, then confirms the port is actually free. | done |
| REQ-000000047 | Tailscale exposure (`tailscale serve`, plus an optional named service) runs after a successful deploy and is torn down on stop; absent `[server.tailscale]`, exposure is a no-op. | done |
| REQ-000000048 | Confirm-gating (opt-in via `confirm_timeout_seconds`) arms an independent `systemd --user` timer after a successful deploy; if unconfirmed by the deadline, the timer's own dispatch (`lws deployment rollback-timeout`, calling Deployment & Servers' PI directly, never through Orchestration) redeploys the last confirmed commit. A stale or already-settled watchdog is a clean no-op, never an error. | done |
| REQ-000000049 | `deployment.seed_confirmation` establishes the confirmed baseline from whatever's currently cached as deployed, closing a gap found while porting: the original tool's confirm-gate assumed a baseline already existed but had no operation of its own to establish one from a pillar-tracked state. | done |
| REQ-000000050 | `servers`/`deployments`/`deploy_confirmations` replace `little-runner`'s loose per-server JSON state files with this pillar's own SQLite tables — `deployments` is a full audit trail, not just the latest state. | done |
| REQ-000000051 | Worktree provisioning (`POST /v1/worktrees`, `DELETE /v1/worktrees/{id}`) is exposed as a shared capability other pillars call into, not something the deploy flow keeps to itself — the thing Policy & Compliance's test-script sandbox (M11) and Requirements & Quality's sandboxed test execution (M9) will call rather than each provisioning their own. | done |
| REQ-000000052 | Reflexive self-redeploy (this pillar, or Core, redeploying itself) is explicitly out of scope here — no cgroup-escape/self-restart-safety logic is ported; see M5. | done |
| REQ-000000053 | Proven end-to-end: Registry & Portfolio bootstraps a project and Deployment & Servers (both genuine `systemd --user` units) deploys it into a real, listening process — verified with a real socket probe, not a mock — and a stop genuinely frees the port. | done |

## M5 — Self-redeploy (reflexive)

| ID | Requirement | Status |
|---|---|---|
| REQ-000000054 | `deployment.redeploy_pillar` (`{pillar, commit}`) generalizes the M4 build-before-stop sequence to little-workshop's own pillars: a scratch git worktree of this repo at `commit`, a smoke import check (never the running version is stopped on broken code), a copy into a fresh stable install location, then stop-old/launch-new — the scratch worktree itself is removed immediately after, matching `git worktree remove --force` in docs/design.md's "Planned upgrade" diagram. | done |
| REQ-000000055 | A real `/proc/self/cgroup` check (`_inside_own_cgroup`) detects when a redeploy targets the very pillar executing the request, and dispatches to an independent, detached `systemd-run --collect` unit instead of running inline — the same cgroup-aware escape `little-runner`'s `ServerFlow._inside_unit_cgroup`/`dispatch_restart` already proved, generalized from one project to every pillar. The dispatched worker runs the redeploy directly against this pillar's own database, never through its own (about-to-be-replaced) HTTP endpoint. | done |
| REQ-000000056 | Confirm-gating defaults on for `core` and `deployment` themselves (the two processes everything else depends on) at a 600-second window — the same figure already used elsewhere in this project as the human-override window — and stays opt-in for every other pillar, matching docs/design.md's "Planned upgrade" diagram exactly. | done |
| REQ-000000057 | A pillar self-redeploy's rollback (`rollback_pillar_if_unconfirmed`) is subject to the identical cgroup check and dispatch escape as the forward redeploy — rolling `deployment` back to a confirmed commit also stops and relaunches its own unit. | done |
| REQ-000000058 | A dispatched worker that completes on behalf of an accepted (`outcome: "accepted"`) Job reports back to Orchestration's `/v1/jobs/{id}/complete` callback — the first real exercise of that previously-unused async-Job completion path from M1. | done |
| REQ-000000059 | Three real environment-propagation bugs found and fixed while building the first genuine `systemd-run`-dispatched worker in this codebase: (1) `common.systemd.start()` never guaranteed `~/.local/bin` (where `lws` itself is pipx-installed) was on the dispatched unit's `PATH`; (2) a dispatched worker doesn't inherit the launching process's own environment at all, so `XDG_CONFIG_HOME`/`XDG_STATE_HOME` and the little-workshop repo location need explicit forwarding; (3) the newly-launched pillar unit itself needs the same `XDG_*` forwarding, not just the worker that launches it. | done |
| REQ-000000060 | Proven end-to-end against a genuine `systemd --user` unit: Deployment & Servers, asked to redeploy itself, detects the self-referential case for real, dispatches a real independent unit, which genuinely stops the old unit and launches a new one from a freshly checked-out and smoke-checked commit of this actual repository — verified by a real health probe against the new unit's own socket, and the originating Job settling to `succeeded` via the real completion callback. | done |

## M6 — Agentic Development

| ID | Requirement | Status |
|---|---|---|
| REQ-000000061 | `agent.run_task` resolves a `BranchingSpec` (`new`/`existing`/`auto`) against Version Control & Release's own branch-type policy (`GET /v1/config`, never a direct `.lvx/config.json` read) and rejects a `new`-mode request whose base doesn't match that type's required base, before anything is touched. | done |
| REQ-000000062 | `new`-mode branch creation calls Version Control & Release's own `version.<type>_start` Job directly over its Pillar Interface (a synchronous subroutine call, not routed through Orchestration a second time); `existing`-mode checks out a named branch (local or remote-tracking); `auto`-mode leaves the checkout on the integration branch for the agent to branch itself. | done |
| REQ-000000063 | The agent is hard-forbidden from touching branches whenever `new` or `existing` was specified — enforced by the rendered prompt's own ground rules, restated from `docs/pillars/policy_compliance.md`'s hard-coded invariants. | done |
| REQ-000000064 | In `auto` mode, work the agent left on the integration branch instead of its own is rescued onto a fallback branch and the integration branch is restored to its pre-run state — never silently lost, never silently kept on the shared branch. | done |
| REQ-000000065 | The completion marker (`AGENT_STATUS: done\|partial\|failed`) degrades to `partial` on a missing or unreadable marker, never to `done` — mirrored by the structured result file, which degrades to a report built from the agent's own summary on the same terms. | done |
| REQ-000000066 | The seven-section change report (Background, Goal, Technical decisions, Executed tasks, Screenshot, Test validation results, Pull request) renders every time, regardless of review mode, with a placeholder for anything not reported. | done |
| REQ-000000067 | Two review modes: manual (default) always stops at `awaiting_finish` after publishing; auto-approve additionally finishes immediately once the agent reports `done` with a non-empty recorded test result — refused otherwise, falling back to manual review. `agent.finish_task`/`agent.skip_task` are the explicit actions that settle a manual-review run. | done |
| REQ-000000068 | Concurrency is the same `queue_key` mutex Orchestration already provides (M1) — `lws agent run-task` sets `queue_key=project_id`, so a project never runs two agent sessions at once; nothing new was built for this. | done |
| REQ-000000069 | Because a real agent session can run for up to an hour, `agent.run_task`'s Job handler dispatches the actual work to a background thread and returns `accepted` immediately, reporting completion back to Orchestration afterward — the same async-Job pattern M5's self-redeploy worker uses, now exercised for the reason design.md's Job concept named it for in the first place. | done |
| REQ-000000070 | The GitHub adapter's two named techniques: actor lookup via the issue *timeline* (`GET /repos/{repo}/issues/{number}/events`), not current state; and cursor-file-based edge detection (now `poll_cursors` rows, not loose JSON files) reconstructing labeled/assigned edges a webhook would have delivered, from successive polling snapshots. | done |
| REQ-000000071 | A project with no GitHub remote at all (bootstrapped locally, never pushed) is a legitimate, ordinary state, not an error: branch preparation and publication both skip any git operation that needs `origin` when none is configured. | done |
| REQ-000000072 | Deliberately out of scope, narrower than `little-runner`'s full `issue_flow.py`: GitHub label choreography (`claude:queued`/`done`/`failed`/...) and comment-driven `/finish`/`/skip` slash commands. State lives in this pillar's own `agent_runs` table instead of labels; "a dashboard button, an MCP call" — already named as equally valid explicit-finish triggers by the pillar spec — are what `agent.finish_task`/`agent.skip_task` are for. Interactive `dtach`-based ad-hoc Remote Control sessions (`little-runner`'s `sessions.py`) are also not ported — only the headless `agent.run_task` flow, which needs no PTY. | done |
| REQ-000000073 | Proven end-to-end against genuine `systemd --user` units (Registry & Portfolio, Version Control & Release, Agentic Development): a bootstrapped project carried through a real `agent.run_task` — a real `version.feature_start` call creating a real branch, a real seven-section report, a real async completion callback — with only the agent session itself stubbed (a fake `claude` executable that still honours the exact same result-file/completion-marker contract), never the real, costly, non-deterministic CLI. | done |

## M7 — MCP Server (minimal)

| ID | Requirement | Status |
|---|---|---|
| REQ-000000074 | The MCP Server is a hand-rolled, dependency-free JSON-RPC 2.0 / MCP stdio server (`initialize`, `tools/list`, `tools/call`) — the same proven shape `little_versions.mcp` already uses in this ecosystem, chosen over the `mcp` PyPI SDK so the pillar has no new runtime dependency and no risk of colliding with that package's own top-level `mcp` import name. The source package is named `mcp_server`, not `mcp`, for exactly that reason — Core's pillar registry still calls the pillar itself `"mcp"`. | done |
| REQ-000000075 | Every MCP tool is a thin translation from MCP arguments into a Pillar Interface call — a mutation becomes an Orchestration `POST /v1/jobs` (the same `create_job` convention the CLI already uses), a read becomes a direct `GET` on the owning pillar's socket, resolved through Core's registry (`common.pillar_socket.resolve_pillar_socket`), never hardcoded. The MCP Server holds no domain data of its own, matching Dashboard's identical "thin, stateless proxy" role in `docs/architecture.md`'s Components table. | done |
| REQ-000000076 | Tool coverage as of this milestone: every mutating Job type and read endpoint already built for Registry & Portfolio, Version Control & Release, Deployment & Servers, and Agentic Development (M2/M3/M4/M6) — excluding internal-plumbing entry points (worktree provisioning, the self-redeploy/rollback confirm-timeout watchdog and its detached workers) built for other pillars to call, not for a human or agent session. Tools are namespaced `workshop_<pillar>_<operation>`. | done |
| REQ-000000077 | `mcp_server.client` is a deliberate duplicate of `cli.client`'s HTTP-over-Unix-socket glue, not a shared import — `docs/architecture.md`'s "no shared Python import path between two pillars' `src/` trees" rule, extended here to little-workshop's two thin proxies (CLI and MCP Server) the same way it already applies between every pair of pillars. Unlike `cli.client`, nothing falls back to a direct database/file read: an MCP caller is always a live session, so an unreachable pillar is simply the answer to report back. | done |
| REQ-000000078 | The MCP Server is never launched by Core/systemd — it's spawned on demand by whatever MCP client attaches it (`lws mcp serve`, over stdio), matching every real MCP server this ecosystem already runs. Core's manifest already defaulted `"mcp": False` with no launch `command` since M1, which — unchanged — is exactly the "not Core-supervised" state this pillar needs. | done |
| REQ-000000079 | Registered at **user scope** per the root MCP policy (`docs/policies/mcp_policy.md`), wired into `bazel run //:install`: `claude mcp remove little-workshop` (no forced scope, so a stale entry can't shadow the new one) followed by `claude mcp add little-workshop -s user -- lws mcp serve`; if `claude` isn't on `PATH`, the install prints the equivalent manual command instead of failing. | done |
| REQ-000000080 | Proven end-to-end against a genuine `systemd --user` unit: a real `tools/call` JSON-RPC request, driven through `MCPServer.handle_request` exactly as the stdio loop would, bootstraps a real project via a real Orchestration-dispatched Job to Registry & Portfolio, and a second real `tools/call` lists it back — both discovered through a real Core registry, never hardcoded. | done |

## M8 — Registry & Portfolio (import path)

| ID | Requirement | Status |
|---|---|---|
| REQ-000000081 | `project.import` registers an already-existing, already-checked-out repository exactly the way `project.bootstrap` registers a brand-new one — deliberately mechanical, the same as bootstrap's own registration step: it validates only that there is something real to register (the path exists and is a git repository), never whether the repository actually conforms to policy. That judgment belongs to whoever drives the import (a human Claude Code session, or Agentic Development's own dispatch — the identical MCP entry point either way) and, later, to Policy & Compliance (M11). | done |
| REQ-000000082 | Unlike `project.bootstrap`, `project.import` never scaffolds, `git init`s, commits, or creates a GitHub repo — it only reads the repository's existing `origin` remote (if any) to populate `repo_url`, and never mutates the repository's own history. | done |
| REQ-000000083 | Exposed as `POST /v1/jobs/project.import` on Registry & Portfolio's own Pillar Interface (mirroring `project.bootstrap`'s Job-dispatch convention) and as the `workshop_project_import` MCP tool — the concrete "MCP-exposed entry point" this milestone's own text names. No new Agentic Development machinery was needed: once a project is registered via `project.import`, it's resolvable like any other, and a normal `workshop_agent_run_task` (M6/M7) is how any further "bring it into conformance" work actually gets done. | done |
| REQ-000000084 | Proven end-to-end against a genuine `systemd --user` Registry unit: a real, pre-existing git repository (never touched by `project.bootstrap`) gets registered via a real Orchestration-dispatched Job and, separately, via the real `workshop_project_import` MCP tool — the second proof also reading a real `origin` remote into `repo_url` — with the repository's own commit history provably unchanged by either path. | done |
