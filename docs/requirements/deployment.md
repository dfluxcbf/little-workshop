# Requirements: Deployment & Servers

Part of little-workshop's requirements register; conventions and the other parts are in [../requirements.md](../requirements.md). Sections keep the order and headings of the round that added them.

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

## M5 — Self-redeploy (reflexive)

| ID | Requirement | Status |
|---|---|---|
| REQ-000000054 | `deployment.redeploy_pillar` (`{pillar, commit}`) generalizes the M4 build-before-stop sequence to little-workshop's own pillars: a scratch git worktree of this repo at `commit`, a smoke import check (never the running version is stopped on broken code), a copy into a fresh stable install location, then stop-old/launch-new — the scratch worktree itself is removed immediately after, matching `git worktree remove --force` in docs/design.md's "Planned upgrade" diagram. | done |
| REQ-000000055 | A real `/proc/self/cgroup` check (`_inside_own_cgroup`) detects when a redeploy targets the very pillar executing the request, and dispatches to an independent, detached `systemd-run --collect` unit instead of running inline — the same cgroup-aware escape `little-runner`'s `ServerFlow._inside_unit_cgroup`/`dispatch_restart` already proved, generalized from one project to every pillar. The dispatched worker runs the redeploy directly against this pillar's own database, never through its own (about-to-be-replaced) HTTP endpoint. | done |
| REQ-000000056 | Confirm-gating defaults on for `core` and `deployment` themselves (the two processes everything else depends on) at a 600-second window — the same figure already used elsewhere in this project as the human-override window — and stays opt-in for every other pillar, matching docs/design.md's "Planned upgrade" diagram exactly. | done |
| REQ-000000057 | A pillar self-redeploy's rollback (`rollback_pillar_if_unconfirmed`) is subject to the identical cgroup check and dispatch escape as the forward redeploy — rolling `deployment` back to a confirmed commit also stops and relaunches its own unit. | done |
| REQ-000000059 | Three real environment-propagation bugs found and fixed while building the first genuine `systemd-run`-dispatched worker in this codebase: (1) `common.systemd.start()` never guaranteed `~/.local/bin` (where `lws` itself is pipx-installed) was on the dispatched unit's `PATH`; (2) a dispatched worker doesn't inherit the launching process's own environment at all, so `XDG_CONFIG_HOME`/`XDG_STATE_HOME` and the little-workshop repo location need explicit forwarding; (3) the newly-launched pillar unit itself needs the same `XDG_*` forwarding, not just the worker that launches it. | done |
| REQ-000000060 | Proven end-to-end against a genuine `systemd --user` unit: Deployment & Servers, asked to redeploy itself, detects the self-referential case for real, dispatches a real independent unit, which genuinely stops the old unit and launches a new one from a freshly checked-out and smoke-checked commit of this actual repository — verified by a real health probe against the new unit's own socket, and the originating Job settling to `succeeded` via the real completion callback. | done |

## M10 — Requirements & Quality

