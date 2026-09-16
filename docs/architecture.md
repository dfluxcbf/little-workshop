# Architecture

See `docs/design.md` for the concepts behind these components. This document
is the settled design for this round; nothing below is implemented yet (see
`docs/milestones.md`).

## Governance note

little-workshop is exempt from the super-repo's root policies — it is not
wired into `little-runner`/`lreq`'s or `little-versions`/`lvx`'s tooling for
its own governance, and defines its own requirements/version policy
instead. See `docs/governance_policy.md` for the full charter and
`docs/README.md`'s "Exemption: little-workshop" for the root-level
acknowledgment. Nothing about this section's technical content below
changes as a result — it's a statement about which policies this project
answers to, not about the architecture itself.

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
unit, tracks liveness via each pillar's own health report (5-second probe
timeout, up to 10 retries), relaunches a crashed pillar immediately from
its last known-good build (no rebuild, no confirm gate) for up to 10
consecutive tries a minute apart before marking it **faulted** and
surfacing that to the user, refuses to start a disabled one, and keeps
the pillar registry (name → Unix-socket path → interface version) every
other pillar and the Dashboard/MCP use to find each other. Holds no
domain data. Full detail: `docs/design.md`'s "Core's own recovery."

Core's own crash is recovered the same way as any pillar's — `systemd
--user Restart=on-failure` on its own unit, no special-cased mechanism —
and because every pillar is systemd's child, not Core's, a Core crash never
cascades to them. On restart, Core does not relaunch what's already
running: it reconstructs its live registry from `systemctl --user
is-active` plus each pillar's own health probe, reconnecting to whatever it
finds and starting only whatever's actually missing. See `design.md`'s
"Core's own recovery" data-flow diagram.

Ten pillars, each its own process, its own SQLite file, and its own Pillar
Interface (a small FastAPI app bound to a Unix domain socket):

| Pillar | Goal | Absorbs | Enabled by default |
|---|---|---|---|
| **Orchestration** | Every unit of work happens once, in the right order, and its outcome is recorded — regardless of which pillar performs it. | little-runner's per-repo queue/slot logic, generalized to every job type and every pillar | Yes — required. Also owns scheduled tasks (`docs/milestones.md` M9): a `ScheduledTask` produces an ordinary Job on a timer (`created_by: "scheduler"`, the placeholder role already named in this document's own dispatch diagram) — `add`/`edit`/`remove`/`trigger`/`cancel`/`stop`/`postpone`/`queue`, over a `once` or weekday-`recurring` schedule. No pillar needs its own scheduling mechanism for a periodic version of what it already does (periodic tests, periodic builds, periodic policy audits, periodic releases) — it's just a `ScheduledTask` naming that pillar's existing Job type. A firing missed while the (locally hosted) daemon was down is a pending decision surfaced to the user, not silently skipped or replayed in full — see `docs/pillars/orchestration.md`'s "Missed firings." |
| **Policy & Compliance** | Nothing drifts from the rules this ecosystem has already agreed to, without someone finding out. | The 8 root policy docs' checkable rules; `doctor.py`; `branch_audit.py`; the checklist skill's manual verification table | Yes — required. Fully specified: `docs/pillars/policy_compliance.md`. A candidate policy set, mined from every project in this ecosystem: `docs/pillars/policy_catalog.md` — framework tier decided, individual entries still pending the authoring pipeline. |
| **Registry & Portfolio** | There is always one true answer to "what projects exist, and what's the state of the work tracked against them." | bootstrap-little-project wholesale; little-projects' informal registry role; `configure_submodules.sh`; little-tickets' undefined purpose; the ecosystem-milestone table | Yes. The main responsible pillar for a project's lifecycle and identity (creation, import, removal) — not owner of everything about a project, which stays deliberately distributed across pillars, but owner of the registration itself. Two creation paths: `project.bootstrap` (deterministic, for a new project) and import (an existing project, driven by a Claude CLI session — human-interactive or Agentic Development's own internal dispatch, the same mechanism either way — through this pillar's MCP-exposed entry point, since bringing an arbitrary existing project into conformance needs judgment a fixed pipeline can't provide). Also owns `project_sets`, moved here from Policy & Compliance — grouping projects is a registry concern even though Policy & Compliance remains its only consumer today. **Removal, resolved 2026-09-15**: hard delete, immediate, cascading — removing a project's registry row triggers every other pillar to purge that project's data through its own Pillar Interface (each pillar deletes its own rows for that `project_id`; Registry & Portfolio doesn't reach into another pillar's database directly). No soft-delete, no undo. |
| **Requirements & Quality** | Every behavior that matters has an auditable answer to "is this actually tested," and every delivery checkpoint has an auditable answer to "is it done." | little-requirements wholesale, plus its own never-built M8 (Use Cases & Milestones) | Yes |
| **Version Control & Release** | Branches and versions are always in a known-safe, consistent state — never by hand, never inconsistently. | little-versions wholesale | Yes |
| **Build & Packages** | A build artifact exists, is reproducible, and is reachable by anything that needs to install it. | little-runner's disclaimed CI non-goal (now real: general build/test Job execution, not just the fork-testing guard it kept), fork-safety-gated PR testing, and little-pypi wholesale | Optional — a project with no build/publish step never starts it |
| **Deployment & Servers** | A running process — a project's server, a Claude CLI instance, or a pillar of this system itself — stays running, and can be safely replaced with a newer version without anything else noticing. | little-runner's server-lifecycle mechanics (worktree deploys, confirm-gated rollback, cgroup-aware self-restart), generalized to every Managed Process | Yes. Also the sole owner of isolated-worktree provisioning — Requirements & Quality's test execution and Policy & Compliance's policy checks both call its interface for one rather than each provisioning their own. |
| **Agentic Development** | Claude does real implementation work on a real branch, safely, and the human stays in control of when it's finished. | little-runner's `issue_flow.py`/`branching.py`/`report.py`/`screenshots.py`/`sessions.py`, generalized from GitHub-issue-only triggers | Optional — a project not using agentic dev never starts it. Fully specified: `docs/pillars/agentic_development.md`. |
| **Dashboard** | A human can see and act on everything above, without a terminal. | little-runner's `web/`, extended with panels for every other pillar | Optional — a headless/automation-only setup can disable it. Visual identity decided: see `presentations/06-visual-identity.md`. |
| **MCP Server** | An agent (Claude, or another tool) can see and act on everything above, without a terminal. | little-requirements' and little-versions' MCP tool surfaces, unified and namespaced | Optional — but the common case, since Claude is this ecosystem's primary caller |

