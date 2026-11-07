# Requirements: Agentic Development

Part of little-workshop's requirements register; conventions and the other parts are in [../requirements.md](../requirements.md). Sections keep the order and headings of the round that added them.

## M6 — Agentic Development

| ID | Requirement | Status |
|---|---|---|
| REQ-000000063 | The agent is hard-forbidden from touching branches whenever `new` or `existing` was specified — enforced by the rendered prompt's own ground rules, restated from `docs/pillars/policy_compliance.md`'s hard-coded invariants. | done |
| REQ-000000064 | In `auto` mode, work the agent left on the integration branch instead of its own is rescued onto a fallback branch and the integration branch is restored to its pre-run state — never silently lost, never silently kept on the shared branch. | done |
| REQ-000000065 | The completion marker (`AGENT_STATUS: done\|partial\|failed`) degrades to `partial` on a missing or unreadable marker, never to `done` — mirrored by the structured result file, which degrades to a report built from the agent's own summary on the same terms. | done |
| REQ-000000066 | The seven-section change report (Background, Goal, Technical decisions, Executed tasks, Screenshot, Test validation results, Pull request) renders every time, regardless of review mode, with a placeholder for anything not reported. | done |
| REQ-000000067 | Two review modes: manual (default) always stops at `awaiting_finish` after publishing; auto-approve additionally finishes immediately once the agent reports `done` with a non-empty recorded test result — refused otherwise, falling back to manual review. `agent.finish_task`/`agent.skip_task` are the explicit actions that settle a manual-review run. | done |
| REQ-000000070 | The GitHub adapter's two named techniques: actor lookup via the issue *timeline* (`GET /repos/{repo}/issues/{number}/events`), not current state; and cursor-file-based edge detection (now `poll_cursors` rows, not loose JSON files) reconstructing labeled/assigned edges a webhook would have delivered, from successive polling snapshots. | done |
| REQ-000000071 | A project with no GitHub remote at all (bootstrapped locally, never pushed) is a legitimate, ordinary state, not an error: branch preparation and publication both skip any git operation that needs `origin` when none is configured. | done |
| REQ-000000072 | Deliberately out of scope, narrower than `little-runner`'s full `issue_flow.py`: GitHub label choreography (`claude:queued`/`done`/`failed`/...) and comment-driven `/finish`/`/skip` slash commands. State lives in this pillar's own `agent_runs` table instead of labels; "a dashboard button, an MCP call" — already named as equally valid explicit-finish triggers by the pillar spec — are what `agent.finish_task`/`agent.skip_task` are for. Interactive `dtach`-based ad-hoc Remote Control sessions (`little-runner`'s `sessions.py`) are also not ported — only the headless `agent.run_task` flow, which needs no PTY. | done |

## M8 — Registry & Portfolio (import path)

| ID | Requirement | Status |
|---|---|---|
| REQ-000000081 | `project.import` registers an already-existing, already-checked-out repository exactly the way `project.bootstrap` registers a brand-new one — deliberately mechanical, the same as bootstrap's own registration step: it validates only that there is something real to register (the path exists and is a git repository), never whether the repository actually conforms to policy. That judgment belongs to whoever drives the import (a human Claude Code session, or Agentic Development's own dispatch — the identical MCP entry point either way) and, later, to Policy & Compliance (M12). | done |

## M12 — Policy & Compliance

| ID | Requirement | Status |
|---|---|---|
| REQ-000000116 | The four-gate authoring pipeline as two Job types: `policy.author` (Gates 1-3 plus test-script drafting — EARS-compliance via a new, narrowly-scoped `agent.query` Job type on Agentic Development, mechanical-enforceability and semantic-conflict checks the same way, then script generation — never saves anything, always returns a draft) and `policy.activate` (Gate 4: the *only* path that ever writes a `policies` row, requiring an explicit `approved: true` — refused otherwise). Editing a policy re-runs the identical pipeline, excluding the policy's own prior version from the Gate 3/3b checks against itself. | done |
| REQ-000000117 | `agent.query` (`src/agent/query.py`, a new Job type on Agentic Development, not a reuse of `agent.run_task`): a narrowly-scoped, text-in/text-out Claude CLI call — no project resolution, no checkout, no branch, no commit, no Remote Control session, run in a fresh scratch directory. A deliberate, documented design choice: `agent.run_task`'s existing flow always resolves a branching spec and prepares a real branch, which cannot satisfy the specification's own "no file writes, no branch, no side effect of any kind" requirement for EARS refinement/conflict-checking/script-drafting calls — reusing it as specified would have contradicted the specification's own next paragraph. Still reached only through Agentic Development's own Pillar Interface, never reimplemented elsewhere. | done |
| REQ-000000128 | `docs/open_questions.md`'s `OQ-12` (does Agentic Development's review mode compose with action-gating on the same Job) answered by the actual implementation's own shape: interpretation (a) — gating always resolves first, regardless of review mode. `orchestration/policy_gate.py`'s gate check lives entirely inside Orchestration's Job-creation/dispatch path with zero awareness of `review_mode`; auto-approve governs only Agentic Development's own report/test-result check and never reaches into, or is consulted by, the policy gate. | done |

