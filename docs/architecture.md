# Architecture

See `docs/design.md` for the concepts behind these components. This document
is the settled design for this round; nothing below is implemented yet (see
`docs/milestones.md`).

## Revision note (this round)

The first round described one process with internal modules sharing one
database. This round replaces that with **Core + ten pillars**, each its own
process with its own storage, communicating only through a declared Pillar
Interface (PI). Every section below (Components, Data model, Feature
mapping, Technical decisions) reflects the pillar model; where it differs
from what a first reading of the design-round-one commit would suggest, this
document — not that history — is authoritative.

## Components

**Core** — not a pillar, cannot be disabled. A small, rarely-changing
process-supervision layer: starts every enabled pillar as a `systemd --user`
unit, tracks liveness via each pillar's health probe, relaunches a crashed
pillar immediately from its last known-good build (no rebuild, no confirm
gate), refuses to start a disabled one, and keeps the pillar registry
(name → Unix-socket path → interface version) every other pillar and the
Dashboard/MCP use to find each other. Holds no domain data.

Ten pillars, each its own process, its own SQLite file, and its own Pillar
Interface (a small FastAPI app bound to a Unix domain socket):

| Pillar | Goal | Absorbs | Enabled by default |
|---|---|---|---|
| **Orchestration** | Every unit of work happens once, in the right order, and its outcome is recorded — regardless of which pillar performs it. | little-runner's per-repo queue/slot logic, generalized to every job type and every pillar | Yes — required |
| **Policy & Compliance** | Nothing drifts from the rules this ecosystem has already agreed to, without someone finding out. | The 8 root policy docs' checkable rules; `doctor.py`; `branch_audit.py`; the checklist skill's manual verification table | Yes — required |
| **Registry & Portfolio** | There is always one true answer to "what projects exist, and what's the state of the work tracked against them." | bootstrap-little-project wholesale; little-projects' informal registry role; `configure_submodules.sh`; little-tickets' undefined purpose; the ecosystem-milestone table | Yes |
| **Requirements & Quality** | Every behavior that matters has an auditable answer to "is this actually tested," and every delivery checkpoint has an auditable answer to "is it done." | little-requirements wholesale, plus its own never-built M8 (Use Cases & Milestones) | Yes |
| **Version Control & Release** | Branches and versions are always in a known-safe, consistent state — never by hand, never inconsistently. | little-versions wholesale | Yes |
| **Build & Packages** | A build artifact exists, is reproducible, and is reachable by anything that needs to install it. | little-runner's disclaimed CI non-goal (now real, including its own never-built E6: on-demand testing, JUnit-XML beyond pytest) + little-pypi wholesale | Optional — a project with no build/publish step never starts it |
| **Deployment & Servers** | A running process — a project's server, a Claude CLI instance, or a pillar of this system itself — stays running, and can be safely replaced with a newer version without anything else noticing. | little-runner's server-lifecycle mechanics (worktree deploys, confirm-gated rollback, cgroup-aware self-restart), generalized to every Managed Process | Yes |
| **Agentic Development** | Claude does real implementation work on a real branch, safely, and the human stays in control of when it's finished. | little-runner's `issue_flow.py`/`branching.py`/`report.py`/`screenshots.py`/`sessions.py`, generalized from GitHub-issue-only triggers | Optional — a project not using agentic dev never starts it |
| **Dashboard** | A human can see and act on everything above, without a terminal. | little-runner's `web/`, extended with panels for every other pillar | Optional — a headless/automation-only setup can disable it. Visual identity decided: see `presentations/06-visual-identity.md`. |
| **MCP Server** | An agent (Claude, or another tool) can see and act on everything above, without a terminal. | little-requirements' and little-versions' MCP tool surfaces, unified and namespaced | Optional — but the common case, since Claude is this ecosystem's primary caller |

Dashboard and MCP Server hold no domain data of their own — both are thin,
stateless proxies translating their caller's request (an HTTP request, an
MCP tool call) into a Pillar Interface call against whichever pillar
actually owns the answer, exactly the same discipline every other pillar
follows.

## Pillar Interface (PI)

- **Transport**: HTTP bound to a Unix domain socket, one per pillar
  (`~/.local/state/little-workshop/sockets/<pillar>.sock`) — never a network
  port, so pillar-to-pillar traffic never needs a firewall rule and is
  invisible outside the host by construction. FastAPI/uvicorn, matching this
  ecosystem's existing preference (`little-requirements`' server,
  `little-runner`'s dashboard).
- **Contract**: each pillar publishes its own OpenAPI schema (FastAPI
  provides this for free); every caller depends on that schema, never on the
  serving pillar's source. This is the same inter-project independence rule
  the [project structure policy](../../docs/policies/project_structure_policy.md)
  already enforces *between* `little-*` projects, now enforced *between
  pillars inside one of them* — structurally, not just by convention: there
  is no shared Python import path between two pillars' `src/` trees, and no
  pillar ever opens another pillar's SQLite file.