Dashboard and MCP Server hold no domain data of their own — both are thin,
stateless proxies translating their caller's request (an HTTP request, an
MCP tool call) into a Pillar Interface call against whichever pillar
actually owns the answer, exactly the same discipline every other pillar
follows.

**CLI (`lws`)** — not a pillar, not Core, and missing its own formal
treatment until now despite being referenced in passing throughout this
document (the Feature mapping table's `little-versions` row, for one:
"the pillar's PI *is* the one surface every caller (CLI, MCP, other
pillars) goes through"). A third thin, stateless proxy alongside
Dashboard and MCP Server — one function per subcommand, no logic of its
own, talking to the daemon over a local socket for anything needing
shared state, falling back to direct state-file/database reads for pure
reads when the daemon isn't running (the same fallback `lrun status`
already uses today). Absorbs `lreq`/`lvx`/`lrun`/`little-pypi`/
`bootstrap-little-project`'s command surfaces as `lws` subcommands
(`lws req`, `lws version`, `lws job`, ...), one pillar's worth of
subcommands added as that pillar is actually built rather than designed
as one command tree up front. Needed earlier than Dashboard or MCP
Server: it's the only interface that can exist before any pillar with a
web or agent-facing surface does, and is what actually exercises Core
and Orchestration during their own construction.

**Compatibility shims**: once `lws` exists, the five absorbed tools'
original CLIs (`lreq`, `lvx`, `lrun`, `little-pypi`, `bootstrap-little-project`)
are expected to become thin forwarders to the equivalent `lws` subcommand
— muscle memory and any existing script that still calls them keeps
working during the transition, rather than every caller needing to
switch atomically the moment a pillar ships. This is deliberately
separate from building `lws` itself; see `docs/milestones.md`.

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
  How long an old version has to stay alive before it can be dropped is,
  resolved 2026-09-15, deliberately **not a policy** — "how long is
  reasonable" isn't something a test script can verify, the same
  reasoning that already dropped other unenforceable candidates from
  `docs/pillars/policy_catalog.md`. It stays a judgment call for whoever
  owns the pillar, each time.
- **Discovery**: Core's pillar registry is the only place a socket path is
  looked up — a pillar never hardcodes another's path.

## Configuration & project data

Two different things, kept in two different places, neither of which is
how pillars talk to each other — configuration may be used to *establish*
a connection (a socket path, a credential), never to carry the conversation
itself; that's what the Pillar Interface is for.

### Pillar configuration — exclusive, one directory per pillar

`~/.config/little-workshop/<pillar>/` — every pillar's own settings,
touched by no other pillar, ever. Ownership needs no naming convention here
because the whole directory is already exclusively scoped by path. This is
distinct from a pillar's own *operational* database
(`~/.local/state/little-workshop/pillars/<pillar>.db` — jobs, rule
definitions, requirement records: data the pillar produces and queries) —
configuration is what a pillar is told; the database is what a pillar
knows. Core is not a pillar, so it has no `<pillar>/` directory of its own;
its bootstrap manifest lives directly at
`~/.config/little-workshop/manifest.toml`, a sibling of the ten pillar
directories rather than inside one.

