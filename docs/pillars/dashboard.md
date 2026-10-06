# Dashboard — pillar specification

## Description

"A human can see and act on everything above, without a terminal"
(`docs/architecture.md`'s Components table). Generalizes `little-runner`'s
own dashboard with a panel per pillar, consuming the already-decided
visual identity directly (`presentations/assets/identity-tokens.css` —
"Ledger" layout, Mission Control's color tokens, violet excluded; dark
mode only, by explicit direction) rather than re-deriving it.

Not a pillar with domain data of its own: every panel is a live read
through `dashboard/client.py` against the owning pillar's Pillar
Interface, and every mutation a Job submitted to Orchestration — the
same discipline the CLI and MCP Server already follow (`docs/
architecture.md`'s "Dashboard and MCP Server hold no domain data of
their own — both are thin, stateless proxies"). Built in M14, once M12/
M13 gave it something concrete and already-backed to surface first
(policy management), per that milestone's own reasoning for landing
last: "its value scales with how much there already is to show."

## Look and installability

The UI is `little-runner`'s own "mission control" design, copied rather
than re-derived (`static/app.css`, minus its violet — this identity is cyan-only, `presentations/06-visual-identity.md`; its top bar and bottom tab
bar, its icons and PWA manifest): dark, card-based, phone-first. It
**installs as an Android app** (a web app manifest with maskable icons,
a root-scoped service worker at `/sw.js`, HTTPS via the tailnet), and
**fits phone widths** (`viewport-fit=cover`; tables collapse to labelled
cards under 680px; a bottom tab bar replaces the top nav). The service
worker caches only the static shell — pages are always live, since they
are the current state of every pillar.

## Responsibilities

- **Dashboard** (`/`, redesigned 2026-09-21, cards made expandable and Control
  folded in 2026-09-22): a project-management overview that fits a phone
  screen. Five stat tiles (projects, servers up, Claude sessions, active
  tasks, pillars healthy — the last links to Pillars and turns red when any
  pillar is not healthy), a workspace-session panel, then one expandable
  `<details>` card per registered project. Collapsed, a card lists only the
  facts that exist — server up/down (with version), Claude session (running /
  idle / untrusted), tasks awaiting finish or running, requirements
  done/total, latest CI — as a plain-text label beside a status pill
  (requirements are green when all are done, red when a *published*
  requirement is not passing, and neutral when the only shortfall is drafts
  awaiting review, counted as "N draft"), never a row of "—" placeholders; a
  project with something to act on (an untrusted session, a task awaiting
  finish, a failing CI) gets an amber edge. Tapping the card expands it to
  the project's own controls — deploy a branch (Launch/Relaunch) or Stop the
  server, Start/Stop a Claude Code session (refused with an explanation when
  the project isn't yet trusted by Claude Code), and its recent tasks with a
  New task action — the same controls the now-removed Control page offered,
  and collapses back to the summary when tapped again (`?expand={project}`
  opens one directly, e.g. after a deploy). Deploys are submitted from a
  background thread, since a cold `bazel run //:install` outlasts a browser
  request; progress is on the Jobs page. For an **externally managed**
  server (see `deployment_servers.md`) the Server section shows its status
  and unit but no Launch/Stop controls, only a note that it is managed
  outside Deployment. Each panel is its own live read; a pillar that is down
  degrades that fact only ("unavailable"), never a 500. Small text is kept
  legible: dim text is a lighter grey than before (`--ink-faint`), and stat
  labels are plain 13px text rather than tiny uppercase.
- **Needs attention** (every page; `/attention`, `/attention.json`, 2026-09-21):
  a banner under the top bar, shown only when something needs a human — an
  enabled policy whose latest check on a project failed, or a Job awaiting
  confirmation. `/attention` lists each item as a card linking to the
  policy or the Jobs page; `/attention.json` is the same list as data. The
  banner re-reads the feed when the page's live stream says something needing attention changed
  ("Live pages", 2026-10-04; it used to every 20 seconds), and once the
  user taps "Alert me on this device" (browser notification permission) it
  raises a notification for each item that newly appears. A pillar that is
  down contributes nothing. This is the Dashboard half of "informing the user
  immediately" (REQ-000000127); GitHub-issue and Remote Control delivery
  are not built.
- **Workbench additions (2026-09-21, Control merged into Dashboard 2026-09-22,
  toolbar/task/job/policy workflow overhauled 2026-09-23)**:
  - **Navigation** is grouped (regrouped 2026-10-03; Backlog and Schedule added 2026-10-04). The bottom
    toolbar has Dashboard (standalone), Documentation, Actions, Policies and System; a group button reveals
    its pages (Documentation: Review, Backlog, Report; Actions: Task list, New task, Jobs, New job, Schedule,
    Triggers; Policies: Policies, New policy; System: Pillars, Incidents, Statistics, Claude usage,
    Settings) with Back returning to the main row, and the
    group of the current page stays open. On a wide screen the top bar holds the groups and a
    sub-navigation row lists the active group's pages. Review is the documentation review (`/docs`).
    New project is reachable only as a contextual link (Dashboard's own "New project" link beside
    "Arrange cards"), since it is reached from the Dashboard rather than picked cold.
  - **Arrange cards** (`/?arrange=1`, `POST /order/dashboard/{project}/{up|down}`): the Dashboard
    keeps its own order of project cards; a project the user has never placed goes after the
    placed ones.
  - **Workspace session** (Dashboard, top panel, above the project cards): starts and stops one
    Claude Code session in the projects root directory (`~/little-projects`, the parent of the
    workshop checkout; `LITTLE_WORKSHOP_WORKSPACE_DIR` overrides), under the reserved project id
    `workspace`, so Claude can work across projects. The root must be trusted by Claude Code like
    any project.
  - **Report** (`/report`, no project pre-selected 2026-09-23): loaded with no project chosen —
    requirements and tests are only fetched once the user picks one, since a self-application
    project's own report can be large and shouldn't load by default. Once a project is picked,
    Requirements and Tests show as two tabs. The Requirements tab lists every requirement as a
    collapsible card — key, title and verdict always visible, description shown only when expanded.
    The Tests tab groups tests into collapsible suites, one card per test with its name and latest
    verdict; for a self-application project (whose own `docs/requirements.md` carries no
    per-requirement test mapping — REQ-000000149's own honest gap), that grouping is by real test
    module (`tools/import_requirements.py`'s `import_module_suites` — `test_foo.py` becomes suite
    `test_foo`), never the one all-tests bucket (`AGGREGATE_SUITE_NAME`, `dashboard/app.py`) that
    suite is linked to a requirement's own done/passing state and is hidden from this tab so it
    doesn't duplicate every test under one giant heading (REQ-000000215, correcting
    REQ-000000212's own "same suite grouping" wording — module suites are never linked to a
    requirement, so they don't affect done/passing). A test the hidden aggregate suite ran more recently than its module suite did shows the aggregate's newer verdict, so the tab never contradicts the requirement verdicts. A search box and a verdict filter apply to
    both tabs.
  - **New project** (`/projects/new`): the form behind `project.bootstrap` — creates the basic files,
    a git repository on `dev` with version tracking, and registers it under the workspace directory;
    a GitHub repository is created only when the box is ticked.
  - **Task list** (`/tasks`, 2026-09-23; sectioned 2026-09-24): shows every task in four sections,
    each with a count — **Queued**, **Running**, **Needs attention** (awaiting finish/skip, failed,
    stopped) and **Done** (finished, skipped, cancelled) — REQ-000000256. An `agent.run_task` Job
    with no run yet is listed from the Job itself (REQ-000000257): queued, awaiting confirmation or
    dispatched-but-waiting for the agent's single run lock (the agent creates the run only once it
    holds that lock) show as "waiting" under Queued; one that failed or was stopped before a run
    existed shows under Needs attention with the Job's error. Such rows are plain text rather than
    a link, since there is no run to open; a Job that has a run (matched by the run's `job_id`) is
    shown once, as that run.
  - **New task** (`/tasks/new`, absorbed Schedule task 2026-09-23): one form; a "When" choice
    (right away / once at a date and time / every week on chosen weekdays) decides whether it
    dispatches an `agent.run_task` Job immediately or files an Orchestration scheduled task — the
    same underlying scheduling this page's own "Schedule task" used to offer as a separate page.
    Scheduled agent tasks are listed on the same page as cards (2026-09-24); tapping one opens
    its details (`/tasks/schedule/{id}`: when, next and missed run, the task's title and
    description, its last Job) with the Remove button. **Queue task** (a task behind a
    predecessor) was dropped as a distinct feature.
  - **Jobs** (`/jobs`): previous, running and queued jobs of every type (unchanged panel; see below).
  - **New job** (`/jobs/new`, new 2026-09-23; absorbed Schedule job 2026-09-24): dispatch or
    schedule any job in the catalog (`dashboard/job_catalog.py`): choose a project, a job and that
    job's own arguments, then a "When" choice — right away / once at a date and time / every week
    on chosen weekdays, the same choice New task offers. Right away dispatches the Job in the
    background (`POST /v1/jobs`) and lands on Jobs; a schedule files an Orchestration scheduled
    task (`POST /v1/scheduled_tasks`) and returns to New job. The submit button reads "Start job"
    or "Schedule job" to match. The old separate Schedule job page is gone from the toolbar;
    `/jobs/schedule` now redirects to `/jobs/new` so old bookmarks still land. Scheduled jobs
    (excluding `agent.run_task` ones, which live on New task instead) are listed under the form as
    cards (2026-09-24); tapping one opens its details (`/jobs/schedule/{id}`: when, next and missed
    run, job type and arguments, its last Job) with the Remove button. The catalog's **Release**
    job (2026-09-24) takes its conditions as arguments — minimum delta and minimum patch bumps
    (numbers), how they combine (and/or, which must be chosen) and three checkboxes (all tests,
    all requirements, all policies pass) — shown under one "Conditions" heading; see
    `docs/pillars/version_control_release.md`'s Release job. A catalog job can be limited to some
    projects (`JobSpec.projects`; empty means all): **Update Standards Documentation**
    (2026-10-01, REQ-000000283..285) is offered only when the project chosen is `little-workshop`
    — the page narrows the Job list as the project changes, and the server refuses it for any
    other project. It takes no arguments and queues an `agent.run_task` Job (intent `docs`, manual
    review, little-workshop's own queue) whose instructions have Claude review every
    little-workshop standard, every registered policy and every registered project's own rules,
    then update the Markdown documents in `shared_project_standards/` and nothing else. Run it
    right away or schedule it like any job; the result is a branch for review on the Tasks page.
  - **Pillars** shows each pillar's full name (Policy & Compliance, Registry & Portfolio, …).
  - **New policy** (`/policies/new`, new 2026-09-23) and **Edit policy** (`/policies/{id}/edit`,
    new 2026-09-23): the same form — the rule in plain language, topic, target pillar, scope and
    gate points — submitted as a `policy.author` Job (`created_by: "user"`, `save_draft: true`,
    `source: "dashboard"`). Edit pre-fills the form from the existing policy and passes its id
    through so authoring produces a new draft linked to it (re-authoring, never a direct edit —
    the existing policy is untouched until the new draft is approved through the ordinary Gate 4
    flow). A failed authoring pipeline re-renders the form with the refusal reason and the user's
    own input preserved, never silently discarded.
  - **Settings** (`/settings`, new 2026-09-23, absorbed Needs attention): the attention feed that
    used to be its own page (`/attention`) now lives under Settings; `/attention.json` is
    unchanged as a JSON endpoint and the banner's link now points to `/settings`.
  - **Data catalog** was removed (2026-09-23) — it duplicated what each owning pillar's own Report/
    Pillars/Secrets panels already show live.
- **Docs — documentation review** (`/docs`, `/docs/{project}`, `/docs/{project}/view?path=`; 2026-09-28):
  under Actions, and a "Docs →" link on each project card. FastAPI's own Swagger/ReDoc pages are
  switched off on this app, since `/docs` is this page. Backed by Agentic Development
  (`docs/pillars/agentic_development.md`'s "Documentation review").
  - **Project page**: the branch being reviewed with a Check out control (`agent.docs_checkout`) —
    reviews happen on whatever branch the project's checkout is on; every markdown file with its
    open/done comment counts; and **Update docs**: send the branch's open comments to Claude
    (`agent.update_docs`), right away or once at a later time (a scheduled task), with a Remote
    Control checkbox. Only one update per project and branch: while one is queued, scheduled or
    running the form is replaced by that job, with Cancel (queued), Edit time/Remote Control or
    Delete (scheduled), or Stop (running — discards its worktree and every change in it). Recent
    finished updates show how many comments were done, the commit, and whether it was pushed.
  - **Viewer** (its own full-screen page, `templates/docs_view.html` + `static/review.js`/`review.css`):
    markdown rendered server-side (`dashboard/markdown.py`, markdown-it-py; raw HTML escaped) in dark
    mode, each block tagged with its source lines, headings with GitHub-style ids; links to other
    markdown files open in the viewer, `#anchor` links scroll within it, relative images are served
    through `/docs/{project}/image`. Every `/static/...?v=` URL carries the newest static file's
    modification time (it used to follow `app.css` alone, so a changed `review.js` was still served
    from the browser's and the service worker's caches). A branch picker in the header checks out another branch.
  - **Highlighting**: select text, then the floating yellow button (bottom right) highlights it; the
    same button on already-highlighted text unhighlights that part (splitting a highlight in two
    when it is the middle); a selection mixing both highlights all of it, merging what it touches
    into one highlight (their comments joined).
  - **Two pages, swiped**: documentation (default) and comments; swipe left for comments, right for
    documentation (or the header toggle, or ←/→). The comments page lists one slot per highlight in
    document order, separated by lines: white with the comment rendered as markdown, grey once
    `Done`, dark (the documentation background) when the highlight has no comment yet — tap a slot
    to write. Each page scrolls on its own; just before a switch the other page is scrolled so the
    same highlight and comment sit at the same height on screen — positions map piecewise between
    highlight *i* and slot *i*, the text between highlights mapping onto the separators, so the two
    pages' scroll lengths differ and vary along the document. Marks take the height of what is on
    the current page: a white mark on the documentation page's right edge spans a highlighted
    section that has a comment (shorter than a longer comment); the yellow strip on the comments
    page's left edge spans the comment (shorter than a longer highlight). Up/down buttons jump
    between highlights (documentation) or comments (comments page).
  - **Keyboard (desktop, 2026-09-28)**: ←/→ switch to documentation/comments; Ctrl+↑/↓ jump to the
    previous/next highlighted section (documentation) or comment (comments) and select it —
    stepping from the selected one — so Del can remove a highlight and Enter starts editing the
    selected comment (outlined in cyan); Ctrl+Enter stops editing a comment (so the arrows work again); Ctrl+Shift+Enter on the selected or
    edited comment switches to the documentation page with its highlight selected; a single click on a highlight selects all of its
    text, and Del removes the highlight from whatever highlighted text is selected. On the
    documentation page Ctrl+Shift+↑/↓ selects the previous/next paragraph (heading, list item, code
    block, table row — highlighted or not), Enter on selected text does what the floating button
    does (highlight/unhighlight) and leaves that section selected, and Ctrl+Enter on a selected highlight switches to the comments
    page editing its comment — on selected text that is not highlighted yet, it highlights it first,
    in one go. On a desktop-sized screen with a mouse, a column on the left lists the
    shortcuts for the current page only. The page off
    screen is `inert`, so Tab never walks into it (it used to, and the browser scrolled the two
    pages sideways out of place).
  - **Certified sections**: Ctrl+I (or the green floating button above the yellow one) toggles
    "Certified" on the selected highlight — on selected plain text it highlights it first — or, on
    the comments page, on the selected comment. A certified highlight is dark green (light text), its
    right-edge mark and comment strip green, and its comment slot reads an automatic, read-only
    "Certified."; it cannot be unhighlighted, edited or finished until the certification is lifted.
    The reviewer's own comment is not lost: it is kept in the comments file (`certified: true`),
    just not shown, and comes back when the certification is lifted. Certified sections never go to
    Claude with a comment (see Agentic Development).
  - **Links in comments** behave like the document's own: `#anchor` (or the document's own file
    with an anchor) switches to the documentation page at that section, a relative link to another
    markdown file opens it in the viewer, and only external links open a new tab.
  - **Autosave**: 2 seconds after typing stops (and at once when the page is hidden, with
    `keepalive`, so switching away from the installed app loses nothing), with a small
    half-transparent little-workshop icon at the top right while a save is in flight.
  - **After an update**: a section the job edited is highlighted blue, and its comment's strip is
    blue; a section it removed shows a red ✖ where it was. `Finish` on a done comment deletes it and
    its highlight; editing a done comment makes it open again, its highlight becoming the (yellow)
    section the job changed. A comment the running job is working on is read-only.
- **Tasks** (`/tasks`, `/tasks/new`, `/tasks/{project}/{run}`):
  Agentic Development's runs across every project (including tasks still queued, shown as
  "waiting"), a New task form (`agent.run_task`, immediate or scheduled), and per-run detail with
  the task description as the user wrote it (with a Copy button), the change report and Finish/Skip for a run awaiting review. A failed one-off task (not one launched by a weekly schedule) also has Retry: a page that restarts it right away or schedules it once, never on a recurrence.
- **Pillars** (`/pillars`, redesigned 2026-09-21): one tappable card per
  pillar, no page heading — the pillar's name is the card's header, with
  its health pill beside it and one compact line below (deployed commit
  SHA and version, "behind" when that differs from this checkout's HEAD,
  crash count, interface version; "initial install" if never redeployed
  through Deployment). Tapping a card selects it (a visually hidden
  checkbox inside a `<label>`, so it submits without JavaScript);
  disabled pillars are dimmed and unselectable. A sticky bar holds a
  **commit dropdown** — the checkout's 10 newest commits, each shown as
  `sha · vVERSION · subject` (from Deployment's `GET /v1/pillars`, which
  reads each commit's `VERSION`; falls back to `HEAD` if unavailable) —
  plus Select all/Clear and **Redeploy N** (`POST /pillars/redeploy`):
  one `deployment.redeploy_pillar` Job per selected pillar, submitted in
  sequence from a background thread with Orchestration/Deployment/Core
  last, since redeploying those interrupts the redeploy chain itself.
  `/jobs` and `/policies` hang off the More tab. **Secrets** (2026-09-22): a
  card above the pillar grid, one row per configured secret (today: the
  host's `gh` CLI token) with a valid/invalid pill and, when it has one,
  its expiration date — never the secret's own value (Registry &
  Portfolio's `GET /v1/secrets`); hidden entirely when nothing is
  configured.
- **Incidents** (`/incidents`, `/incidents/{id}`; 2026-09-22) — an
  unhandled exception, anywhere in this ecosystem, awaiting the user's
  confirmation before it becomes a bug task. The list (status filter:
  open/confirmed/dismissed/all) is one card per incident — exception type,
  raising component, message, project, age. A card's own page shows the
  full traceback (open by default) and, while still open, a review-mode
  choice (auto-approve or manual, defaulting to auto-approve) with Confirm
  (`agent.confirm_incident`, starting a real bug-intent task and linking
  it here) or Dismiss (`agent.dismiss_incident`, discarded, no task). An
  open incident also appears on **Needs Attention** as a "bug" chip. See
  `docs/pillars/agentic_development.md`'s own "Incidents" section for the
  reporting side.
- **Policy drafts** (`/policies/drafts`, `/policies/drafts/{id}`; 2026-09-21)
  — where Gate 4 happens. `/policies` shows a banner ("N policies
  awaiting your approval") linking to the list: one card per pending draft
  (topic, full text, source, target pillar, "refined", and a "check notes"
  pill when the review notes flag a concern), with rejected drafts folded
  away and restorable. A draft's page is cards: summary; **Review notes**
  (amber border when flagged); the **Test script** in full, open by
  default since it is what is being approved; **Gate points** (none = audit
  only, pre-checked from the draft's own recommendation); **Apply to**
  (every registered project, each with a Gating switch — 2026-09-22: a
  project the draft itself recommends comes pre-checked, with its Gating
  switch set from the draft's own value, a "pre-filled from the draft's own
  recommendation" note shown when it has one; still just a starting point —
  the reviewer's own choice, submitted with the decision, is what takes
  effect); and a decision card with an optional rejection reason and
  Approve / Reject. Pressing **Approve** is itself the confirmation
  (2026-09-22: the earlier separate "I have reviewed..." switch was
  dropped as redundant friction) — one `policy.approve_draft` Job carries
  the whole decision, and the page then opens the next pending draft, or
  the Policies page once none remain. This is the only way to approve a
  draft — no CLI or MCP path exists.
- **Jobs** (`/jobs`, redesigned 2026-09-21): the Orchestration queue as a
  list of expandable cards, newest first, phone-first (no table, no page
  heading). A search box (type, project, pillar, creator or id) and a strip
  of status chips with counts — All, Running, Needs confirmation, Queued,
  Failed, Succeeded, Cancelled, only those that exist; the strip scrolls
  sideways on a phone. A card shows the job type (namespace dimmed), a
  status pill, the project, how long ago it was created, how long it took
  once finished, who created it, and a failed job's error in red; the left
  edge is coloured by status. Tapping a card expands the id, target,
  queue, timestamps, what it waits on, and the params, result and error as
  formatted blocks. 50 jobs per page with "Show more" (filters preserved).
  The list follows the page's live stream (2026-10-04, replacing a re-fetch
  every 5 seconds while jobs were active): it changes as the jobs do, keeping
  the cards that are open. An `agent.run_task` card also opens its task.
  Filtering and searching are done
  on the full list, so the counts always describe everything.
  **Delete old jobs**: a collapsed panel above the filters. Pick an age —
  12 hours, 2 days, 1 week, 4 weeks, 8 weeks or 24 weeks, each showing how many
  finished jobs it would delete (an age with none is disabled) — and the
  button reads "Delete N jobs"; a native confirmation asks before it goes
  through (`POST /jobs/cleanup` → Orchestration's `POST /v1/job-cleanup`,
  which refuses any other age). Only finished jobs are ever removed, and
  the result message says how many were deleted and how many were kept
  because active work still depends on them.
- **Policy management** (`/policies`, `/policies/{id}`) — the concrete,
  fully-specified panel `docs/milestones.md` M14 called out. The list
  (redesigned 2026-09-21) is one card per policy, no page heading: the
  topic as the card's header with an enabled/disabled pill, the full
  `ears_text` beneath it, and a row of chips — one per gate point (or
  "no gate"), the scope, the target pillar, and who created it. The
  whole card opens the policy; a disabled policy is dimmed. The chips
  show how many projects the policy applies to (or the one project's
  name) and, when any, how many have gating on, in a neutral chip
  (2026-09-22: shown as "gating on" rather than "gating off", since a
  reviewer asked what an amber "gating off" chip meant and it turned out
  gating on was the more useful count to see at a glance — per-project
  detail is on the policy's own detail page's Projects card). The
  policy's detail page (also redesigned 2026-09-21) is four cards, each
  headed by an icon and a one-word title rather than a boxed sentence:
  a **summary** (topic, status pill, the full `ears_text`, chips for
  target pillar, author, short id and amend date, and an **Edit** button
  to `/policies/{id}/edit` — 2026-09-23 — the same authoring form as New
  policy, pre-filled, that re-authors the policy as a new draft rather than
  changing it directly); **Gate points** — an
  `enabled` switch and the ten gate points as tappable chips (multi-select
  or none) with one Save; **Test script** — a link card to the exact file
  on GitHub, at the commit whose blob equals the script the policy
  actually runs, showing that commit's `VERSION` and short SHA (Deployment
  finds it by git blob hash under `tools/policy_scripts/`; a script that
  lives only in the Policy database falls back to a collapsed inline code
  card; editing is `lws policy activate`'s own job, Gate 4's
  human-review step — never bypassed here); and **Projects** — every
  registered project as a tappable row (selected = the policy applies to
  it) with a per-project **Gating** switch, one Save (`policy.set_projects`,
  the whole selection at once). Gate points are never chosen per project.
  Backed entirely by `policy.configure`/`policy.set_projects` Jobs and
  reads of `GET /v1/policies` — no new pillar mechanism beyond Deployment's
  `POST /v1/policy-scripts/locate`.
- **Unreachable-pillar handling**: since this app holds no domain data
  and applies no local fallback (unlike the CLI, which falls back to a
  direct database read when a daemon is down — a Dashboard user is
  already looking at a live page served by a live process, so "the
  pillar behind this panel is down" is simply the real answer to show,
  rendered as a visible banner, never a 500).

## Job page: the checklist (2026-10-03)

REQ-000000312.

`/jobs/{id}` shows one job and what it does: the ordered actions, an icon per state (empty circle
not started, blue running, green finished OK, red did not finish OK, yellow needs your attention),
each action's runtime (running ones count up; an action waiting for the user holds its runtime) and
its expected runtime, and for an action waiting for the user its question with one button per
option (`POST /jobs/{id}/steps/{key}/answer`); unanswered, the job fails after 12 hours. The page
follows its live stream (topic `job:<id>`; it used to refresh itself every 2 seconds while the job was
active). Each Jobs-page card shows `done/total actions` and the
current action, flags a question, and links to the page; a running job with a question is also a
card in the attention list. An `agent.run_task` job's page links to its task (REQ-000000377).

While the job is running or queued the page has a Stop job (queued: Cancel job) button, REQ-000000318:
`POST /jobs/{id}/stop` asks Orchestration to stop it and returns to the page, which shows the reason
when the job cannot be stopped (a declared-unstoppable type, an unreachable pillar).

**Force stop** (2026-10-04, REQ-000000366): a running job's page (and its task's) has a folded
"Force stop…" panel, opened by itself after a refused stop, that says what forcing means — the job is
settled as stopped without its pillar's agreement, whatever the pillar was doing carries on and is not
undone, its later outcome is ignored, the jobs waiting on it fail and its queue is released at once —
and a Force stop button behind one more browser confirmation (`POST /jobs/{id}/stop` with `force=1`;
Orchestration's stop with `force`, `docs/pillars/orchestration.md`). It is there for a job whose worker
died, as the `agent.run_task` of 2026-10-04 that held little-workshop's queue for hours. `next` (a local
path) says which page to return to.


## Interfacing

- **Calls**: every other pillar's Pillar Interface, read-only for
  status/listing panels; Orchestration's `POST /v1/jobs` for every
  mutation (`policy.configure`, `policy.set_projects` today — the same
  pattern any future panel's own
  mutations would follow). `dashboard/client.py` is a deliberate
  duplicate of `cli/client.py`'s and `mcp_server/client.py`'s HTTP glue,
  never a shared import — the same "no shared Python import path
  between two `src/` trees" rule every other pair of pillars in this
  project already keeps, extended here to its third thin proxy.
- **Called by**: a human's browser, through a reverse proxy in front of
  this pillar's Unix domain socket — the identical `tailscale serve`
  model `little-runner`'s own dashboard already uses today. Exposure
  configuration (which reverse proxy, which Tailscale ACL) is a
  deployment-time concern this app doesn't solve itself, the same way
  `docs/architecture.md`'s PI section already treats "never a network
  port" as an absolute rule with browser-facing exposure handled
  entirely outside the pillar process.

## Data model

No domain data. The Dashboard stores two things of its own, both under `<state>/dashboard/`:

- `prefs.json` (`dashboard/prefs.py`): the user's display preference, the order of the project
  cards on the Dashboard. A missing or damaged file means the default order.
- `statistics.db` (`dashboard/statistics.py`, SQLite): the per-day counts behind the Statistics
  page, described under "Statistics" below.

Everything else on a page is a live read of the pillar that owns it.

## Left open

- **Per-pillar panel coverage is partial, deliberately**: Overview,
  Projects, Jobs, and the fully-specified Policy panel are real and
  working; Version Control & Release, Build & Packages, Requirements &
  Quality, Deployment & Servers, and Agentic Development don't have
  their own dedicated panels yet. `dashboard/client.py`'s HTTP glue
  pattern makes adding one for any of them a small, additive change —
  not a redesign — whenever that pillar's own dashboard need is
  concrete enough to justify one, the same "value scales with what
  there already is to show" reasoning that placed this milestone last.
- **Browser-facing exposure** (which reverse proxy fronts the Unix
  socket, Tailscale ACL specifics) is real future work for whoever
  deploys this pillar for real, not solved by this pass — matching
  `little-runner`'s own existing `tailscale serve` reliance, external to
  its own app.

## New task: include shared project standards (2026-10-01)

The New task form (also the scheduled and retry paths) has an **Include shared project standards** checkbox,
checked by default (REQ-000000288). A hidden `include_standards=0` input precedes the checkbox so an unchecked box
still posts; a request that omits the field counts as included. The value is stored as the boolean
`include_standards` in the `agent.run_task` params, and a retry reuses the original run's value.

## Deciding policy-gated jobs; tagged attention cards (2026-10-01)

A Job held in `pending_confirmation` by a failed policy gate has three buttons (REQ-000000290, 291), on its
Jobs-page card (which opens by default) and on its Settings attention card: **Confirm anyway**
(`POST /jobs/{id}/gate/confirm`, behind a browser confirm; Orchestration `policy_confirm`), **Cancel**
(`.../cancel`; Orchestration `stop`, status cancelled) and **Mark as failed** (`.../fail`; Orchestration
`policy_confirm_timeout`, status failed, error "policy check unconfirmed"). Each releases the job's queue key.
The form's `next` field (a local path only) says where to return, with the outcome as the flash message.

Each Settings "Needs attention" card carries a tag chip (REQ-000000292): **Policies** (a failing check), **Jobs**
(awaiting confirmation) or **Incidents** (an open bug). `/attention.json` items carry the same `tag`.

## Failed tasks leave the attention list (2026-10-03)

REQ-000000313.

A failed (or stopped) task is listed under Needs attention with **Move to done** and **Retry**
buttons (the task page offers both too). Moving it runs the `agent.dismiss_task` job, which sets
`dismissed_at` on the run; the run keeps its `failed` status, so Retry stays available from the Done
list. A task that failed more than a week ago (`TASK_ATTENTION_DAYS`, measured from the run's last
update) is listed under Done by itself, with no job: the grouping is computed on every page load.
A failed job that never became a run has no button, and moves by age only. `awaiting_finish` tasks
are not affected: they still wait for Finish or Skip.

## What a job waits on, in words (2026-10-03)

REQ-000000312.

A job's "Waits on" (the Jobs-page card and the job page) lists each dependency by what it is, not by
id: the job type and project (`policy.check_project · little-workshop`), or `Task: <title> · <project>`
for an `agent.run_task` job that has a run. Each is a link, to the task page or the job page, and
carries the state word and colour of that job (green succeeded, red failed, blue running, yellow
awaiting confirmation, grey queued, cancelled or stopped). A dependency already deleted from the job
history is named as such, without a link.

A job's card (and page) also says what it is about (2026-10-03): a policy job shows the policy it runs
(its topic and EARS text, read from the Policy pillar by the job's `policy_id`, or from the job's own
`topic`/`ears_text` when it authors one); a version job shows its branch (`improvement/x → dev` once
the finish has checked it, or the branch a start creates) and the version number, as a transition
(`0.2.32 → 0.2.33`) when it changes, taken from the version steps' details. The state word of a
dependency is only a tooltip: its colour says it.

## Statistics (2026-10-03)

`/statistics` (System → Statistics) plots little-workshop's operations per day. The counts are kept
apart from the pillars, in `<state>/dashboard/statistics.db`, because Orchestration's job cleanup
deletes old Jobs and the plots must outlive them.

- **Updating** is automatic, never asked for: on every visit of the page, every ten minutes while
  the Dashboard runs (a background thread started with the app, `STATISTICS_UPDATE_SECONDS`), and
  before old jobs are deleted. Each time the Dashboard reads the policies (Policy & Compliance, without
  their scripts) and incidents (Agentic Development) the pillars hold, the summaries of the Jobs
  (Orchestration) changed since the last update and the policy check results recorded since then, each
  less ten minutes; once a day it reads all the Jobs and results (2026-10-05, REQ-000000383,
  REQ-000000391). It adds each one it has not counted to its day. A `seen` table remembers what was
  counted, so updating twice counts nothing twice; an id the pillar no longer holds is forgotten, its
  counts stay (Jobs and results only by the daily read of all of them). A
  test run's summary keeps its verdict counts; one from before runs answered with counts is read whole
  to tell whether a test failed. A Job is counted
  as triggered when first seen and as succeeded or failed once it has ended (a cancelled Job is
  only triggered). An unreachable pillar's numbers are left as they were and the page says so.
  Deleting old jobs on the Jobs page (`POST /jobs/cleanup`) updates the statistics first, so a
  job is counted before it goes and the deletion never changes a plot. The file is never rebuilt
  in normal use; only a change of how something is classified (in `dashboard/statistics.py`)
  leaves older days under the old classification.
- **Days, projects and pillars**: a day is the local date of the Job's creation, the check or the
  incident. Every row carries two separate columns the page filters by: `project`, the project it
  is about, and `pillar`, the pillar that did it (a Job's target pillar; Policy & Compliance for a
  policy check; the pillar an incident was raised in, `unknown` when that is not a pillar). Only a
  project registered in Registry & Portfolio when the row is counted is a project; anything else a
  Job names there (a pillar on a pillar redeploy, the workspace session, a mistyped id) and no
  project at all are stored as an empty `project`. While the registry cannot be read nothing is
  counted, and the next update catches up.
- **What is stored**, one row per day, project and pillar:
  - `job_days` (per job type): `triggered`, `succeeded`, `failed`, and
    `duration_seconds` with `duration_count` for the finished runs (creation to end, waiting in
    the queue included). All three counts are kept so a divergence can be looked into.
  - `policy_days` (per policy topic): `passed`, `failed`, `not_applicable`.
  - `incident_days`: `incidents`.
  - `test_days`: `succeeded`, `failed` test executions. An execution is one
    `requirements.run_tests` or `requirements.import_junit` Job; it failed when the Job failed or
    any of its tests has the verdict failed or error.
  A store written before project and pillar were separate columns (2026-10-03) is dropped when
  first opened and recounted from what the pillars hold.
- **The page** embeds the rows and draws everything in the browser (`static/statistics.js`, inline
  SVG, no library):
  - One **project or pillar** choice for all plots, at the top, as two separate lists of which
    one applies at a time: a project shows everything about that project whichever pillar did it,
    a pillar shows everything that pillar did whichever project it was for. Picking in one list
    sets the other back to all.
  - One **time period** for all plots, picked as two days on a month calendar (or Last 7 days, Last
    30 days, Everything). A calendar day is brighter the more jobs it had, relative to the other
    days of the month shown.
  - **Jobs triggered** (bars), **Job success ratio** (succeeded / (succeeded + failed)), **Policy
    fail ratio** (failed / (passed + failed); not applicable checks are left out), **Job duration**
    (average per day), **Incidents** (bars) and **Test success ratio**. The job and policy plots
    have chips to select or deselect jobs or policies. A plot shows the sum over what is selected.
  - Hovering or tapping a day shows its exact numbers (for a ratio, both counts); a day with
    nothing to measure is a gap in a line, not a zero. Each plot has a table view.


## Backlog (2026-10-04)

REQ-000000372, REQ-000000373.

Documentation → Backlog (`/backlog`) lists every project with how many entries its backlog holds; a
project's page (`/backlog/{project}`) has a form to add an entry, as written, and the entries as cards
(title, the start of the primary entry, and whether it is as written, refined, has its task prompt, or
Claude is working on it). An entry's page (`/backlog/{project}/{entry}`) shows its four fields:

- **Primary entry**, editable and saved as is;
- **Refinement notes**, editable and saved;
- **Refined entry**, rendered from its Markdown (its headings under the page's own), with **Refine
  entry**: it saves both fields, then starts `registry.backlog_refine`, which has Claude write a new
  refined entry from the primary entry, the current refined entry and the notes, and replaces it;
- **Task prompt**, with a copy icon at its top right corner and **Generate task prompt**
  (`registry.backlog_task_prompt`, from the refined entry; it ends with the instruction to delete
  the entry once the work is done). Disabled until the entry is refined.

Both have Claude read the project's checkout, which it cannot change (since 2026-10-05,
`docs/pillars/registry_portfolio.md`, "Backlog"); the task prompt's card says so.

While Claude works the buttons say so, the entry's page shows the session as it grows (its Claude
peek), and a failed attempt's error shows until one succeeds. The entry itself records this (Registry &
Portfolio, REQ-000000385), so the backlog pages read no Jobs; a mark older than fifteen minutes (past the
630 s Orchestration waits for either Job; ten minutes until 2026-10-05, when Claude only had the entry's
text) is shown as an attempt that did not finish.
**Launch task** opens New task (`/tasks/new?backlog=<id>`) with the entry's title and its task prompt
in the Task box, the text written for the task and the only one that ends by having it delete the entry;
while there is no task prompt yet, the refined entry, and while the entry is not refined, the primary
entry, the form saying which (until 2026-10-05 it gave the refined entry, so a launched task never
deleted its entry). **Delete from backlog** asks first, then
deletes the entry and everything it holds. The pages follow the `backlog` topic, so an entry refined,
prompted or deleted elsewhere (`lws`, an MCP tool, Claude at the end of a task) shows at once, and a
deleted one says it is gone. The store is Registry & Portfolio's (`docs/pillars/registry_portfolio.md`,
"Backlog").

**Make it a task** on an automation candidate (Claude usage page) works the same way: it opens New task
(`/tasks/new?candidate=<id>`) filled with the candidate's task — title, kind `task`, review and the
description the Usage pillar writes — instead of starting it at once; starting it there tells the Usage
pillar the candidate's task has started.

## Schedule (2026-10-04)

REQ-000000375.

Actions → Schedule (`/schedule?month=YYYY-MM`) is a month calendar, Monday first, of the scheduled jobs
and tasks (`dashboard/calendar.py`): their runs come from Orchestration's Job history (the Jobs the
scheduler created), on the local day each was created, and what will run from the scheduled tasks — a
once task on its day, a weekly one on each of its days from its next run on; a missed firing shows on its
day as skipped. **Everything** (`&show=all`) adds every other job and task that ran (a branch finish alone
creates dozens of policy checks, hence not by default). A Job deleted from the history, one cancelled
before it ran and a removed schedule are not shown.

A day shows symbols, not items (2026-10-05): a triangle for tasks (`agent.run_task`), a circle for jobs,
filled with the state's colour — succeeded green, failed red, running cyan (pulsing), skipped grey (a
stopped job, a skipped or stopped run), scheduled hollow and dashed (queued, or still to come). A task's
state is its run's (a run awaiting review counts as succeeded). A symbol is never repeated: there is one
per kind and state, with the number of items it stands for inside when there are several (`99+` above
99), tasks first, then jobs, each in the order succeeded, failed, running, skipped, scheduled
(`calendar.symbols`).