- **Versioning**: paths carry a version prefix (`/v1/...`); a pillar may
  serve two versions at once during a migration window, so upgrading one
  pillar's interface never forces every caller to upgrade in lockstep.
- **Discovery**: Core's pillar registry is the only place a socket path is
  looked up — a pillar never hardcodes another's path.

## Data model

**Revision note**: the first round specified one shared SQLite database for
the whole system. That is superseded — a shared database is exactly the
kind of direct, un-mediated coupling the pillar model exists to prevent (a
schema change in one pillar could silently break another that also reads
its tables). Each pillar now owns one exclusive SQLite file under
`~/.local/state/little-workshop/pillars/<pillar>.db`, WAL mode, and no other
process ever opens it — the same rule this ecosystem already applies to
`little-requirements`' database ("never edited or queried directly, always
through `lreq`"), now universal instead of specific to one tool.

- **Orchestration** — `jobs`, `job_runs`, `pipelines`, `pipeline_runs`.
- **Policy & Compliance** — `policy_rules`, `policy_check_results`.
- **Registry & Portfolio** — `projects` (incl. the super-repo container
  row), `tickets`.
- **Requirements & Quality** — `requirements`, `test_suites`,
  `test_results`, `use_cases`, `milestones`.
- **Version Control & Release** — no persistent DB beyond a small cache;
  git itself (branches, tags, the committed `VERSION` file) remains the
  actual source of truth, exactly as `little-versions` treats it today.
- **Build & Packages** — `packages` (metadata; blobs stay on disk),
  `build_runs`.
- **Deployment & Servers** — `servers`, `deployments`,
  `deploy_confirmations`.
- **Agentic Development** — `agent_runs`, `remote_control_sessions`.
- **Dashboard**, **MCP Server** — no persistent storage of their own.

What stays outside every pillar's database, deliberately: git repositories
and worktrees themselves (git remains the actual VCS), package blobs, run
logs (large text, kept as files under
`~/.local/state/little-workshop/logs/<pillar>/`, referenced by ID rather
than stored inline), and screenshots. The git-committed `VERSION` file in
each project remains the single source of truth for that project's version;
any pillar's cached copy of it is a cached read, never the authority.

An `events` append-only log — every state change, what triggered it — is
kept per-pillar (each pillar logs its own events) rather than as one
cross-cutting table, for the same reason every other table is: a pillar's
audit trail is that pillar's own data, readable by any caller through its
PI, not a table another pillar could accidentally join against.

## Feature mapping

| Absorbed tool | Becomes | What changes |
|---|---|---|
| `little-requirements` | Requirements & Quality pillar | Same model (Project→Suite→Requirement→TestResult, draft/published), plus its own planned M8 (Use Cases & Milestones) and E6 (on-demand testing, JUnit-XML beyond pytest) actually built. Data moves from N per-project SQLite files into one pillar-owned database, `project_id`-scoped, reachable only through this pillar's own interface — never a shared file. |
| `little-versions` | Version Control & Release pillar | Same Gitflow primitives and precondition checks, called over its Pillar Interface instead of via a subprocess to a sibling CLI. Fixes the documented gap where its MCP surface doesn't live in its own source tree — the pillar's PI *is* the one surface every caller (CLI, MCP, other pillars) goes through. |
| `little-runner` | Orchestration, Policy & Compliance, Registry & Portfolio (audit half), Requirements & Quality (testing half), Build & Packages (CI half), Deployment & Servers, Agentic Development, Dashboard pillars | Its stated non-goals ("not a general CI system," "not an interactive UI [beyond GitHub]") are lifted. Its trigger source generalizes from GitHub-issue-only to any Job source. Every safety property (branching control, one-flow-per-project, confirm-gated self-restart, cgroup-aware dispatch) is carried over unchanged, and the *mechanism itself* — worktree-based build-before-stop, confirm-gated rollback — becomes the general-purpose way every pillar (including Core and Deployment & Servers themselves) gets safely redeployed. |
| `little-pypi` | Build & Packages pillar | Same PEP 503 + `.vsix` model; the 5-minute-session compromise is dropped since this pillar is already a persistent process — that compromise only ever existed because `little-pypi` had no persistent process to live inside. |
| `bootstrap-little-project` | Registry & Portfolio pillar (`project.bootstrap` pipeline) | Its own two deliberate non-goals — registering the submodule, wiring the requirements database — stop being non-goals: both now touch one pillar's own table instead of shared files a single-project-scoped tool couldn't reach. |
| `little-projects` (informal role) | Registry & Portfolio + Policy & Compliance pillars | `configure_submodules.sh`, `stabilize_branches.py`/`branch_audit.py`, and the hand-maintained `docs/milestones.md` ecosystem table become scheduled/on-demand Jobs and registry rows with full history. The super-repo itself keeps existing as the physical git container — its *active* tooling role migrates in, the container repository does not go away. |
| `little-tickets` | Registry & Portfolio pillar | Its own docs currently say "purpose not yet defined" — this is that purpose: the source of record for portfolio/ecosystem tracking, including for projects with no GitHub remote (`little-mapgen`), which can't participate in the issue-driven workflow today. |

