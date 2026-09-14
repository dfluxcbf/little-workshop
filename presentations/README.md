# little-workshop — presentations

Five short decks, meant to be read in order, walking through every goal and
decision made about this project so far. Each slide is a `##` heading;
`---` marks a slide break. These are the *narrative* — the why and the
decision — not the spec: every deck links out to `../docs/design.md` and
`../docs/architecture.md` for the authoritative technical detail rather than
restating it.

1. [Vision & Goals](01-vision-and-goals.md) — why this project exists, what
   it will and won't do, the naming decision.
2. [The Pillar Architecture](02-pillar-architecture.md) — the redefinition
   that supersedes "one process, internal modules": Core, pillars, and the
   Pillar Interface.
3. [Jobs, Pipelines, Policy & Recovery](03-execution-model.md) — how work
   actually moves between pillars, and the two distinct recovery
   mechanisms.
4. [What Each Old Tool Becomes](04-feature-mapping.md) — the feature
   inventory and the mapping from `little-requirements`/`little-versions`/
   `little-runner`/`bootstrap-little-project`/`little-projects` onto pillars.
5. [Status & What's Next](05-status-and-roadmap.md) — what's actually built
   today, what's deliberately not, and what the next round is.

Start at [1 — Vision & Goals](01-vision-and-goals.md).
