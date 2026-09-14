← [Vision & Goals](01-vision-and-goals.md) · deck 2 of 8 · next: [Jobs, Pipelines, Policy & Recovery →](03-execution-model.md)

# The Pillar Architecture

This is the redefinition that supersedes the first round's "one process,
internal modules" design. Getting this right mattered enough to redo it
before any implementation started.

## Eight requirements, one at a time

A second pass at the design added requirements a single process cannot
satisfy:

1. Facilitate design and implementation.
2. Ensure quality of different aspects — specific goals, features built
   around them.
3. Proper separation of code to avoid entanglement (unexpected bugs,
   change-sensitivity).
4. Easy definition of new features — support an existing pillar, or found
   a new one.
5. Enable/disable parts by user need — a genuinely lightweight system.
6. Parts interface each other like **independent software components**.
7. Recover from a crash by relaunching only the crashed part.
8. Update and relaunch a part without interrupting anything else.

---

## Why "modules in one process" fails 4 of these

- **#5 (disable = save resources)** — a module's code is still loaded and
  initialized in the shared interpreter even when "disabled"; nothing is
  actually saved.
- **#6 (independent components)** — modules calling each other's Python
  functions directly *is* entanglement, dressed up as tidy file layout.
- **#7 (crash isolation)** — one process, one crash domain. Anything
  unhandled anywhere takes everything down.
- **#8 (live update)** — there is no safe way to hot-swap code inside a
  running interpreter for arbitrary modules.

Something structurally different was needed, not a better-organized version
of the same thing.

---

## The redefinition

**Core** — not a feature, cannot be disabled. Starts every enabled pillar,
watches whether each is alive, relaunches a crashed one immediately, holds
no domain data. The smaller and more stable Core stays, the more reliably
everything depending on it stays up.

**Pillar** — an independently deployable *process*, one stated goal, its
own exclusive storage, exactly one way in: its own declared interface.
Ten of them. Each is a `systemd --user` unit — the exact primitive
`little-runner` already uses for every project server, turned inward on
`little-workshop` itself.

Full definitions: [`../docs/design.md` → Core concepts](../docs/design.md#core-concepts).

---

## The ten pillars

| Pillar | One-line goal |
|---|---|
| Orchestration | Every unit of work happens once, in order, with its outcome recorded. |
| Policy & Compliance | Nothing drifts from the rules without someone finding out. |
| Registry & Portfolio | One true answer to "what projects exist, what's their state." |
| Requirements & Quality | An auditable answer to "is this tested" and "is this done." |
| Version Control & Release | Branches/versions always in a known-safe, consistent state. |
| Build & Packages | A build artifact exists, is reproducible, is installable. |
| Deployment & Servers | A running process stays running, and can be safely replaced. |
| Agentic Development | Claude does real work on a real branch, safely. |
| Dashboard | A human can see and act on all of the above. |
| MCP Server | An agent can see and act on all of the above. |

Full table with what each absorbs: [`../docs/architecture.md` → Components](../docs/architecture.md#components).

---

## How a pillar is reached: the Pillar Interface (PI)

- HTTP over a **Unix domain socket**, one per pillar — never a network
  port. Debuggable with `curl`, invisible outside the host.
- Each pillar publishes its own OpenAPI schema. Callers depend on the
  schema, never on the pillar's source.
- Versioned paths (`/v1`, `/v2`) — a pillar can run two versions at once
  during a migration, so upgrading it never forces every caller to move in
  lockstep.
- Core's registry is the *only* place a socket path is looked up.

---

## How data ownership actually enforces separation (#3, #6)

- One SQLite file **per pillar**. No pillar ever opens another's file.
- This supersedes round one's single shared database — a shared file is
  direct coupling by another name, exactly the entanglement risk this whole
  model exists to prevent.
- The only way to another pillar's data is that pillar's own interface —
  the same rule this ecosystem already applies to `little-requirements`'
  database ("never query it directly, always through `lreq`"), now
  universal instead of specific to one tool.

---

## Enable/disable, made real (#5)

- A pillar's manifest entry has `enabled: true|false`. Disabled means Core
  never starts its process — no memory, no CPU, no attack surface.
- A caller depending on a disabled pillar gets an immediate, clear
  "pillar disabled" response — never a hang.
- Concretely: a single-project, no-GitHub, no-agentic-dev setup runs five
  or six small processes. Build & Packages, Deployment & Servers, Agentic
  Development, Dashboard, and MCP Server simply never launch.

---

## New features, without entanglement (#4)

A proposed feature either:

- serves an existing pillar's *one stated goal* → becomes a new endpoint
  on that pillar's interface, or
- doesn't fit any existing goal statement → becomes a **new pillar**, with
  its own goal, storage, and manifest entry.

Never bolted onto an existing pillar "because it was convenient" — that is
exactly the path back to entanglement.

---

Next: [3 — Jobs, Pipelines, Policy & Recovery →](03-execution-model.md)
