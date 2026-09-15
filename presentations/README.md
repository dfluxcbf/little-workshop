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
6. [Visual Identity — Decided](06-visual-identity.md) — the Dashboard
   pillar's chosen look: Ledger's layout, Mission Control's exact color
   tokens, violet excluded. Design tokens ready for implementation live in
   [`assets/identity-tokens.css`](assets/identity-tokens.css); a static,
   dependency-free preview is at
   [`assets/identity-reference.html`](assets/identity-reference.html).
7. [Independence & Self-Governance](07-self-governance.md) — why
   little-workshop stopped depending on `little-requirements`/`lreq` and
   `little-versions`/`lvx` for its own governance, and wrote
   [`../docs/governance_policy.md`](../docs/governance_policy.md) instead.
8. [Core's Recovery & Configuration Ownership](08-core-recovery-and-configuration.md)
   — how Core recovers from its own crash without losing any other pillar,
   and the filename-encoded ownership convention for shared project
   configuration.
9. [Policy & Compliance: Rules for the Rules](09-policy-and-compliance.md)
   — the first pillar specified end-to-end: three tiers of rules,
   scope/precedence, EARS, the two-Claude-CLI-call authoring pipeline, and
   enforcement. Full spec:
   [`../docs/pillars/policy_compliance.md`](../docs/pillars/policy_compliance.md).
10. [Action-Gating: Policy Confirmation](10-action-gating-confirmation.md)
    — when a policy check actually runs relative to the action it governs:
    fail closed, human override within ten minutes, or the action never
    happens at all.

Start at [1 — Vision & Goals](01-vision-and-goals.md).
