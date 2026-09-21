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
than re-derived (`static/app.css` verbatim, its top bar and bottom tab
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
- **Pillars** (`/pillars`): the system's own components, separate from
  project management — Core's live health, crash counts, interface
  version. `/jobs` and `/policies` hang off it.
- **Jobs** (`/jobs`): the Orchestration queue, optionally filtered by
  status — the same `GET /v1/jobs` the CLI's `lws job list` already
  uses.
- **Policy management** (`/policies`, `/policies/{id}`) — the concrete,
  fully-specified panel `docs/milestones.md` M14 called out: every
  policy in its natural-language `ears_text` form with a checkbox for
  its `enabled` toggle, a per-policy gate-point selector (all ten
  categories, multi-select or none), a read-only view of the generated
  `test_script` (editing it is `lws policy activate`'s own job, Gate 4's
  human-review step — never bypassed here), and a per-project waiver
  grant/revoke view with its required `reason` shown inline. Backed
  entirely by `policy.configure`/`policy.waiver_grant`/`policy.
  waiver_revoke` Jobs and `GET /v1/policy_waivers` — no new pillar
  mechanism, this milestone is the UI surfacing what M12/M13 already
  built.
- **Unreachable-pillar handling**: since this app holds no domain data
  and applies no local fallback (unlike the CLI, which falls back to a
  direct database read when a daemon is down — a Dashboard user is
  already looking at a live page served by a live process, so "the
  pillar behind this panel is down" is simply the real answer to show,
  rendered as a visible banner, never a 500).

## Interfacing

- **Calls**: every other pillar's Pillar Interface, read-only for
  status/listing panels; Orchestration's `POST /v1/jobs` for every
  mutation (`policy.configure`, `policy.waiver_grant`, `policy.
  waiver_revoke` today — the same pattern any future panel's own
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