## M15 — Self-hosting / dogfooding

| ID | Requirement | Status |
|---|---|---|
| REQ-000000151 | **A real, previously-invisible defect found by using the real Claude CLI instead of the fake stub** (the same category of finding as REQ-000000132): `orchestration/dispatch.py`'s `dispatch_job` hardcodes a 20-second timeout with no way for a caller to widen it, and nothing forwards a per-request override through `POST /v1/jobs`/`dispatch_ready_jobs`. Invisible in every existing test (the fake `claude` stub responds in milliseconds) but real against the actual API: one `policy.author` attempt succeeded, two identical resubmissions timed out. Logged as `docs/open_questions.md`'s `OQ-14` rather than silently patched — widening a load-bearing default without review risks masking a genuinely-down pillar behind a longer wait, a design tradeoff for a maintainer to weigh in on, not a mechanical fix. | done |

## M16 follow-up — parity survey and rejecting compatibility shims (2026-09-17)

| ID | Requirement | Status |
|---|---|---|
| REQ-000000160 | **Compatibility shims rejected, by direct decision — `docs/milestones.md` M16 and `docs/architecture.md`'s CLI section both corrected in place** (not append-only ledger entries; both are living design docs, corrected as understanding improves, per this project's own documentation discipline). The five absorbed tools (`lreq`, `lvx`, `lrun`, `little-pypi`, `bootstrap-little-project`) are never made to forward into `lws`, and `lws` never calls into them — each keeps running its own original, unmodified code for the entire transition, no shared code path. A project's "cutover" (M17) means its owner actually switches what they invoke, never a transparent swap underneath an unchanged command name. The five old tools are uninstalled only once every milestone is done, a first release exists, and their owner has personally validated `lws` through real use — a manual, confidence-gated decision, explicitly never an automated milestone step (the same reasoning that removed the original M18 "retire the absorbed tools" from the plan entirely, at this project's direct request). | done |
| REQ-000000161 | **A real, command-by-command parity survey**, comparing each of the five tools' actual CLI surface (~90 subcommands total, read directly from each tool's own `cli.py`/flow modules, not from memory or documentation) against what little-workshop's own pillars implement today. Findings, tool by tool: `little-versions` is closest to full parity (one gap — standalone `issue list/set/unset`, `version` currently only takes `issue=` as a one-time start-time parameter); `little-pypi`'s storage half (`publish`/`list`/`delete`) matches, only dev-convenience commands (`pip`, `print-pip-config`, `write-pip-config`, `print-vsix-urls`) are missing; `bootstrap-little-project` needs three real features added to `project.bootstrap` (GitHub-automation init — `.github/workflows`, `.lrun/config.toml` — `lvx init`, and GitHub label creation) before it's honest; `little-requirements` has no equivalent at all for its client-side `workspace`/`.reqs` config model, file-level `lock`/`unlock`, manual verdict-setting, or export/import; `little-runner` has no equivalent for GitHub-issue-triggered polling, per-repo issue queues, session management, branch stabilization, or cross-project branch auditing, and is the one tool every other real project in this ecosystem currently depends on for actual CI. Full detail kept as a published reference (an HTML ledger, not duplicated into this file) rather than restated here. | done |
| REQ-000000162 | **Parity survey gaps closed or dropped by decision.** Every gap found by REQ-000000161 is either closed by a later requirement or dropped by explicit decision (per-project `.lrun/config.toml`, the `little-requirements` workspace/project/lock model, GitHub tracking-issue sync); none remains open. | done |
| REQ-000000167 | Proven at the unit level: `test_build_print_pip_config`, `test_build_write_pip_config` (real file write, real content), `test_build_pip_shells_to_real_pip_with_the_extra_index` (the real `subprocess.run` call args, `subprocess.run` itself stubbed rather than actually invoking `pip`), `test_build_print_vsix_urls_success`/`test_build_print_vsix_urls_honors_explicit_base_url`, all in `tests/unit/test_cli_success_paths.py`. No new Job type or Pillar Interface route exists for any of the four, so no separate app-level or integration proof is needed beyond the existing `build.list_packages`/`GET /v1/packages` coverage the fourth command already reuses. | done |
| REQ-000000169 | **Documented, deliberate scope reduction**: `.lrun/config.toml` (the old tool's per-project config, host-local under `~/.config/little-runner/projects/`, never committed to the project's own repo) is left un-migrated. It has no clear pillar owner (it configures a GitHub-issue-polling loop with no equivalent anywhere in little-workshop yet — the same gap `little-runner`'s own still-open M16 entry tracks) and no clear future pillar-equivalent to write it for, unlike the version-tracking and label gaps, which had an obvious owning pillar and format already in place. Forcing it into `project.bootstrap` now would mean guessing at a shape this project doesn't own yet. | removed 2026-09-21 — dropped by decision (`.lrun/config.toml` migration is out of scope) |
| REQ-000000170 | **`little-requirements`' real CLI-surface gaps, closed** — four new `lws requirements` subcommands, all pure CLI-side additions (`src/cli/main.py` only, no new Pillar Interface route): `export` (writes `requirement_export.md`/`.csv`/`.json`, optionally scoped to one suite, sorted by title or key) and `import` (bulk create/update from a JSON `{"list": [...]}` payload, matching existing requirements by their human-facing `key` rather than a server-generated `id` since little-workshop generates IDs itself, rejecting an attempt to edit a published requirement) match the old tool's `requirement export`/`import`. `report --md/--csv/--json-file [--suite/--requirement] [--path]` extends the existing JSON-to-stdout `report` command with the old tool's own file-writing formats, without changing its no-flags default (still a bare JSON print, so the one existing test asserting that default keeps passing). `test-structure-create` matches `test-structure create` (`tests/<suite>/__init__.py` scaffolding, a confirmation prompt unless `--force`). One old-tool command, `set-verdict`, needed no new code at all: `requirements.record_test_result` already writes `source="manual"`, so `lws requirements result record` already has full functional parity with it. | done |

## M16 follow-up — `little-runner`, the last absorbed tool (2026-09-17)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000173 | **Correction to REQ-000000161's own earlier survey finding**: "`little-runner` has no equivalent for GitHub-issue-triggered polling, per-repo issue queues, session management, [or] cross-project branch auditing" undercounted by the time this pass ran — `docs/pillars/agentic_development.md`'s own poller (`src/agent/poller.py`), GitHub adapter (`github_adapter.py`), and session-tracking table (`db.py`'s `remote_control_sessions`) already gave `lrun issue run/finish` and the *actor-lookup-and-dispatch* half of `lrun session start/stop/list` full functional parity — it had simply never been checked off explicitly against the old tool's own CLI surface. Corrected here rather than left standing, per this project's own "corrected forward, not rewritten" documentation discipline. | done |
| REQ-000000174 | **`lrun ci run/status`, closed, as `agent.ci_check`** — a new Job type on Agentic Development: given a `pr_number` and `suite_id`, resolves the pull request's real head via a new `GitHubAdapter.pull_request` (`gh pr view`), posts a `pending` GitHub commit status via a new `GitHubAdapter.post_status`, calls Requirements & Quality's own `requirements.run_tests` directly against that head sha (a new `agent/requirements_client.py`, mirroring `agent/version_client.py`'s already-established synchronous-cross-pillar-call pattern for a second pillar), and posts the final `success`/`failure`/`error` status back. A status is reporting, never the job itself (`GitHubAdapter.post_status`'s own docstring) — a real test failure is a `failure` status on an otherwise-`succeeded` Job; only a genuine infra failure (no GitHub remote, an unreadable PR, Requirements & Quality unreachable) fails the Job itself. Recorded in a new `ci_checks` table (`GET /v1/ci_checks/{project_id}`, `.../latest`), exposed as `lws agent ci-check/ci-list/ci-status` and matching `workshop_agent_ci_check/ci_list/ci_status` MCP tools, queued on the same `queue_key=project_id` mutex `agent.run_task` already uses (both share the same worktree-provisioning path). | done |
| REQ-000000176 | **Claude Code session start/stop/list per project** — the only part of the earlier deferral that stays in scope; implemented as REQ-000000185 describes. `stabilize` and `branch-audit` are covered by `version.stabilize` and `version.branch_audit`, and the rest is dropped by decision. | done |

## M16 follow-up — `little-runner`'s branch audit (2026-09-17)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000179 | **Documented, deliberate scope reduction**: the old tool's GitHub tracking-issue sync (opening, updating, and closing a persistent `branch-hygiene`-labeled issue per repo once violations are found) is left un-ported. This Job is read-only by design; automating a GitHub side effect (issue creation) from a read-only audit is a separate decision from closing the classification gap itself, and wasn't bundled in. | removed 2026-09-21 — dropped by decision (GitHub tracking-issue sync for branch hygiene is out of scope) |

## M16 follow-up — `little-runner`'s `stabilize run`, the last real gap (2026-09-17)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000181 | **Correction to REQ-000000176's own earlier description**: "`lrun stabilize run` (repeatedly dispatching an agent fix task until a project's CI goes green, or a budget is exhausted)" was wrong — the real tool's own docstring says outright what it does: commit pending changes, bring the integration branch up to date with `origin`, finish the currently-checked-out feature/improvement/hotfix branch, push, and delete already-merged branches. No agent, no CI loop, no budget. Corrected here rather than left standing, per this project's own "corrected forward, not rewritten" documentation discipline (the same treatment REQ-000000173 already gave a different undercounted survey finding). | done |
| REQ-000000183 | **Documented, deliberate scope reduction**: this pillar had never pushed to a remote before this Job — every other Job type deals in local git state only, leaving publishing to whichever caller owns that concern (Agentic Development's own `_publish`, for its `agent.run_task` flow). `version.stabilize` is a first, deliberate exception, scoped narrowly to this one Job and gated behind its own explicit `execute` flag — not a general new pushing capability for the pillar. | done |

## M16 follow-up — `little-runner`'s last item: ad-hoc sessions (2026-09-17)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000185 | **`lrun session start/stop/list`, closed, reversing REQ-000000176's own earlier deferral** — as `agent.session_start`/`agent.session_stop` (`agent/session_flow.py`) plus `GET /v1/sessions/{project_id}` on Agentic Development. The deferral's own reasoning ("host-process machinery ... with no clean equivalent in this daemon's own Job/Pillar-Interface model") didn't hold up once actually attempted: Deployment & Servers already launches and tracks long-running background processes started once and stopped later, and M5's self-redeploy worker already established the `systemd-run --user --collect` detached-dispatch pattern this reuses — the genuinely new piece was only the PTY itself, since `agent.run_task`'s own Remote Control usage is headless (`claude -p`). `dtach` supplies that PTY and keeps a session alive independent of whatever launched it; a transient `systemd --user` *scope* (not a *service*) tracks cgroup population rather than one main PID, so it survives this pillar's own unit restarting — `little-runner`'s own hard-earned finding, ported directly, that a plain `dtach -n` fork still inherits its launcher's cgroup despite reparenting in the process tree. Exactly one session per project (`Paths.session_socket_path`'s deterministic socket path, a new `Paths` addition); refused outright if one is already running or if the project's checkout isn't yet trusted by Claude Code (`~/.claude.json`'s own trust-dialog state, read through a new `LITTLE_WORKSHOP_CLAUDE_CONFIG_PATH` override that exists purely so a test launching this pillar as a real separate `systemd --user` unit can fake it without ever touching a developer's own real trust state). Exposed as `lws agent session-start/session-stop --project-id` and `lws agent session-list` (looping `GET /v1/sessions/{project_id}` over Registry & Portfolio's own project list, the same pattern `version.branch_audit`/`version.stabilize --all` already established), plus matching `workshop_agent_session_start/stop/status` MCP tools. | done |

## Dashboard redesign: mission control for projects (2026-09-21)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000196 | **A real defect found while building it**: Agentic Development's read endpoints (`/v1/runs`, `/v1/ci_checks`) took the same lock `agent.run_task`'s worker holds for the entire run (up to an hour), so the Dashboard's task and CI panels would time out and mark the pillar unreachable for exactly as long as it was busiest. Reads now use a short-lived read-only connection and never queue behind a run. | done |

## Workbench usability: ordering, workspace session, reporting, bootstrap, scheduling (2026-09-21)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000201 | **Workspace-level Claude session.** When the user starts the workspace session, the Agentic Development pillar shall launch one Claude Code session in the projects root directory, outside any single project (`common/workspace.py`, the reserved project id `workspace`). | done |

## System health, automatic bug tasks, coverage policy, data catalog (2026-09-22)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000225 | **An unhandled exception becomes an open incident.** When code instrumented with the incident reporter raises an unhandled exception, the Agentic Development pillar shall record an open incident carrying the project, the raising component, the exception and its traceback, unless that code is marked to suppress it. `common/incident.py` (little-workshop's own 10 pillars) and `tools/incident_reporter.py` (a vendorable, dependency-light copy for any child project's own app) both report to a new `POST /v1/incidents` on Agentic Development — deliberately not a Job through Orchestration, so a crash reporter survives Orchestration itself being unreachable. | done |
| REQ-000000227 | **Confirming an incident starts a bug task.** When the user confirms an open incident, the Agentic Development pillar shall start a bug-intent task (`agent.run_task`'s existing `intent=bug`) on the incident's own project, using the review mode the user chose (auto-approve or manual, defaulting to auto-approve), and shall link the incident to that task. `agent.confirm_incident` reuses `agent.run_task`'s own dispatch path directly. | done |
| REQ-000000228 | **Dismissing an incident discards it.** When the user dismisses an open incident, the Agentic Development pillar shall mark it dismissed and shall never create a task for it. | done |

## Toolbar and task/job/policy workflow overhaul (2026-09-23)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000234 | **Task list shows tasks waiting to execute.** Where an agent task has been submitted but not yet started, the Task list shall show it as waiting, alongside tasks that are running or have already finished. | done |

## Playwright as standard: MCP for agents, UI tests run against the app (2026-09-24)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000254 | **The agent is told to verify UI/UX in a browser and cover it with Playwright tests.** When Agentic Development renders an agent run's prompt, the prompt shall instruct the agent, for any UI/UX change, to check it in a real browser through the Playwright MCP tools (desktop and phone viewports) and to cover it with pytest-playwright tests under `tests/ui/` using relative URLs; when the server is not attached, the prompt shall still ask for the tests and ask the agent to report that the UI was not checked in a browser. | done |

## Comprehensive Task list (2026-09-24)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000257 | **Task list shows tasks that have a Job but no run yet.** Where an `agent.run_task` Job has no Agentic Development run, the Task list shall still show it — as Queued while the Job is queued, awaiting confirmation, or dispatched but waiting for the agent's one-task-at-a-time lock, and under Needs attention with the Job's own error when it failed or was stopped before a run was created; a Job that already has a run shall be shown once, as that run. | done |

## REQ-NONE for commits that affect no requirement (2026-09-24)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000267 | **Agent commits cite requirements or REQ-NONE.** When the Agentic Development pillar starts an agent run, the pillar shall instruct the agent to cite the affected requirement ids in each commit message, or REQ-NONE when the commit affects no requirement, and never both. | done, superseded by REQ-000000501 |
| REQ-000000501 | **Agent commits cite requirements in full-governance projects.** When the Agentic Development pillar starts an agent run for a project of `full` governance, the pillar shall instruct the agent to cite the affected requirement ids in each commit message, or REQ-NONE when the commit affects no requirement, and never both; for a project of `light` governance, the run's prompt shall ask for no requirement ids. | draft |

## Standards copy for tasks (2026-10-01)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000286 | **A task gets a copy of the shared project standards.** When an agent.run_task run is not told otherwise, the Agent pillar shall give the run a copy of little-workshop's `shared_project_standards/` holding every Markdown file at its root and the Markdown files of that project's own folder (`<project id with hyphens as underscores>_standards`), and no other file, name the copy in the prompt as read-only reference material, and leave the original untouched. | superseded by REQ-000000479 |
| REQ-000000479 | **A task about documentation or standards gets a copy of the shared project standards.** When an agent.run_task run's intent is docs, or its title or description mentions standards, and the run is not told otherwise, the Agent pillar shall give the run a copy of little-workshop's `shared_project_standards/` holding every Markdown file at its root and the Markdown files of that project's own folder (`<project id with hyphens as underscores>_standards`), and no other file, name the copy in the prompt as read-only reference material, and leave the original untouched; any other run shall get neither the copy nor the standards section of the prompt. | done |
| REQ-000000287 | **The standards copy is temporary and deleted when the task completes.** When an agent.run_task run ends by any outcome, the Agent pillar shall delete the temporary directory that holds its copy of the shared project standards. | published |
| REQ-000000289 | **A missing standards source never fails a task.** If the shared_project_standards folder of little-workshop cannot be found or copied, then the Agent pillar shall run the task without the copy instead of failing it. | published |

## Claude-native policy enforcement (2026-10-02)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000304 | **little-workshop sessions cannot run tests/integration beside the live install.** When a Claude Code session in little-workshop runs a Bash command that would collect tests/integration, the live-install guard hook shall deny it and name tests/unit as the alternative; such commands are pytest with no path or with the repository root, tests/ or tests/integration, bazel test //:test, //:coverage or //..., and the scripts behind them. | draft |

## Policies as the hard layer of Claude instructions (2026-10-02)

| ID | Description | Status |
| --- | --- | --- |
| — | **Generated Claude files are rewritten in place.** When tools/sync_claude_files.py runs with --write against a project checkout, it shall write every generated Claude file and merge the generated permission table and guard hook into .claude/settings.json, keeping every permission entry, hook and setting the project added; without --write it shall only list what differs. | draft |

## Remote Control update-doc sessions start on the branch's tip (2026-10-03)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000319 | **A Remote Control update-doc session reaches the branch's tip with a command the project allows.** When an update-doc Job starts a Remote Control session, the Agentic Development pillar shall tell the session to move its worktree to the branch's latest commit with `git switch --detach <commit>`, a command that none of the deny rules little-workshop generates for a project refuses, and shall publish the commits the session makes on that detached worktree to the branch. | draft |

## Claude usage, quick wins (M18, 2026-10-04)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000320 | **Claude runs record the usage they report.** When a Claude run started by the Agentic Development pillar returns its result JSON, the Agentic Development pillar shall store the usage that JSON reports (the input, cache-read, cache-write and output tokens, the estimated cost, the turns, the duration, the model and the session id) with the kind of run (a task run, a query or a headless documentation update), its project and its Job, whether the run succeeded or failed, and shall return the stored usage with a task run's status and through a read of its own. | draft |
| REQ-000000321 | **agent.query runs where its prompt cache can be reused.** When the Agentic Development pillar runs an `agent.query`, the Agentic Development pillar shall start Claude in one working directory that stays the same from call to call, under little-workshop's state home, and with a 5-minute prompt-cache lifetime (`CLAUDE_CODE_PROMPT_CACHE_TTL=5m`), so that consecutive queries share a cache and a single-request call does not pay for a 1-hour cache write. | draft |
| REQ-000000323 | **Generated Claude files route waiting and multi-pillar redeploys.** The Claude files little-workshop generates for a project shall allow `workshop_job_wait` wherever `workshop_job_status` is allowed and shall tell a session, in the `workshop-lifecycle` skill, to wait for a Job with `workshop_job_wait` instead of polling its status or sleeping; for little-workshop's own project the skill shall also say that several pillars are redeployed with one `workshop_deployment_redeploy_pillars` call. | draft |

## Claude usage, collection: the Usage & Efficiency pillar (M19, 2026-10-04)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000331 | **Runs little-workshop starts report with their project, Job and run.** When the Agentic Development pillar starts Claude while the Usage & Efficiency pillar is reachable, the Agentic Development pillar shall add to the environment of the task run, query, documentation update or project session the telemetry variables that pillar hands out and resource attributes naming the project, the Job, the job type and the run, and shall start the run unchanged when that pillar is switched off or unreachable. | draft |
| REQ-000000333 | **Run results stored by Agentic Development reach the usage store.** While the Usage & Efficiency pillar runs, the Usage & Efficiency pillar shall every ten minutes read the run usage the Agentic Development pillar stored since its last read and keep each row once, rebuild the rollups, and leave its store as it was when Agentic Development or the Registry cannot be reached. | draft |

## Claude usage, findings (M20, 2026-10-04)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000339 | **Generated Claude files keep heavy folders, test output and policy reading out of the context.** The Claude files little-workshop generates for a project shall deny reading the heavy generated folders of the project's template with `Read(...)` rules, shall enable the code-intelligence plugin of each language the project holds, shall register after the scope guard a hook that sends a plain test command through a filter which prints short output whole and reduces long output to the failing lines with their context and the end of the run while keeping the command's exit status and all of the output in a file, shall include a policies-digest skill whose script prints the policies attached to the project with their gate points and rules, and shall carry no telemetry variables; the sync shall keep the project's own plugins and deny rules; and the policy `claude-config-present` shall fail a project that lacks the skill, the registered filter or any `Read(...)` deny rule. | draft |

## Claude usage, token-aware operation (M22, 2026-10-04)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000348 | **Each kind of Claude run has its own model, effort and budget.** When the Agentic Development pillar starts Claude, the Agentic Development pillar shall take the model, the effort and the budget of the run's job type (task run, query, documentation update or project session) from a table of defaults that a file in its configuration directory can override per job type, shall pass them to Claude with a query bounded by a budget by default, shall let a task run's own model and budget take precedence, and shall store with the run's usage the budget it was given so that the share it used can be shown. | draft |

## Stuck jobs, force stop and incidents (2026-10-04)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000363 | **An unknown intent is refused before a run starts.** When an `agent.run_task` Job names an intent the Agentic Development pillar has no prompt for, the pillar shall fail the Job at once with an error naming the intents it knows, and shall create no run. | draft |
| REQ-000000364 | **A crashed task fails its run and its Job.** If an exception escapes the worker of an `agent.run_task` run, then the Agentic Development pillar shall mark the run failed with the exception, report the Job failed to Orchestration and record an incident, so that no run or Job is left running without a worker. | draft |

## Live pages and Claude peek (2026-10-04)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000380 | **Claude can be watched while it works.** While Claude works on an `agent.run_task` run or an update-doc job, the Agentic Development pillar shall serve what the session has done so far, read from its transcript (its messages and each tool call with a short form of its input and whether it failed), and the Dashboard shall show it on the task's and the job's pages as a feed that grows as Claude works, without reloading the page. | draft |

## Reading only what is shown; Claude's short answers watched (2026-10-05)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000386 | **Claude's short answers can be watched too.** While Claude answers an agent.query that a Job asked for, the Agentic Development pillar shall record the session against that Job so that the peek of that Job shows it (a backlog entry's refinement or task prompt, a policy's authoring), and the Dashboard shall show it on the job's page and on the backlog entry being refined, as it grows. | draft |

## Temporary policy bypass for the user (2026-10-05)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000397 | **A query can read a project's checkout and change nothing.** When a Job asks for an agent.query that reads a project's checkout, the Agentic Development pillar shall run Claude in that project's registered checkout with only the tools that read files, confined to that checkout and without bypassing permissions, and shall fail the query without starting Claude when the project or its checkout cannot be found. | draft |

## Remote control on a running Claude job (2026-10-05)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000399 | **Remote control is active from `started_at` until `stopped_at`.** When the user deactivates remote control or a Job's Claude work ends, the Agentic Development pillar shall set `stopped_at` of the Job's remote-control session, and the Dashboard shall treat a session without `stopped_at` as active only while the Job runs and keep its status as history. | draft |

## Claude Code status: the phone and the Windows app (2026-10-06)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000413 | **A Claude session is named after its project, version and branch.** When a Claude session starts or the user submits a prompt in a checkout whose top holds a VERSION file, the session-title hook shall set the session's title to "<project> · <version> · <branch>" if the project, the branch or the version differs from the ones it last applied to that session and leave the title as it is otherwise, so that a name the user gives in between stays until the next change, and shall never block the session or the prompt. | draft |

## Asset review: Blender assets in the Dashboard (2026-10-09)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000427 | **Only the visible asset folder and its subfolders are listed and served.** The Agentic Development pillar shall list the Blender files, glTF models, images and Markdown files of a project's visible asset folder as a tree with the folder's own structure, with each Blender file's exported bundle kept out of the tree and attached to it, and shall serve a file only when its path resolves inside that folder, refusing absolute paths, parent references and links that lead out. | draft |
| REQ-000000429 | **A comment on an asset is a Markdown file beside it.** When the user saves a comment on an asset in the Assets page, the Agentic Development pillar shall write it as the Markdown file named after the whole asset file plus the .md suffix (house.blend.md) beside the asset, delete that file when the comment is emptied, refuse an asset outside the visible folder, and the Assets page shall show the saved comment again. | draft |

## Asset review: the Export button (2026-10-09)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000446 | **Reads and side writes never use the shared connection outside its lock.** The Agentic Development pillar shall serve its reads and the short writes it makes apart from a task run on connections of their own, without using the shared database connection outside its lock, so that a read or a side write keeps working while the shared connection is in use or being closed. (2026-10-24: asking the shared connection for its file on another thread crashed the interpreter in a recorded test run.) | draft |

## Claude configuration: the newest request outranks earlier decisions (2026-10-24)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000447 | **The newest request outranks earlier decisions.** The generated Claude files shall state in every project's shared briefing section that the user's newest request outranks earlier decisions (design docs, house settings, Claude's memories, requirement rows, tests that pin a look), and when the user submits a prompt, the generated UserPromptSubmit hook (`.claude/hooks/newest_request_reminder.py`) shall add that rule to the prompt's context; the sync shall keep a project's own UserPromptSubmit hooks beside it. | superseded by REQ-000000478 |
| REQ-000000478 | **The newest request outranks earlier decisions, stated once in the briefing.** The generated Claude files shall state in every project's shared briefing section that the user's newest request outranks earlier decisions (design docs, house settings, Claude's memories, requirement rows, tests that pin a look), and shall register no UserPromptSubmit hook for it; when the Claude files are synced, the sync shall remove the retired `.claude/hooks/newest_request_reminder.py` and its settings entry and keep a project's own UserPromptSubmit hooks. | done |

## A Windows-hosted project's own session and server run natively (2026-10-28)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000467 | **A Windows-hosted project's session and server run as native Windows processes.** When a project's checkout is on a Windows drive, the Agentic Development pillar shall launch its Claude Remote Control session as a detached native Windows process tracked by a PID file rather than a WSL `dtach` socket, and the Deployment & Servers pillar shall launch its manifest `command` the same way, tracked by a PID file rather than a `systemd --user` unit, polling that PID and relaunching it on crash since there is no Windows unit for either pillar to delegate restart-on-failure to. | draft |
| REQ-000000481 | **A branch finish needs no separate Definition-of-Done run.** The generated workshop-lifecycle skill shall list, as the steps before a branch finish, only the policy-preflight skill and the finish itself, and shall say that a Python project's Definition-of-Done run is recorded by the release's coverage check, while another project records its run before the release. | done |

## Windows-drive projects as an explicit host (2026-11-07)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000512 | **An agent task commits only what it changed.** When an agent task's changes are committed, the Agentic Development pillar shall stage only the untracked files and the tracked files that differ from HEAD by more than line endings, and shall leave a file whose only change is its line endings uncommitted. | draft |

## Phase 6: closing the gaps (2026-11-07)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000518 | **An agent task runs in a worktree of its own.** When an agent task (agent.run_task) starts on a project that is not a Windows-hosted Unity project, the Agentic Development pillar shall provision a worktree of the project through Deployment & Servers on the task's branch (created from its base when new; a Windows-hosted project's worktree on its own drive under LWS_WINDOWS_WORKTREE_ROOT, C:\little-workshop\worktrees by default, made with git.exe), shall run Claude, the screenshots and the commit and push there without requiring or changing the live checkout, shall remove the worktree when the run ends awaiting finish and keep it when the run failed or was stopped until the project's next task removes it, and shall allow tasks of one project to run in parallel (the Orchestration queue key is per branch). A Windows-hosted Unity project's task shall keep running in the live checkout behind the cleanliness check, one at a time per project. | draft |
