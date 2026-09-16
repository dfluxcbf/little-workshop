# Requirements & Quality

**Not yet built.** Scheduled as `docs/milestones.md`'s M9, depending on
M1 (Core + Orchestration), M2 (Registry & Portfolio), and M4 (Deployment
& Servers, for sandboxed test execution). This document describes the
design as currently specified — see `docs/architecture.md` and
`docs/milestones.md` for the authoritative source; nothing here has
shipped code behind it yet.

## Description

Requirements & Quality absorbs `little-requirements` wholesale: the
Project → Test Suite → Requirement → Test Result model, with a
draft/published lifecycle for requirements. Planned to also implement
`little-requirements`' own never-built M8 (a Use-Case/Milestone axis
independent of test suites) and E6 (JUnit-XML ingestion beyond pytest,
on-demand test execution) — work that was scoped but never landed in the
standalone tool.

## Purpose & goal

"Every behavior that matters has an auditable answer to 'is this
actually tested,' and every delivery checkpoint has an auditable answer
to 'is it done'" (`docs/architecture.md`).

## Responsibilities (as designed)

- **The `little-requirements` model** — Project, Test Suite, Requirement,
  Test Result, carried over unchanged: data moves from N per-project
  SQLite files into one pillar-owned database, `project_id`-scoped,
  reachable only through this pillar's own Pillar Interface — never a
  shared file another pillar or tool opens directly.
- **Sandboxed test execution** — calls Deployment & Servers' worktree
  interface (`POST /v1/worktrees`) rather than provisioning its own
  isolated checkout, the shared capability that pillar was built to
  offer starting in M4.
- **The multi-pillar definition-of-done concept**
  (`docs/pillars/policy_compliance.md`) — implemented on this pillar's
  own side in M9; the *conflict-with-a-policy* half of that mechanism is
  Policy & Compliance's (M11), once that pillar exists to be the other
  party in the conversation.

## Interfacing (as designed)

- **Calls**: Registry & Portfolio, to resolve a `project_id`'s repository
  path, the same way every project-scoped pillar already does. Deployment
  & Servers, for worktree provisioning during sandboxed test runs.
- **Called by**: `lws`, the MCP Server (once its tool catalog is
  extended to cover this pillar — M7's own text notes tool coverage
  grows automatically as later pillars ship), and Orchestration for
  every requirements-related Job type. Policy & Compliance (M11) is
  planned to read this pillar's definition-of-done state as one input to
  its own conflict-check flow.
- **Discovered through**: Core, like every pillar.