A day is one button and nothing on the calendar is a link: a tap anywhere on a day lists all its items in
a panel beside the calendar on a wide screen and under it on a phone, each a line (symbol, time, title,
state) that expands and collapses on its own, so several can be open at the same time. Expanding an item
reads its details (`/schedule/item/job/{id}`, `/schedule/item/scheduled/{id}?at=`): a job's checklist,
error and links to the job and its task, or a schedule's times and template with a link to its page,
where it is removed. The day's sheet travels with the month (a `<template>` in the day), so listing a day
reads nothing more. The page follows the `schedule` topic: when the month changes, the panel lists its
day anew, keeping open the items that were open.

The look (2026-10-05) is the Dashboard identity's — Ledger's structure, Mission Control's colours — with
depth: a title block (the month, ‹ Today › and a Scheduled | Everything switch); the month at a glance as
the dashboard's five stat tiles, one per state with its tasks and jobs (`calendar.totals`, the month's
own days only), which are also the colours' legend; the days as tiles raised off a board (weekends
tinted, past dates dimmed, days of the months around recessed, today's date a cyan pill with a glow);
symbols lit from above with a soft shadow, a failed one with a red halo and a running one with a ping
(succeeded green and running cyan are close, so running also moves: still, a ring, under reduced motion);
and the day sheet: the date, how far it is from today, a pill per state with its count, and the items on
a timeline, their details opening in a recessed well. The month that holds today opens with today
listed; Today lists it again without reloading.

