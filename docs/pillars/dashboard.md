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

- **Dashboard** (`/`, redesigned 2026-09-21): a project-management
  overview, no longer a pillar table — stat tiles (projects, servers
  running, Claude sessions, active tasks, pillars healthy) and one row
  per registered project: server up/down, Claude Code session, tasks
  awaiting finish/running, requirements done/total, latest CI. Each
  panel is its own live read; a pillar that is down renders as "—" for
  that panel only, never a 500.
- **Control** (`/control`): per-project accordion, copied in shape from
  `little-runner`'s own Control page — deploy a branch (Launch/
  Relaunch) or Stop the server, **Start/Stop a Claude Code session**
  (`agent.session_start`/`session_stop`; refused with an explanation
  when the project isn't yet trusted by Claude Code), and see the
  project's recent tasks. Deploys are submitted from a background
  thread, since a cold `bazel run //:install` outlasts a browser
  request; progress is on the Jobs page.
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
  only); **Apply to** (every registered project, each with a Gating
  switch); and a decision card with an "I have reviewed the policy text and
  the test script" switch, an optional rejection reason, and Approve /
  Reject. Approve without the switch does nothing (the route sends no Job);
  with it, one `policy.approve_draft` Job carries the whole decision. This
  is the only way to approve a draft — no CLI or MCP path exists.
- **Jobs** (`/jobs`): the Orchestration queue, optionally filtered by
  status — the same `GET /v1/jobs` the CLI's `lws job list` already
  uses.
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

None. Dashboard has no database of its own (`docs/architecture.md`'s
Data model section already lists it alongside MCP Server as holding "no
persistent storage of their own").

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
