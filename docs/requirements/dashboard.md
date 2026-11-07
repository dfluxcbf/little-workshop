# Requirements: Dashboard

Part of little-workshop's requirements register; conventions and the other parts are in [../requirements.md](../requirements.md). Sections keep the order and headings of the round that added them.

## M12 — Policy & Compliance

| ID | Requirement | Status |
|---|---|---|
| REQ-000000127 | **Needs-attention alerts for failing checks and pending confirmations** (implemented 2026-09-21). When an enabled policy's latest check on a project fails, or a Job awaits confirmation, the Dashboard shows a "needs your attention" banner on every page and lists each item at `/attention` (`GET /attention.json` is the feed). The banner refreshes itself every 20 seconds without a reload and, where the user has allowed browser notifications, raises one for each newly appearing item. GitHub-issue and Remote Control delivery are not built. | done |

## M14 — Dashboard

| ID | Requirement | Status |
|---|---|---|
| REQ-000000144 | Every panel degrades to a visible "UNREACHABLE" banner, never a 500, when the pillar behind it is down — the same discipline every route's `try`/`except client.PillarUnavailable` enforces, proven directly (unit tests monkeypatching each `dashboard.client` call to raise, and a real end-to-end test hitting every panel with nothing but Dashboard itself running). `manifest.py`'s `DEFAULT_ENABLED["dashboard"]` flips to `True` — on by default per `docs/architecture.md`'s own "a headless/automation-only setup can disable it" — now that the pillar is real. | done |

## M16 follow-up — parity survey and rejecting compatibility shims (2026-09-17)

| ID | Requirement | Status |
|---|---|---|
| REQ-000000166 | **Wheel publishing derives name and version from the filename** (closed 2026-09-21). `build.publish_pypi` reads them from the wheel/sdist filename when omitted and refuses a filename with no version or one that disagrees with a supplied version (PEP 440 spelling). CLI: `lws build publish-pypi FILE [--name] [--version]`. The `wheel-filename-has-version` policy script (`tools/policy_scripts/`, imported as a draft awaiting approval in the Dashboard) checks that `py_wheel` targets declare a version and built wheels are named `<name>-<version>-<tags>.whl`. | done |