## Policy filters (2026-10-04)

REQ-000000376.

The Policies page has a filter bar: a text matched against each policy's name and description together,
as a glob (`*` any text, `?` one character; without either, anywhere in them; case ignored), the status
(enabled, disabled, failing), gate points and projects governed (dropdowns of checkboxes; several match
any of them), and "Gating on in a project". The filters combine and live in the address
(`?q=&status=&gate=&project=&gating=1`): the server applies them to the page it sends, and the page
applies the same rules as they change, without reloading, showing "N of M policies".

## A task and its job (2026-10-04)

REQ-000000377. A task's page names the Job that ran it, with its state and a link; an `agent.run_task`
Job's page and its Jobs-page card link to its task (Agentic Development's `GET /v1/runs/by-job/{job}`).
A running task's page has the job's Stop and Force stop.

## Claude usage: pointing at a day (2026-10-04)

REQ-000000374. Pointing at (or tapping) a Claude usage plot marks the day under the pointer with a
dashed line and shows a box with that day's date and the value of every series drawn — each with its
colour, comparison weeks included, and the total of a stacked plot — as the Statistics page does. It
replaces the browser's tooltips on single bars and dots.

## Live pages (2026-10-04)

REQ-000000379.

Pages no longer refresh themselves on a timer. Each page names the kinds of information it shows (its
topics, `<body data-live>`) and keeps one server-sent event stream, `GET /live/events?topics=…`
(`dashboard/live.py`); when one of its topics changes, `static/live.js` fetches the page once and
replaces each `[data-live-region]` whose content differs, keeping open `<details>`, never touching a
region the user is typing in or has edited, and never fetching more often than every 1.5 seconds (4 on
the Dashboard, whose cards cost several reads each). Plots are drawn again from their new data, keeping
what the user chose. A tab out of sight lets its stream go and catches up once when it is looked at.

