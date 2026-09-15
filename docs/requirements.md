# Requirements

little-workshop's own interim requirements register — see
`docs/requirements_policy.md` for why this exists instead of `lreq`: this
project is exempt from the root requirements policy, and tracks its own
requirements in this file until its Requirements & Quality pillar exists
to do it for real, at which point little-workshop becomes its own first
user of that pillar (the self-application principle,
`docs/governance_policy.md`).

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

## M1 — Core + Orchestration

No requirements drafted yet — creating them is the first real task of
implementing this milestone, not pre-work done here. See
`docs/milestones.md`'s M1 entry for the objective these requirements need
to cover.

| ID | Requirement | Status |
|---|---|---|