## Technical decisions

- **Process-per-pillar, not modules in one process.** This is the central
  revision of this round: a shared process cannot give independent crash
  isolation, independent live updates, or a genuinely lightweight
  "disabled costs nothing" story — see `design.md`'s Revision note above.
  Ten small processes plus Core cost more in raw process count than one
  process would, and that cost is accepted deliberately because the
  properties it buys (see design.md's Goals) were explicitly required, not
  assumed.
- **Unix-domain-socket HTTP, not shared memory or raw sockets.** HTTP is
  debuggable with `curl`, versionable by URL path, and consistent with this
  ecosystem's existing FastAPI preference; a Unix socket (not a TCP port)
  keeps every pillar-to-pillar call invisible outside the host and avoids
  port-allocation bookkeeping across ten-plus processes.
- **One SQLite file per pillar, not one shared database.** Superseded from
  round one specifically because a shared file is direct coupling by
  another name — any pillar could theoretically open it and reach into
  another pillar's tables. Per-pillar files make "the only way to this
  pillar's data is its own interface" a structural fact, not a rule someone
  has to remember to follow — the same upgrade this ecosystem's requirements
  policy already made for one tool ("never query `lreq`'s database
  directly"), now load-bearing for every pillar.
- **Two distinct recovery mechanisms, not one.** A crash needs the fastest
  possible relaunch of what already worked (Core's job: no rebuild, no
  confirm gate). A planned upgrade needs to never disturb a sibling pillar
  and to be reversible if it's wrong (Deployment & Servers' job:
  build-before-stop, confirm-gated). Conflating these into one mechanism
  would either make crash recovery too slow (rebuilding on every crash) or
  make planned upgrades too risky (no confirm gate) — keeping them separate
  lets each be no more careful than it needs to be.
- **`systemd --user` per pillar, not a custom process supervisor.** Free
  `Restart=on-failure` at the OS level is the first line of defense before
  Core's own crash-counter logic ever runs, and it's the exact primitive
  every project server in this ecosystem already uses — nothing new to
  operate.
- **No multi-tenant auth/RBAC.** Tailscale network identity plus the
  existing actor-allowlist model (owner + configured `allowed_actors`) is
  the entire access-control story, attached only to the Dashboard and any
  pillar that talks to GitHub — unchanged from what `little-runner` already
  does.
- **Worktree-based deployment, not container-first.** Every existing
  project already deploys as a bare process via `systemd --user`; adopting
  a Docker-first model would be a regression for the sole benefit of
  matching an unneeded PaaS convention. Docker stays available as an
  opt-in sandbox backend for test isolation inside the Requirements &
  Quality pillar, not the default deploy model.
- **A small number of safety rules are hard-coded, not `PolicyRule`s.**
  "Automation never merges/deletes/finishes on its own initiative" and "the
  agent never touches branches when a Job specifies them" are invariants in
  the Orchestration and Agentic Development pillars' own code, not entries
  in Policy & Compliance's editable rule table — these are safety properties
  this ecosystem's production use already depends on, not conventions a
  future policy edit should be able to loosen by accident.
- **A new feature maps to exactly one pillar's interface, or becomes a new
  pillar.** If a proposed feature clearly serves an existing pillar's one
  stated goal, it's a new endpoint on that pillar's interface. If it doesn't
  fit any existing pillar's goal statement, it becomes a new pillar — with
  its own goal, its own storage, its own manifest entry — rather than being
  bolted onto an existing pillar for convenience, which is exactly the
  entanglement path this whole model exists to avoid.

## Dependencies

Not finalized — implementation work. Expected to mirror the absorbed tools'
existing choices where they already fit the constraints here: Python ≥3.11
stdlib-first per pillar (matching `little-runner`'s and
`bootstrap-little-project`'s existing "no third-party runtime dependency"
preference beyond what a pillar genuinely needs), FastAPI/uvicorn for every
Pillar Interface and the Dashboard, `git`/`gh`/`claude`/`systemctl`/
`systemd-run`/`bazel`/`tailscale` as external, already-required host tools
(unchanged from today).

## Build

Not applicable yet — no `src/` beyond the standard scaffold this round (see
`docs/design.md`'s non-goals). Once implementation begins, this project
follows the root [build policy](../../docs/policies/build_policy.md) like
every other submodule. Expect one Bazel package per pillar under `src/`
(each pillar is independently buildable/installable, matching its
independently-deployable nature) plus `//:all`/`//:test` aggregating across
all of them; exact target shape is implementation work, not decided here.

## Additional folders

`presentations/` — a set of cross-linked, presentation-style documents
covering the goals and decisions behind this design, for review and
onboarding; see `presentations/README.md`. Includes `presentations/assets/`:
`identity-tokens.css` (the decided Dashboard visual identity's design
tokens, ready for implementation to consume directly) and
`identity-reference.html` (a static, dependency-free preview of that
identity applied to two real screens). This is additional to the standard
layout per the
[project structure policy](../../docs/policies/project_structure_policy.md)'s
"projects may have additional top-level folders where their nature requires
it" allowance.