| Page | Topics |
|---|---|
| Dashboard | `dashboard` |
| Jobs; a job | `jobs`; `job:<id>` (and its peek while it runs) |
| Tasks; a task | `tasks`; plus its `job:<id>` and peek |
| Schedule | `schedule` |
| Policies; Incidents; Settings | `policies`; `incidents`; `attention` |
| Statistics; Claude usage | `statistics`; `usage` |
| Backlog pages | `backlog` (and an entry's peek while Claude works on it) |
| Pillars | `pillars` (pillar health, from Core; a redeploy) |
| every page | `attention` (the banner only) |

The Dashboard learns of changes as cheaply as each pillar allows, and only while some page is open:
Jobs, steps and scheduled tasks from Orchestration's change feed (`GET /v1/changes`, a long poll
answered the moment something changes, `docs/pillars/orchestration.md`); the backlog, Claude usage and
incidents from a version string each pillar serves, read every 3 seconds while a page shows them (and
the pillars' health from Core's status, while the Pillars or Dashboard page is open, REQ-000000388); the
Statistics store is counted again when a Job has settled, at most every 15 seconds, while that page is
open.

## Claude peek (2026-10-04)

REQ-000000380.

A task's page and its job's page (an update-doc job's too) have a **Claude peek** card: what the session
has done so far, read from its transcript by Agentic Development (`GET /v1/peek/{job}`,
`docs/pillars/agentic_development.md`) — each message, each tool call with the gist of its input, and
each failed result in red — newest at the bottom. Since 2026-10-05 (REQ-000000386) a Job that asks
Claude a short question through the agent (a backlog entry's refinement or task prompt, a policy's
authoring) has the card too, and so has the backlog entry Claude is working on. While Claude works the card says "live" and grows as
the session does: the page's stream carries each new batch (topic `peek:<job>`), and a batch that does
not follow on from what is shown is caught up from `/jobs/{id}/peek.json`. Remote Control remains the
way to talk to a running task; the peek only shows it.

## Reading only what is shown (2026-10-05)

REQ-000000383. Every list of Jobs reads them as summaries (each Job's brief in place of its params and
result, `docs/pillars/orchestration.md`, "Job summaries and filtered lists"), filtered by Orchestration:
the attention banner the Jobs awaiting confirmation or running; the launch buttons the queued and running
deploys and session starts; Tasks the task Jobs that may lack a run; a project's docs its active
update-doc Jobs; Schedule the month's Jobs (the scheduler's, unless Everything is chosen); what a Job
waits on the Jobs it names. The Jobs page reads the counts per status (`GET /v1/jobs/counts`, with the
search for the total) and the one page it shows, newest first, and how many Jobs each cleanup age would
delete without any Job's content (REQ-000000389); Statistics read only what changed. The attention banner
reads the latest failing policy results and the policies without their scripts, the policies pages the
latest results (REQ-000000390), and Statistics the policy results recorded since their last count
(REQ-000000391). The
Jobs page and a job's page show at most 20,000 characters of a Job's params or result (`JSON_SHOWN`),
then say how much there is, and name what a summary left out; both link to `/jobs/{id}.json`, the whole
Job.