### Project files — shared, ownership encoded in the filename

`~/.config/little-workshop/projects/<project>/` — one directory per project
this system manages, shared across every pillar that has something to say
about that project. Because the directory itself isn't exclusive to one
pillar, ownership is encoded in the filename: `<name>.<owner-tag>.<format>`.
The second-to-last extension is the *owner tag* — the one pillar allowed to
write that file; the last extension is only its serialization format
(`.json`, `.yaml`, `.toml`, ...), free per file. A pillar may own any
number of distinctly-named files per project.

| Owner tag | Pillar |
|---|---|
| `.orchestration` | Orchestration |
| `.policy` | Policy & Compliance |
| `.registry` | Registry & Portfolio |
| `.requirements` | Requirements & Quality |
| `.version` | Version Control & Release |
| `.build` | Build & Packages |
| `.deployment` | Deployment & Servers |
| `.agent` | Agentic Development |
| `.dashboard` | Dashboard |
| `.mcp` | MCP Server |

Examples: `retention.policy.json` (Policy & Compliance's, for one project),
`manifest.deployment.toml` (Deployment & Servers', for one project's server
manifest — the direct descendant of little-runner's existing per-project
`~/.config/little-runner/projects/<owner>-<name>.toml`, now split by owner
instead of being one shared file). Core owns no project files — it isn't a
pillar and has no project-scoped concerns of its own.

`manifest.deployment.toml`'s shape is inherited close to verbatim from
`little-runner`'s own proven `[server]` table (root
`docs/features/server_management.md`) — the one application-specific
piece every other mechanic (build, launch, readiness, Tailscale mapping)
is generic against:

```toml
[server]
name    = "little-meals"
command = "lmeals serve"
port    = 8765
match   = ".local/bin/lmeals serve"
install = ["bazel", "run", "//:install"]

[server.tailscale]
https_port = 443
```

**Read/write rule**: any pillar may *read* any project file regardless of
owner tag. Only the owning pillar may *write* its own files. A pillar
needing a value from a file it doesn't own reads it directly — read access
is universal — and needing that value *changed* is a request to the
owner's Pillar Interface, never a direct write: see `design.md`'s
"Cross-pillar configuration change" data-flow diagram.

**Enforcement**: not filesystem permissions — every pillar runs as the
same host user, so there is no real UID boundary to enforce this with
(the same reasoning already behind this project having no multi-tenant
auth model at all). Enforcement is a shared client library: every pillar
touches `~/.config/little-workshop/projects/` only through it, and it
refuses a write whose target file's owner tag isn't the caller's own — a
structural rule in code, not a convention someone has to remember.

**No repetition of configuration**: two distinct guarantees, not one.
*One writer per file* is what the tag convention above mechanically
enforces — a hard guarantee. *No two files encoding the same underlying
setting* is not mechanically provable in general (nothing can verify two
differently-named values mean the same thing from the outside) — this is a
design discipline (a setting is defined once, by its one owning pillar; a
pillar needing that value reads the owner's file rather than keeping its
own copy) backed by a best-effort Policy & Compliance check: matching key
names appearing across two owners' files get flagged for a human to look
at, not silently allowed or silently merged.

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

**Boundary with project files**: a pillar's SQLite file is for its own
*operational* data — records it produces and queries at volume (test
results, job history, policy check history). Small, per-project *settings*
a pillar exposes for other pillars to read belong in "Configuration &
project data" above, as project files, not as rows in this database. The
`projects` table below is the exception worth naming explicitly: it's
Registry & Portfolio's own index of what exists (name, repo path, GitHub
remote, cached status) — and, resolved this round, the *authoritative*
one: Registry & Portfolio is this system's main responsible pillar for a
project's lifecycle and identity (see its Components entry below), so its
row is what "this project exists" actually means. A
`~/.config/little-workshop/projects/<project>/` directory existing is a
*consequence* of registration, not a parallel source of truth — Registry &
Portfolio creates it as part of registering a project, the same way it
creates the registry row, never the other way around.

