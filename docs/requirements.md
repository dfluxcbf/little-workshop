# Requirements

little-workshop's requirements register, mirroring the Requirements & Quality pillar (where they are
tracked for real; `docs/requirements_policy.md` says how). It is split by pillar so a session reads only
the part it needs. A requirement goes in the file of the pillar its statement names (the first one it
names); one that names none goes in the part its feature belongs to, or `general.md`.

- [Core](requirements/core.md)
- [Orchestration](requirements/orchestration.md)
- [Policy & Compliance](requirements/policy.md)
- [Registry & Portfolio](requirements/registry.md)
- [Requirements & Quality](requirements/requirements.md)
- [Version Control & Release](requirements/version.md)
- [Build & Packages](requirements/build.md)
- [Deployment & Servers](requirements/deployment.md)
- [Agentic Development](requirements/agent.md)
- [Dashboard](requirements/dashboard.md)
- [MCP Server](requirements/mcp_server.md)
- [Usage & Efficiency](requirements/usage.md)
- [lws CLI](requirements/cli.md)
- [Cross-cutting](requirements/general.md)

## Conventions

- **ID format**: `REQ-` followed by 9 digits (`REQ-000000001`), matching
  this ecosystem's existing shape exactly — sequential, never reused,
  never chosen ahead of time, assigned in the order a requirement is
  actually created in this file.
- **One requirement, one row.** Text stays short enough to fit a table
  cell; anything that needs more explanation links to the
  `docs/`/`docs/pillars/` section that's actually authoritative for it,
  per this project's own "don't duplicate content across files" habit.
- **Status**: `draft` (mutable, the default) → `done` (implemented,
  tested, and the test confirmed passing — never marked done on
  implementation alone). Nothing in between; a requirement that's
  half-true isn't done.
- **Milestone**: which `docs/milestones.md` entry a requirement belongs
  to. A milestone's definition-of-done (per
  `docs/policies/documentation_policy.md` inherited via this project's
  own kept conventions) requires every one of its requirements at `done`.
- New requirements are created as part of planning a milestone's actual
  work, not invented speculatively ahead of need.
