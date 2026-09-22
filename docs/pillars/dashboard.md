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
  banner re-reads the feed every 20 seconds without reloading, and once the
  user taps "Alert me on this device" (browser notification permission) it
  raises a notification for each item that newly appears. A pillar that is
  down contributes nothing. This is the Dashboard half of "informing the user
  immediately" (REQ-000000127); GitHub-issue and Remote Control delivery
  are not built.
- **Workbench additions (2026-09-21, Control merged into Dashboard 2026-09-22)**:
  - **Navigation** is grouped. The bottom toolbar has Dashboard, Tasks and System; a group
    button reveals its pages (Dashboard: Dashboard, Report, New project; Tasks: Task list, New task,
    Queue task, Schedule task, Jobs; System: Pillars, Policies, Attention) with Back returning to
    the main row, and the group of the current page stays open. On a wide screen the top bar holds
    the three groups and a sub-navigation row lists the active group's pages.
  - **Arrange cards** (`/?arrange=1`, `POST /order/dashboard/{project}/{up|down}`): the Dashboard
    keeps its own order of project cards; a project the user has never placed goes after the
    placed ones.
  - **Workspace session** (Dashboard, top panel, above the project cards): starts and stops one
    Claude Code session in the projects root directory (`~/little-projects`, the parent of the
    workshop checkout; `LITTLE_WORKSHOP_WORKSPACE_DIR` overrides), under the reserved project id
    `workspace`, so Claude can work across projects. The root must be trusted by Claude Code like
    any project.
  - **Report** (`/report`): for one project, Requirements and Tests as two tabs. The Requirements
    tab lists every requirement as a collapsible card — key, title and verdict always visible,
    description shown only when expanded. The Tests tab groups tests into collapsible suites, one
    card per test with its name and latest verdict; for a self-application project (whose own
    `docs/requirements.md` carries no per-requirement test mapping — REQ-000000149's own honest
    gap), that grouping is by real test module (`tools/import_requirements.py`'s
    `import_module_suites` — `test_foo.py` becomes suite `test_foo`), never the one all-tests
    bucket (`AGGREGATE_SUITE_NAME`, `dashboard/app.py`) that suite is linked to a requirement's
    own done/passing state and is hidden from this tab so it doesn't duplicate every test under
    one giant heading (REQ-000000215, correcting REQ-000000212's own "same suite grouping"
    wording — module suites are never linked to a requirement, so they don't affect done/passing).
    A search box and a verdict filter apply to both tabs.
  - **New project** (`/projects/new`): the form behind `project.bootstrap` — creates the basic files,
    a git repository on `dev` with version tracking, and registers it under the workspace directory;
    a GitHub repository is created only when the box is ticked.
  - **Queue task** (`/tasks/queue`): a task that starts only after a chosen unfinished agent task
    has succeeded (a job with `depends_on`); the form refuses to submit without a predecessor.
  - **Schedule task** (`/tasks/schedule`): a scheduled task on Orchestration whose job starts an
    agent task once at a chosen local time or on chosen weekdays each week; scheduled agent tasks are
    listed and can be removed.
  - **Pillars** shows each pillar's full name (Policy & Compliance, Registry & Portfolio, …).
- **Tasks** (`/tasks`, `/tasks/new`, `/tasks/{project}/{run}`):
  Agentic Development's runs across every project, a New task form
  (`agent.run_task`), and per-run detail with the change report and
  Finish/Skip for a run awaiting review.
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
  `/jobs` and `/policies` hang off the More tab.
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
  effect); and a decision card with an "I have reviewed the policy text and
  the test script" switch, an optional rejection reason, and Approve /
  Reject. Approve without the switch does nothing (the route sends no Job);
  with it, one `policy.approve_draft` Job carries the whole decision. This
  is the only way to approve a draft — no CLI or MCP path exists.
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
  While anything is running, queued or awaiting confirmation the page marks
  itself "live" and re-fetches its list every 5 seconds — never while a card
  is expanded or the search box has focus. Filtering and searching are done
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
  name) and, when any, how many have gating off. The
  policy's detail page (also redesigned 2026-09-21) is four cards, each
  headed by an icon and a one-word title rather than a boxed sentence:
  a **summary** (topic, status pill, the full `ears_text`, chips for
  target pillar, author, short id and amend date); **Gate points** — an
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

No database. The one thing the Dashboard stores is the user's own display preference — the order of
the project cards on the Dashboard — in `<state>/dashboard/prefs.json`
(`dashboard/prefs.py`). A missing or damaged file means the default order. Everything else on a
page is a live read of the pillar that owns it.

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