- **Orchestration** — `jobs`, `job_runs`, `pipelines`, `pipeline_runs`.
- **Policy & Compliance** — `policies`, `policy_check_results`
  (three-outcome: pass/fail/not-applicable), `policy_waivers` (an
  explicit, auditable per-project exception, added 2026-09-16). Fully
  specified, including the scope/precedence model and the EARS-based
  authoring pipeline:
  `docs/pillars/policy_compliance.md` — the schema there supersedes the
  placeholder shape this list originally gave this pillar. Reads
  `project_sets` via Registry & Portfolio's Pillar Interface when
  resolving a project-set-scoped policy's targets — it doesn't own that
  table (moved this round; see Registry & Portfolio's entry below).
- **Registry & Portfolio** — `projects` (incl. the super-repo container
  row), `project_sets`, `tickets`.
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

**Log retention, resolved 2026-09-15**: size-based, not age-based —
appropriate for a system hosted on one personal machine, where "how much
disk" is the actual constraint, not "how old." When a pillar's log file
grows past its size threshold, the oldest lines are trimmed first (a
rolling window, not periodic full-file rotation). The exact threshold is
implementation-time detail, not decided here.

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
| `bootstrap-little-project` | Registry & Portfolio pillar (`project.bootstrap` pipeline) | Its own two deliberate non-goals — registering the submodule, wiring the requirements database — stop being non-goals: both now touch one pillar's own table instead of shared files a single-project-scoped tool couldn't reach. Also extended with a capability it never had: importing an *existing* project (not just scaffolding a new one), via a Claude-CLI-driven flow through this pillar's MCP entry point rather than a deterministic pipeline. |
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
  operate. This applies to Core's own unit too: Core gets no bespoke,
  more-fragile recovery mechanism for itself.
- **Core reconstructs its registry on restart rather than trusting memory
  it just lost.** The alternative — persisting the live registry somewhere
  so Core could resume "where it left off" — adds a second source of truth
  that can drift from systemd's own actual unit states. Asking systemd and
  each pillar's health probe directly, every time Core starts, is slower
  by a few round trips and never wrong.
- **Filename-encoded ownership for project files, not OS permissions.**
  Every pillar runs as the same host user, so there is no UID boundary to
  enforce write-ownership with — the same fact already behind this
  project's "no multi-tenant auth" decision. A shared client library that
  refuses a mismatched write is weaker than kernel-enforced permissions in
  the adversarial case, and exactly as strong in the case that actually
  exists here: one user, ten pillars, no adversary.
- **No multi-tenant auth/RBAC.** Tailscale network identity plus the
  existing actor-allowlist model (owner + configured `allowed_actors`) is
  the entire access-control story, attached only to the Dashboard and any
  pillar that talks to GitHub — unchanged from what `little-runner` already
  does.
- **Worktree-based deployment, not container-first.** Every existing
  project already deploys as a bare process via `systemd --user`; adopting
  a Docker-first model would be a regression for the sole benefit of
  matching an unneeded PaaS convention. Docker stays an opt-in sandbox
  backend for test isolation inside the Requirements & Quality pillar —
  not the default deploy model — but is the *required* execution sandbox
  for Policy & Compliance's generated test scripts specifically, whose
  network/filesystem boundaries need real namespace isolation to mean
  anything; see `docs/pillars/policy_compliance.md`'s "Test-script
  execution sandbox."
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
(unchanged from today). Confirmed 2026-09-15 as deferred to
implementation — no pillar was flagged as needing anything unusual.

## Build

Not applicable yet — no `src/` beyond the standard scaffold this round (see
`docs/design.md`'s non-goals). Once implementation begins, this project
follows the root [build policy](../../docs/policies/build_policy.md) like
every other submodule. One Bazel package per pillar under `src/` (each
pillar is independently buildable/installable, matching its
independently-deployable nature), plus `//:all`/`//:test` aggregating
across all of them.

**Per-pillar targets, resolved 2026-09-15**: `//:build`, `//:install`
(runs `//:build` first), `//:test`, and `//:server` — the last one new to
this ecosystem's standard vocabulary, specific to pillars: every other
`little-*` project builds a wheel/CLI/extension, but a pillar's own
artifact type is a long-running service, so it gets a target that runs
it directly (local dev/testing) the same way `//:wheel`/`//:vsix` exist
for the artifact types that need them. This fits the root build policy's
own extensibility rule — "only implement the targets that make sense for
that project's outputs, under these exact names" already anticipates a
new artifact type earning a new standard name. Anything beyond these four
defers to implementation.

## Additional folders

`presentations/` — a set of cross-linked, presentation-style documents
covering the goals and decisions behind this design, for review and
onboarding; see `presentations/README.md`. Includes `presentations/assets/`:
`identity-tokens.css` (the decided Dashboard visual identity's design
tokens, ready for implementation to consume directly) and
`identity-reference.html` (a static, dependency-free preview of that
identity applied to two real screens).

`docs/pillars/` — deep, per-pillar specifications, one file each, once a
pillar has been worked out in enough detail that `design.md`/
`architecture.md`'s survey-level treatment stops being enough. Holds
`policy_compliance.md`, `policy_catalog.md` (its candidate policy set),
and `agentic_development.md`; more will be added as other pillars get the
same treatment, not all at once.

This is additional to the standard layout per the
[project structure policy](../../docs/policies/project_structure_policy.md)'s
"projects may have additional top-level folders where their nature requires
it" allowance.