| ID | Requirement | Status |
|---|---|---|
| REQ-000000099 | `run_tests` — E6's other half ("on-demand testing") — calls Deployment & Servers' worktree interface (`POST /v1/worktrees` over a real Pillar Interface, discovered through Core, via the new shared `common/worktree_client.py`, never a direct import into `deployment/`'s own tree) to provision an isolated checkout of a given `commitish`, runs the project's real test command (`python3 -m pytest` by default, overridable) inside it with `--junit-xml`, ingests the report through the same `junit.py` parser `import_junit` uses directly, and always releases the worktree again — success or failure — so a failed run never leaks disk. | done |

## M11 — Build & Packages

| ID | Requirement | Status |
|---|---|---|
| REQ-000000104 | `little-runner`'s disclaimed CI non-goal ("not a general CI system") lifted: `build.run` calls Deployment & Servers' worktree interface (the same shared `common/worktree_client.py` Requirements & Quality's `run_tests` uses) to provision an isolated checkout of a given `commitish` and runs the project's own arbitrary build/test command (`command: list[str]`, no assumption of `pytest`/Bazel/npm/etc.) inside it — never the project's own working checkout — recording a `BuildRun` (`id`, `commitish`, `command`, `status`, `exit_code`, combined stdout+stderr `log`, timestamps) in this pillar's own `project_id`-scoped database. | done |

## M12 — Policy & Compliance

| ID | Requirement | Status |
|---|---|---|
| REQ-000000121 | Action-gating extends the Job model with `pending_confirmation` (`orchestration/policy_gate.py`), reusing the status value M1 had already reserved in `orchestration/models.py`/`orchestration/queue.py`. At Job creation, a gated Job type (`GATED_JOB_TOPICS`) gets a `policy.check_project` Job for the topic's current winning policy attached as an ordinary `depends_on` dependency — never a synchronous blocking call. When that dependency resolves with verdict `fail`, the gated Job flips to `pending_confirmation` (never dispatching) and a real, independent `systemd-run --user` transient timer is armed (`--on-active=600s`, matching `little-runner`'s own existing `confirm_timeout_seconds` default), calling back `lws job policy-confirm-timeout <job_id>` — the identical pattern Deployment & Servers' own confirm-gate watchdog (M4) already established, reused rather than a second mechanism invented. `pass`/`not_applicable` dispatch the gated Job normally, no different from any other satisfied dependency. | superseded by REQ-000000474 |
| REQ-000000474 | **A held action waits for the user.** When a gating policy check of a Job fails, the Orchestration pillar shall hold the Job in `pending_confirmation` until the user confirms or cancels it, and shall fail it as unconfirmed after a delay only when Orchestration's environment sets `LWS_POLICY_CONFIRM_TIMEOUT_SECONDS` to a positive number of seconds. | draft, superseded by REQ-000000539 |
| REQ-000000539 | **A held action waits for a confirmation.** When a gating policy check of a Job fails, the Orchestration pillar shall hold the Job in `pending_confirmation` until it is confirmed or cancelled, and shall fail it as unconfirmed after a delay only when a confirmation timeout is configured. The confirmation may come from the user or from the Claude session working on that project (`workshop_policy_confirm` and `lws job policy-confirm` are pre-allowed there); the generated instructions tell the session to fix a failure its branch caused and to confirm one it did not cause, naming the policy in its report. | done |
| REQ-000000475 | **A newer request supersedes a held action.** When a Job of a gated type is created for a project, the Orchestration pillar shall cancel every Job of the same type held in `pending_confirmation` for the same project and branch, recording that it was superseded by the new Job, and shall fail the Jobs that depend on it. | draft |

## M17 — ecosystem cutover begins: `little-mindcheck` as pilot (2026-09-17)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000189 | **A real, previously-undiscovered gap found by actually attempting a real deploy**: nothing in Deployment & Servers ever exposed a way to write a project's own `manifest.deployment.toml` — `_load_manifest` could read one, but no Job, CLI command, or MCP tool could ever create one for a *real* project (every existing test wrote it directly via `ProjectFilesClient`, bypassing the gap entirely). Closed as `deployment.set_manifest` (validates via `parse_manifest` before writing, so a malformed manifest is rejected outright) plus `GET /v1/manifest/{project_id}`, exposed as `lws deployment set-manifest --project-id --file` and a `workshop_deployment_set_manifest` MCP tool. | done |

## System health, automatic bug tasks, coverage policy, data catalog (2026-09-22)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000230 | **Any project's server can report an incident.** Where a project's own server is instrumented with the vendored incident reporter (`tools/incident_reporter.py`, copied into the project's own tree per this project's "no shared import across repos" convention), an unhandled exception in that server shall be reported to little-workshop the same way as one of little-workshop's own pillars. Delivered as a tested, ready-to-adopt module and its own doc section; not yet wired into every child project's own repository in this pass (each is its own commit, in that project's own repo) — little-meals and little-home are the natural first adopters, being the actively maintained ones. | done |

## Dashboard page-load sweep: Pillars page fixed (2026-09-23)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000247 | **The Pillars page reads recent commits' versions in one process, not one per commit.** Where the Pillars page lists the repo's recent commits with each one's `VERSION` file contents, Deployment & Servers shall read all of them via one `git cat-file --batch` call, never one `git show <sha>:VERSION` subprocess per commit. Root cause, found by timing every Dashboard route: `recent_commits(limit=10)` spawned 11 git subprocesses (1 `git log` + 10 `git show`) on every `/pillars` load — each spawn's real OS overhead made the page take 1.0-1.6s measured live, the slowest page on the site after the already-fixed home page. Verified: the same 10-commit lookup dropped from consuming the bulk of that time to 0.007s once batched into a single `git cat-file --batch` call. | done |

## A self-redeploy refreshes lws (2026-09-24)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000272 | **A self-redeploy refreshes lws.** When the Deployment pillar redeploys one of little-workshop's own pillars to a commit whose version differs from the installed lws, the pillar shall upgrade lws in place to that commit and shall report the outcome without failing the redeploy. | done |

## lws never moves backwards (2026-09-24)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000273 | **lws never moves backwards.** When the Deployment pillar rolls a pillar back or redeploys a pillar to a version not newer than the installed lws, the pillar shall leave lws unchanged. | done |

## Policies as the hard layer of Claude instructions (2026-10-02)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000314 | **Pillars are redeployed in batches ordered by their dependencies.** When several of little-workshop's own pillars are redeployed together, the Deployment & Servers pillar shall split them into batches by the redeploy order rules, redeploy the pillars of a batch side by side, start a batch only after every redeploy of the previous batch has succeeded, and use a single batch when the chosen pillars have no rule between them. | draft |
| REQ-000000476 | **The automatic pillar redeploy waits until the workshop is idle.** While a Job that is not a pillar redeploy is running or is queued without waiting behind a held Job, the Orchestration pillar shall keep the redeploy of changed pillars queued. | draft |
| REQ-000000477 | **Orchestration is redeployed last, never mid-dispatch.** When several pillars are redeployed together, the Deployment & Servers pillar shall redeploy the Orchestration pillar after every other chosen pillar except Core; while any other Job is being dispatched, the Orchestration pillar shall keep the redeploy of the Orchestration pillar queued. | draft |

## Claude usage, automation: mining and workshop triggers (M21, 2026-10-04)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000345 | **The pillars a finished branch changed are redeployed by one Job.** When the `deployment.redeploy_changed_pillars` Job runs, the Deployment & Servers pillar shall compare the commit (HEAD when none is given) with its first parent or the given base, shall take the live pillars whose `src/<pillar>` code changed and every live pillar when shared code changed, shall create one confirm-gated `deployment.redeploy_pillar` Job per pillar in dependency order, and shall redeploy nothing when no pillar's code changed; and the Orchestration pillar shall offer little-workshop's own triggers for installation once: redeploy the changed pillars and report differing Claude files after a finished branch on little-workshop, and ingest transcripts when a Claude session ends. | draft |

## Pillar crashes and the work they cut (2026-10-05)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000404 | **A pillar asked to stop leaves within a minute.** When a pillar process is asked to stop, the pillar shall end its long-polls and live streams at once, give the requests still running up to 60 seconds and then cut them, run its shutdown hooks and exit on its own instead of waiting for systemd to kill it. | draft |

## Windows-drive projects as an explicit host (2026-11-07)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000515 | **A Windows-hosted server runs from a kept deploy folder, tracked by PID and start time.** When a Windows-hosted project is deployed, the Deployment & Servers pillar shall check the commit out with git.exe into `<root>\<project>\<commit>` (root `C:\little-workshop\deploy`, or `LWS_WINDOWS_DEPLOY_ROOT`), install and launch it with that folder as its working directory, keep the newest two such folders of the project and remove older ones; and it shall record the launched process's PID with its start time and working directory, treat a PID whose process has another start time as dead, and relaunch a crashed server from the same folder. | draft |

## Phase 6: closing the gaps (2026-11-07)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000521 | **A deploy's health probe confirms or rolls back on its own.** Where a project's deployment manifest has a `[server.health]` table (`url` or `port`, `timeout_seconds`, `interval_seconds`), when a deploy has launched the server, the Deployment & Servers pillar shall probe it (an HTTP GET answered below 400, or a port accepting a connection, from WSL also for a Windows-hosted server) until it is healthy or the timeout passes; healthy, it shall confirm a pending deploy, or record the deploy as the confirmed baseline when the server is not confirm-gated; unhealthy, it shall roll back at once to the confirmed deploy through the confirm gate's rollback and fail the deploy Job saying so, or, with no confirmed deploy, fail it saying there is nothing to roll back to. Without `[server.health]` the deploy shall behave as before. | draft |
