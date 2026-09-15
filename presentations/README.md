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
11. [Authoring Gates & Boundaries](11-authoring-gates-and-boundaries.md) —
    the ~50-entry draft catalog's open questions, resolved: framework tier
    across the board, a mechanical-enforceability gate ("if it can't be
    enforced, it isn't defined"), conflict elevated from a periodic audit
    to a pre-save gate, Policy & Compliance placed out of user policies'
    reach, and user-policy lifecycle defined.
12. [Framework-Policy Lifecycle](12-framework-policy-lifecycle.md) — hard
    while in force, but revisable: little-workshop's own maintainers can
    edit or remove a framework policy, treated as continuous development
    of little-workshop itself rather than a runtime toggle.
13. [The Test-Script Sandbox](13-test-script-sandbox.md) — closes the
    generated-artifact sandboxing gap: network limited to other pillars'
    safe interface calls, filesystem read-only and allowlisted, writes
    limited to the verdict and this pillar's own files, a 30-minute async
    execution ceiling — reusing Requirements & Quality's existing
    optional Docker sandbox as the enforcement mechanism.
14. [Verdicts vs. Faults](14-verdicts-vs-faults.md) — a script timeout is
    a framework fault, never routed through the ten-minute
    deliberate-bypass window that exists only for a real, determined
    policy failure; and full human approval of both the EARS text and the
    generated script, every time, is what makes a separate allowlist
    review step unnecessary.
15. [Who Owns a Project?](15-project-ownership.md) — Registry & Portfolio
    is the main responsible pillar for a project's identity and lifecycle
    (not everything about it); two creation paths, bootstrap and
    Claude-CLI-driven import through its MCP entry point, reach the same
    registration; project sets move there from Policy & Compliance.
16. [Tracking Open Questions](16-tracking-open-questions.md) — introduces
    [`docs/open_questions.md`](../docs/open_questions.md), the single
    tracker every scattered "Left open" section now points to instead of
    duplicating; catches a gap that had quietly gone unresolved for seven
    decks; splits into an enforceable "the file exists" policy and an
    unenforceable "Claude CLI documented what it surfaced" convention.

Start at [1 — Vision & Goals](01-vision-and-goals.md).