## Dashboard redesign: mission control for projects (2026-09-21)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000193 | **The Dashboard is now a project-management surface, modeled on `little-runner`'s UI.** `/` is a per-project overview (server, Claude session, tasks, requirements done/total, CI); `/control` is `little-runner`'s Control page reshaped (deploy/stop a server, start/stop a Claude Code session, recent tasks); `/tasks`, `/tasks/new` and `/tasks/{project}/{run}` cover Agentic Development's runs, a New task form and Finish/Skip; the system's own pillars moved to their own `/pillars` page. `little-runner`'s stylesheet, top/bottom navigation, icons and manifest were copied rather than re-derived. Deploys are submitted from a background thread (a cold `bazel run //:install` outlasts a browser request). | done |
| REQ-000000194 | **Installable on Android and phone-sized**: a web app manifest (standalone, maskable icons), a root-scoped service worker (`/sw.js`, static shell only — pages are always live) and an offline page; `viewport-fit=cover`, tables collapsing to labelled cards, and a bottom tab bar on narrow screens. Checked at 390×844. | done |
| REQ-000000195 | **Launching a Claude Code session from the UI**: Control starts/stops `agent.session_start`/`session_stop` per project. `GET /v1/sessions/{project}` gained `trusted` (whether Claude Code has accepted the workspace-trust prompt), so an untrusted project shows an explanation instead of a Start button that would only hang. | done |
| REQ-000000197 | Proven at the unit level (`test_dashboard_app.py`: the project dashboard, per-panel degradation when a pillar is down, unhealthy-pillar banner, Control's launch/stop/session controls and the untrusted explanation, non-blocking deploy submission, session start/stop, task list/new/detail/finish/skip, manifest/icons/service worker/offline; `test_dashboard_client.py`: 404-as-absent vs. outage; `test_agent_app.py`: `trusted`, and a run-lock regression test that reads a run while another blocks) and end-to-end (`test_dashboard_end_to_end.py` against real units), plus a phone-width screenshot of the live install. | done |

## Workbench usability: ordering, workspace session, reporting, bootstrap, scheduling (2026-09-21)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000199 | **Order of project cards on the Dashboard.** When the user moves a project card up or down on the Dashboard, the Dashboard shall remember that order and show the project cards in it on every later visit (`dashboard/prefs.py`). | done |
| REQ-000000202 | **Requirements and tests report.** When the user opens the Report page for a project, the Dashboard shall list every requirement with its description and verdict and every test with its name and verdict. | done |
| REQ-000000203 | **Bootstrap a project from the toolbar.** When the user submits the New project form, the Dashboard shall submit a `project.bootstrap` job that creates the project's basic files and registers it. | done |
| REQ-000000204 | **Schedule a task.** When the user submits the Schedule task form, the Dashboard shall create a scheduled task that starts the described agent task once at a chosen time or on chosen weekdays each week. | done |
| REQ-000000205 | **Queue a task behind a predecessor.** When the user submits the Queue task form with a predecessor task, the Dashboard shall submit the new task so it starts only after the predecessor has succeeded (a job with `depends_on`). | removed 2026-09-23 — dropped by decision (Queue task is out of scope; New task's own Schedule/immediate choice replaces it) |
| REQ-000000206 | **Queueing requires a predecessor.** If the user submits the Queue task form without a predecessor task, then the Dashboard shall refuse the submission and shall not create a job. | removed 2026-09-23 — dropped by decision (Queue task is out of scope) |
| REQ-000000208 | **Grouped bottom toolbar.** When the user taps a group button on the bottom toolbar, the Dashboard shall reveal that group's related pages, so that Tasks offers the task list, Jobs, New task, Queue task and Schedule task. | done |
| REQ-000000200 | **Order of project cards on Control** — superseded, not implemented as its own thing: the separate Control page this requirement named no longer exists (REQ-000000214), folded into the Dashboard's own single order (REQ-000000199). | removed 2026-09-22 — superseded by REQ-000000214 (Control page merged into the Dashboard) |

## Report and Dashboard redesign: tabs, collapsible cards, Control merged in (2026-09-22)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000210 | **Report page splits requirements and tests into tabs.** When the user opens the Report page, the Dashboard shall present Requirements and Tests as separate tabs and show only the selected tab's content. | done |
| REQ-000000211 | **Requirement cards are collapsible on the Report page.** When the user taps a requirement's card on the Requirements tab, the Report page shall expand or collapse that requirement's description while its key, title and verdict stay visible. | done |
| REQ-000000212 | **Tests are grouped into collapsible suites with one card per test.** On the Tests tab, the Dashboard shall group tests into collapsible suites, using the same test-suite grouping as the Requirements tab, and shall show each test in its own card. **Corrected by REQ-000000215**: "the same test-suite grouping as the Requirements tab" turned out to be one all-tests bucket, not real per-file suites — the first implementation was literally true but useless; the module-suite grouping REQ-000000215 describes is what this requirement actually meant. | done |
| REQ-000000213 | **Dashboard project cards expand to show controls.** When the user taps a project card on the Dashboard, the Dashboard shall expand it to show that project's server, Claude Code session and task controls, and collapse it back to the summary when tapped again. Implemented as a native `<details>`/`<summary>` card. | done |
| REQ-000000214 | **Control page merged into the Dashboard.** The Dashboard is the only page that shows a project's server, Claude Code session and task controls; the separate `/control` page and its route are removed, and the workspace session panel moved to the top of the Dashboard. | done |
| REQ-000000242 | **Policies pages surface per-policy pass/fail status.** When a policy has one or more currently-failing associated projects, the system shall show a failing count on that policy's card on the Policies list and on its own detail page, and shall list every associated project's pass or fail status with the failing check's own stdout available for a failing project. | done |

## Report suite grouping correction: real test modules, not one all-tests bucket (2026-09-22)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000215 | **Tests grouped by real test module, not one all-tests suite** — found by the user testing REQ-000000212's own first implementation, which had used the single self-application "Unit tests" bucket (`tools/import_requirements.py`'s `REGISTER_SUITE`) as the only suite, so the Tests tab showed every one of a self-application project's ~2000 tests flattened under one heading instead of grouped by file. When the Report page's Tests tab groups tests for a self-application project, the Dashboard shall use one suite per test module (`test_foo.py` -> suite `test_foo`), never one suite covering the whole test run. Closed via a new `import_module_suites` (`tools/import_requirements.py`, deriving each module from a JUnit case's own `classname`, folding a class-based test's trailing class segment into its module) recording results under per-module suites that are never linked to a requirement — a requirement's done/passing state stays governed by whichever suite it is actually linked to (the aggregate bucket, for a self-application project), untouched. The Report route (`dashboard/app.py`'s `build_report`) excludes that aggregate suite by name (`AGGREGATE_SUITE_NAME`) from the Tests tab so it is never shown flattened again. | done |

## Policy draft project selection is pre-fillable (2026-09-22)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000217 | **Policy draft project selection is pre-filled on the approval page.** When a policy draft carries its own project selection, the Dashboard's draft approval page shall pre-check each of those projects and set its Gating switch from the draft's own recorded value, instead of starting every project unselected. `PolicyDraft` gains a `projects` field (`policy/models.py`), the same `{project_id, gating_enabled}` shape as a real policy's own selection, settable via `policy.author`/`policy.draft_import` and migrated onto an existing database with a guarded `ALTER TABLE`. The reviewer's own choice at approval time, submitted with the decision, is still what actually takes effect — this only changes what the form starts with. | done |

## Policy draft approval streamlined: no confirmation switch, auto-advance (2026-09-22)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000218 | **Approving a policy draft needs no separate confirmation.** When the user presses Approve on a policy draft, the Dashboard shall submit the approval without requiring a review checkbox or a typed reason. The earlier "I have reviewed the policy text and the test script" switch is removed; `policy.approve_draft` still receives `reviewed: true`, sent unconditionally the moment Approve is pressed. | done |
| REQ-000000219 | **Approving a policy draft advances to the next one.** When a policy draft is approved, the Dashboard shall open the next pending draft, or the Policies page when none remain, instead of landing on the newly created policy's own page. | done |

## Policies list gating chip shows "on" instead of "off" (2026-09-22)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000220 | **Policies list shows gating-on count in a neutral chip.** Where a policy's card on the Policies page shows how many applied projects have gating on, the Dashboard shall use a neutral chip and shall omit it when no project has gating on. Replaces the earlier amber "gating off: N" chip, at the user's request after asking what it meant. Per-project detail (which project, its own Gating switch) remains on the policy's own detail page. | done |

## System health, automatic bug tasks, coverage policy, data catalog (2026-09-22)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000222 | **Data catalog lists pointer and inline data.** When the user opens the Data page, the Dashboard shall list every pointer data category with its owning pillar and live endpoint, and every inline data category with its own current value. | removed 2026-09-23 — dropped by decision (Data catalog is out of scope; it duplicated each owning pillar's own live panels) |
| REQ-000000223 | **Inline data is always read live.** Where a data category is shown inline on the Data page, the Dashboard shall read its current value live from the owning pillar on every visit, never from a separately stored cache — the honest reading of "CI automatically updates those values": there is no separate stale copy for anything to refresh. | removed 2026-09-23 — dropped by decision (Data catalog is out of scope) |
| REQ-000000224 | **Secrets and tokens show validity on System health.** When the user opens the Pillars page, the Dashboard shall show each configured secret's validity and, when it carries one, its expiration date, never the secret's own value. Registry & Portfolio's `GET /v1/secrets` reads the host's `gh` CLI credential (`gh auth status` for validity, GitHub's own `github-authentication-token-expiration` response header for expiration, present only for a token that carries one) — never the token text itself. | done |
| REQ-000000229 | **Open incidents appear in Needs Attention.** Where an incident is open, the Dashboard shall list it on the Needs Attention page and count it in the attention banner, alongside a failing policy check and a job awaiting confirmation. | done |

## Report page: phantom horizontal scroll fixed (2026-09-23)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000231 | **Report page never causes phantom horizontal scroll.** Where a requirement's title or description has no wrap points nearby, the Report page shall still wrap it within its own card, never widening the page's own layout viewport, so the top and bottom bars stay fixed on a phone. Root cause: flex children (`.report-title`, `.report-desc`, `.report-head`) default to `min-width: auto` (their own content's natural width), so a long, low-wrap-opportunity run of text could push a card — and, on a real phone, the whole page's layout viewport — wider than the screen, even though nothing looked visibly clipped. That viewport widening is what made the fixed top and bottom bars appear to scroll away: their `left`/`right` resolve against the widened viewport, not the true device width. Fixed with `min-width: 0` plus `overflow-wrap: anywhere` on the affected elements. The bottom tab bar's own "System" row (5 pages plus Back) also gained horizontal scrolling within itself, the same pattern the Jobs page's status-chip strip already used, as a second layer of defense against the same class of bug. Verified live: `document.documentElement.scrollWidth` now equals `innerWidth` on the Report page (was 435 vs. a true 412), and the sticky header/fixed tab bar both stay in place through a real scroll. | done |

## Toolbar and task/job/policy workflow overhaul (2026-09-23)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000232 | **Bottom toolbar is grouped into Dashboard, Actions, Policies, System.** When the user opens the app, the Dashboard shall show a bottom toolbar with a standalone Dashboard button and three groups — Actions (task list, new task, jobs, new job, schedule job), Policies (policy list, new policy) and System (pillars, incidents, settings). Report and New project moved off the toolbar entirely, reachable only as contextual links (Dashboard's own "New project" link, and each project card's own "Report →" link). | done |
| REQ-000000233 | **Report loads with no project preselected.** When the user opens the Report page without a project chosen, the Dashboard shall show no project's data until the user selects one, never preselecting the first registered project. | done |
| REQ-000000235 | **New Task lets the user choose immediate or scheduled.** When the user submits the New Task form, the Dashboard shall start the task immediately or create a scheduled task for it, according to the timing the user chose on that same form. Queue task (a task behind a predecessor, REQ-000000205/206) was dropped rather than folded in. | done |
| REQ-000000236 | **New Job dispatches a reused or Claude-drafted job.** When the user submits the New Job form, the Dashboard shall dispatch the job template the user chose — reused from an existing job's own type and parameters, or drafted once by Claude from the user's own description — after letting the user review and edit it. Any Orchestration job type can be built this way, not only `agent.run_task`. | done |
| REQ-000000237 | **Schedule Job schedules a reused or Claude-drafted job.** When the user submits the Schedule Job form, the Dashboard shall create a scheduled task from the job template the user chose — reused from an existing job, or drafted once by Claude — starting once at a chosen time or on chosen weekdays each week. | done |
| REQ-000000238 | **New Policy authors a draft via Claude.** When the user submits the New Policy form, the Dashboard shall run the policy-authoring pipeline once on the user's own plain-language description and file the result as a draft awaiting the user's approval. | done |
| REQ-000000239 | **Editing a policy re-authors it as a new draft.** When the user submits the Edit Policy form for an existing policy, the Dashboard shall re-run the policy-authoring pipeline with that policy's own id and file the result as a new draft awaiting the user's approval, never changing the existing policy directly. | done |
| REQ-000000240 | **Settings absorbs Needs Attention.** When the user opens Settings, the Dashboard shall show the Needs Attention feed there instead of on its own separate page. `/attention.json` is unchanged as a JSON data endpoint. | done |

## Dashboard home page load time fixed (2026-09-23)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000246 | **The Dashboard's home page loads without a full table scan per requirement.** Where the Definition-of-Done report (`/v1/report`) resolves a requirement's linked suites' latest test verdicts, the Requirements & Quality pillar shall look each one up via an index on `test_results(suite_id, executed_at)`, never by scanning every historical row a suite has ever recorded. Root cause: `latest_result_for_suite` fetched and sorted a suite's *entire* result history on every call with no supporting index — with little-workshop's own accumulated ~41k rows, its home-page report (231 requirements) alone took 9.7s of the Dashboard's measured 11.3s total home-page load (reported by the user as "a bit more than a minute" on a phone). Verified: the same report against a copy of the live data dropped from 9.7s to 0.43s (22x) after adding the index and rewriting the lookup as two indexed queries (latest timestamp, then that batch) instead of one unindexed full-history fetch. | done |

## Dashboard page-load sweep: Pillars page fixed (2026-09-23)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000250 | **The home page fetches each project's own panel data concurrently, not one project at a time.** Where the Dashboard's home page builds each registered project's row (server/session/tasks/requirements/CI/branches — six independent, read-only pillar calls per project), it shall fetch them concurrently across projects rather than looping through projects one at a time, since none of those calls depends on another project's result. Verified live: home page load dropped from ~1.7-2.0s to ~0.9-1.0s with this alone (on top of REQ-000000246's query fix); a dedicated test asserts the calls actually overlap in time, not just that the page still renders correctly. No caching involved — same live data, fetched in parallel instead of serially, so no staleness risk. | done |

## Comprehensive Task list (2026-09-24)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000256 | **Task list is grouped into Queued, Running, Needs attention and Done.** When the user opens the Task list, the Dashboard shall show every agent task in one of four sections, in this order and each with its own count: Queued (submitted, not started), Running, Needs attention (awaiting the user's finish/skip review, failed, or stopped) and Done (finished, skipped, or cancelled before it started); an empty section shall say so rather than disappear. Supersedes REQ-000000234's single flat list. | done |

## Scheduled tasks and jobs as cards (2026-09-24)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000259 | **Scheduled tasks and jobs are cards that open their details.** When the user opens New task or Schedule job, the Dashboard shall list the scheduled tasks or jobs as cards and shall open a tapped card's own details page showing its schedule, next and missed run, what it runs, its last Job and a Remove action. | done |

## New job absorbs Schedule job (2026-09-24)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000263 | **New job starts or schedules the chosen job.** When the user submits the New job form, the Dashboard shall start the chosen catalog job right away, or, if the user chose "once at a date and time" or "every week" under When, shall create a scheduled task for it instead; the separate Schedule job page is removed from the toolbar, `/jobs/schedule` redirects to New job, and New job lists the scheduled jobs as cards. Supersedes the separate page of REQ-000000237 and the "schedule job" toolbar entry of REQ-000000232. | done |

## Update Standards Documentation job (2026-10-01)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000283 | **A catalog job can be limited to some projects.** Where a catalog job names the projects that offer it, the Dashboard shall offer that job on New job only while one of those projects is chosen, and shall refuse to start or schedule it for any other project. | done |
| REQ-000000284 | **Update Standards Documentation is offered for little-workshop only.** When the project chosen on New job is little-workshop, the Dashboard shall offer the job "Update Standards Documentation"; for every other project it shall not. | done |
| REQ-000000285 | **Update Standards Documentation has Claude refresh `shared_project_standards/`.** When Update Standards Documentation is started or scheduled, the Dashboard shall queue an `agent.run_task` Job of intent docs for little-workshop, on little-workshop's own queue, whose instructions have Claude review every little-workshop standard, every registered policy and every registered project's own rules, then update every document in `shared_project_standards/` and nothing outside it, as Markdown only. | done |

## Standards copy for tasks (2026-10-01)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000288 | **Starting a task chooses whether it includes the standards.** When a task is started or scheduled or retried from the Dashboard or through workshop_agent_run_task, the Dashboard shall offer an include_standards choice that defaults to included as a checked box on the New task form and carry it through scheduling and retry, so that a run whose choice is off gets no copy. | published |

## Deciding gated jobs from the Dashboard (2026-10-01)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000290 | **A job awaiting confirmation can be decided from the Dashboard.** When a Job is in pending_confirmation, the Dashboard shall offer Confirm anyway and Cancel and Mark as failed on its Jobs page card and on its Settings attention card. | published |
| REQ-000000292 | **Attention cards are tagged by what they concern.** When the Settings page lists what needs attention, the Dashboard shall tag each card as Policies or Jobs or Incidents according to what it concerns. | published |

## Policies as the hard layer of Claude instructions (2026-10-02)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000311 | **An action that needs the user asks, and times out after 12 hours.** When an action of a running Job needs the user to decide how to proceed, the Job shall put the action in the needs-attention state with a question and options and wait for an answer given through lws, the Dashboard or an MCP tool; with no option available it shall fail the action and the Job at once, and after 12 hours without an answer it shall fail the action and the Job. | draft |
| REQ-000000312 | **The CLI and the Dashboard show what a job is doing.** When a Job is started with lws, the CLI shall print its checklist and then each action as it starts and ends, asking the user when an action needs them; the Dashboard job page shall show the checklist with an icon per action state, each action's current or total runtime and expected runtime, and the buttons to answer a question. | draft |
| REQ-000000313 | **A failed task leaves the attention list when moved to done, or after a week.** When a task has failed, the Dashboard shall list it under Needs attention with a Move to done button and a Retry button; when the user moves it to done, or when a week has passed since it failed, the Dashboard shall list it under Done, and the task shall keep its failed status so it can still be retried. | draft |

## Documentation menu and Statistics page (2026-10-03)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000315 | **Documentation groups Review and Report in the toolbar.** The Dashboard's toolbar shall hold a Documentation group in place of the Report entry, with the pages Review (the documentation review at /docs) and Report (/report), and the Actions group shall not list the documentation review. | draft |
| REQ-000000316 | **Operation statistics are kept per day, apart from the pillars.** When the Statistics page is visited or ten minutes have passed since the running Dashboard last counted or the Dashboard is about to delete old Jobs, the Dashboard shall read every Job, policy check result and incident the pillars hold and add each one not counted before to the local day it happened on in its own store: per job type and project the times triggered, succeeded and failed and the duration of the finished runs; per policy and project the checks passed, failed and not applicable; the incidents; and per project the test executions that succeeded and failed. The Dashboard shall store with each count both the project it is about and the pillar that did it, keep the counts of a day when the Jobs behind them are deleted, and leave a source whose pillar is unreachable as it was. | draft |
| REQ-000000317 | **The Statistics page plots the operations per day.** The Dashboard shall offer a Statistics page under System that plots, per day over a period the user picks as two days on a calendar whose days are brighter the more jobs they had, the jobs triggered, the job success ratio, the policy fail ratio, the average job duration, the incidents and the test success ratio; the page shall offer, at the top and for all plots, a Project list showing everything about one project and a separate Pillar list showing everything one pillar did with only one of the two applying at a time, the job and policy plots shall let the user select and deselect jobs or policies, and hovering a day shall show its exact numbers. | draft |

## Stop button on the job page (2026-10-03)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000318 | **The job page stops a running job.** While a Job is running or queued, the Dashboard job page shall offer a button that, once the user confirms, asks Orchestration to stop the Job (a queued Job is cancelled) and returns to the job page; when the Job cannot be stopped, the page shall show the reason Orchestration gave, and the page shall offer no such button for a Job in any other state. | draft |

## Claude usage page on a phone (2026-10-04)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000355 | **The Claude usage page says what each colour is, dates its days and fits a phone.** The Dashboard's Claude usage page shall draw each plot at the width it has on screen with a legend naming the series of each colour and marking a comparison week as dashed, shall label each day on the x axis with its weekday and its date and each week with the date of its Monday, and on a screen 680 pixels wide or narrower shall show each table row as a block of values under their column names, so that the page needs no horizontal scrolling. | draft |

## Stuck jobs, force stop and incidents (2026-10-04)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000366 | **A job can be force-stopped.** When the user force-stops a running Job from the Dashboard after a warning of what it does, the Orchestration pillar shall ask the target pillar to stop it and then, whatever the pillar answers, settle the Job as stopped ("force-stopped by user"), release its queue key, fail the Jobs that wait on it and dispatch what its queue key held, and shall ignore a completion later reported for a Job that has already settled. | draft |

## Backlog (2026-10-04)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000372 | **The Backlog pages.** The Dashboard shall have a Backlog page under Documentation listing each project's backlog, a page per project with its entries as cards and a form to add one, and a page per entry showing the primary entry, refinement notes, refined entry and task prompt, from which the user can delete the entry (after confirming), edit and save the primary entry, write and save refinement notes, refine the entry, generate the task prompt, copy the task prompt with an icon at its top corner, and launch a task, which opens New task with the entry's task prompt in the Task box (its refined entry while it has no task prompt, its primary entry while it is not refined, each said so). | draft |
| REQ-000000373 | **"Make it a task" opens New task.** When the user chooses "Make it a task" on an automation candidate, the Dashboard shall open New task on little-workshop with the candidate's task title, kind and description filled in, and once that task starts the Usage & Efficiency pillar shall mark the candidate's task started. | draft |

## Dashboard pages: usage hover, schedule, filters, links (2026-10-04)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000374 | **Hovering a Claude usage plot shows the day's numbers.** When the user points at or taps a plot on the Claude usage page, the Dashboard shall mark the day under the pointer with a vertical line and show a box with that day's date and the value of every series drawn, comparison weeks included, as the Statistics page does. | draft |
| REQ-000000375 | **A calendar of jobs and tasks.** The Dashboard shall have a Schedule page under Actions with a month calendar of the scheduled jobs and tasks: each run of a schedule from the Job history, and each date a schedule will run, a weekly one on each of its days from its first run on (and, when the user asks for everything, every other job and task that ran too); a day shall show its tasks as triangles and its jobs as circles, coloured by state (failed, running, skipped, succeeded, scheduled), one symbol per kind and state with the number of its items inside when there are several; a job deleted from the history, a job cancelled before it ran and a removed schedule shall not appear; choosing a day, anywhere on it, shall list all its items in a panel to the right of the calendar, or below it on a phone, the items on the calendar themselves not being choosable; choosing an item in the panel shall expand or collapse its details, several of them open at the same time; the page shall show how many of the month's items are in each state, of each kind, and the panel how many of the chosen day's; the month that holds today shall open with today listed in the panel. | draft |
| REQ-000000376 | **Policies can be filtered.** The Policies page shall filter the policies by a text matched with glob wildcards against each policy's name and description together, by status (enabled, disabled, failing), by gate points and by the projects it governs (several of each), and by gating on in at least one project; the filters combine and are kept in the page address. | draft |
| REQ-000000377 | **A task and its job link to each other.** A task's page shall link to the Job that ran it, and the page and card of an `agent.run_task` Job shall link to its task. | draft |

## Live pages and Claude peek (2026-10-04)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000379 | **Pages update themselves.** The Dashboard shall keep one event stream per open page, subscribed to the kinds of information the page shows, and shall send an event on it within seconds of a change of that kind (Jobs and their steps, tasks, scheduled tasks, policies, incidents, the backlog, statistics, Claude usage); the page shall then replace only the parts of it that changed, keeping what the user has open or is typing, never reloading itself and never polling on a timer; the Dashboard, Jobs, a job, Tasks, a task, Schedule, Policies, Incidents, Statistics, Claude usage and Backlog pages and the attention banner subscribe. | draft |

## Reading only what is shown; Claude's short answers watched (2026-10-05)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000383 | **The Dashboard reads only what it shows.** The Dashboard shall read Jobs only as filtered summaries for its lists and only the one Job it shows in full, and the Jobs page and a job's page shall show at most 20,000 characters of a Job's params or result, linking to the whole as JSON. | draft |
| REQ-000000388 | **Pillar health updates the pages that show it.** When a pillar's health changes, the Dashboard shall update its Dashboard and Pillars pages as it updates its other live pages. | draft |

## Reading only what is shown: attention and cleanup (2026-10-05)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000389 | **A cleanup count reads no Job's content.** When the Dashboard asks how many Jobs each cleanup age would delete, the Orchestration pillar shall count them from each Job's status, last change and dependencies, read once for every age, and never read a Job's params or result for it. | draft |
| REQ-000000391 | **Statistics read only new policy results.** The Dashboard shall read for its statistics only the policy check results recorded since its last count, and every one of them once a day. | draft |

## Temporary policy bypass for the user (2026-10-05)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000392 | **Every policy request records its origin.** Every Job and policy evaluation shall carry the origin of its request (the user's terminal CLI, Claude, a scheduled job, a Dashboard-triggered job, an event trigger or unknown), stamped by the client that made it, and a policy check result shall record the origin it was requested from; a request whose origin is not positively the user's terminal is never taken for it. | draft |
| REQ-000000394 | **The bypass lets only the user's CLI past a failing policy.** WHILE bypass_policies is TRUE, a failing policy check requested from the user's terminal CLI shall still run, be recorded and be reported to the user but shall not gate the action; a request from Claude, a scheduled job (right away, once at a date and time, every week), a Dashboard-triggered job or an event trigger shall still gate on failure, and bypassed violations shall still appear on the Dashboard and in its statistics. | draft |
| REQ-000000395 | **The Dashboard warns while the policy bypass is TRUE.** WHILE bypass_policies is TRUE, the Dashboard shall show on every page a warning with the remaining time and a button that sets the flag to FALSE at once, after which the warning shall disappear. | draft |

## Remote control on a running Claude job (2026-10-05)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000398 | **The owner can activate remote control on a running Claude job.** While Claude is running a Job or task, the Dashboard shall offer on its own page an "Activate remote-control" button that resumes the Job's Claude session with remote control without stopping or interrupting the work, shall show afterwards only the remote-control status and session name, and shall remove the button without a page refresh when the Job stops. | draft |

## Claude Code status: the phone and the Windows app (2026-10-06)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000411 | **A project's status for Claude Code is one read.** When asked for the status of a project, the Dashboard shall answer in one JSON response, from filtered reads of the owning pillars made at once: whether the project has a server and whether it runs; its running Jobs; its tasks that need attention as the Tasks page groups them; its Jobs that failed in the last two hours; its active scheduled tasks due within the next hour; its failing policies; and, across every project, the open incidents recorded in the last two hours and the pillars Core sees crashed or faulted. Each topic shall carry its count and its first items, a count of null with the pillar named in `unavailable` when that pillar does not answer, and a project the Registry does not know shall get only the workshop's topics. | draft |
| REQ-000000412 | **The status page shows every topic, zeros included.** The Dashboard shall serve a status page per project with a tile for the project's server and for each topic of its status, and one for the workshop's incidents and dead pillars, every count shown even when it is zero; it shall list the items behind each count above zero with a link to their page, say in one line when a group has nothing to list, follow its topics live and look again every minute. | draft |

## Asset review: Blender assets in the Dashboard (2026-10-09)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000428 | **The Dashboard has an Assets page for projects with a visible asset folder.** Where a project has a visible asset folder, the Dashboard shall show an Assets button above the project's list of documents, leading to an Assets page that shows the folder's tree and, for the selected Blender file, its 3D model, its ten preview images, its UV layout and its textures, and for a glTF model, an image or a Markdown file, the file itself, each texture and UV image being visible directly. | draft |

## Documentation review: the Docs page as folders (2026-10-09)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000431 | **The Docs page of a project is organised as its folders.** When the user opens a project's Docs page, the Dashboard shall show the documents of the project's root folder as a grid of two columns of cards, subfolders first, each folder card showing how many documents lie beneath it with their open, done and certified comment counts and opening a page of its own for that folder with a breadcrumb back to the root, and each document card opening the document. | draft |

## Asset review: the Export button (2026-10-09)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000432 | **An Export button on the Assets page exports a Blender file's model and previews.** When the user presses the Export button shown under the export state of a Blender file on the Assets page, the Dashboard shall start the catalogued Export Blender assets Job for that file, which the Agentic Development pillar runs by running Blender headless to write the file's bundle, shall show the file as exporting and reload itself until the Job ends, shall not start a second export of a file that is being exported, and shall refuse a file that is not a Blender file of the project's visible asset folder. | draft |

## Requirement conflicts, found by code and resolved by a Claude task (2026-10-30)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000469 | **Data management deletes a project's local data and registration.** When the user confirms a project's name on System > Data management, the Dashboard shall delete the project's registration and every record the workshop keeps about it (registry, usage, policy, agent, deployment, build and orchestration), and shall keep its git checkout and its requirements. | draft |
| REQ-000000471 | **Data management lists and deletes stored requirements.** The Dashboard shall list on System > Data management every project id with stored requirements, tag each one that is not registered, and, when the user types the project id after an irreversibility warning, delete that project id's requirements. | draft |

## Phase 6: closing the gaps (2026-11-07)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000522 | **An on-demand job audits a project's docs against its code.** When the user starts the New Job catalog's "Audit docs against code" for a project, the Dashboard shall submit an `agent.query` Job that reads the project's checkout and changes nothing (read_checkout, up to 1800 seconds), whose prompt asks Claude for each documentation statement the code contradicts, with its document and line, what the code does and the fix; and the Job's page shall show Claude's answer. The audit shall never run on a schedule of its own. | draft |