## Policy bypass warning (2026-10-05)

While the Policy pillar's `bypass_policies` flag is TRUE (`GET /v1/bypass`), every page shows a bar under the
navigation: "bypass_policies is TRUE", the remaining time counting down in the browser (the page reloads when it
reaches zero), and a "Set to FALSE now" button posting to `/policy-bypass/off`, which sets the flag back at once. The
Dashboard never raises the flag. See `policy_compliance.md`, "Policy bypass".

## Status for Claude Code (2026-10-06)

REQ-000000411, REQ-000000412. What a Claude session shows of the workshop, read from the Dashboard:

- `GET /status.json?project=<id>` (`collect_status`): for the project, its server (`null` without a deployment
  manifest), its running Jobs, its tasks in the Tasks page's "Needs attention" group, its Jobs failed in the last
  2 hours, its active scheduled tasks due within the next hour and its failing policies (as the attention list
  decides it, `_failing_checks`); across every project, the open incidents of the last 2 hours and the pillars Core
  sees `crashed` or `faulted`. Each topic is `{count, items}` (the first 20), read filtered from its owner, all reads
  at once; a pillar that does not answer is listed in `unavailable` and its topics' `count` is `null`. A project the
  Registry does not know gets the workshop's topics alone. The status mod reads it every 30 seconds per session that
  something draws (`docs/pillars/usage.md`, "Configuration").
- `GET /status/<project>`: the same topics in detail for the Windows app's Browser pane (Ctrl+Shift+B, then this URL
  on the Dashboard's address) and any browser: a tile per topic with its count, zeros included, then a panel listing
  what is behind each count above zero, linked to its page; a group with nothing to list says so in one line. It
  follows the jobs, tasks, schedule, policies, incidents and pillars topics live and looks again every minute, since
  its windows move with the clock (`window.liveRefresh` in `static/live.js`). The icons are the phone's.
