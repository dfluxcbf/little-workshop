← [Action-Gating: Policy Confirmation](10-action-gating-confirmation.md) · deck 11 of 11 · back to [index](README.md)

# Authoring Gates & Boundaries

Answers left open across decks 9 and 10, plus the tier question the
~50-entry draft catalog (`docs/pillars/policy_catalog.md`) surfaced.
Resolved 2026-09-15.

## Tier: framework, across the board

No strong-default/user-tier split for the mined catalog. Every candidate
in it is a framework-policy candidate — immutable, authored through the
"little-workshop itself" tab, always all-projects.

---

## A third gate nobody had defined: is this even checkable?

The catalog's data-at-rest-encryption entry (`little-mindcheck`'s "rely on
host disk encryption, not database encryption") couldn't survive one
question: could a test script actually verify it? No — encryption state
isn't an artifact of the project's own code, and "we chose not to
implement X" is an absence, not a behavior. It's dropped entirely, not
narrowed, per one governing rule now built into the authoring pipeline:

> **If a policy cannot be enforced, it is not defined.**

This became Gate 2 in `docs/pillars/policy_compliance.md`'s authoring
flow — a proposed policy that fails EARS-refinement's Gate 1 gets another
attempt; a proposed policy that fails Gate 2 is rejected outright, told to
live in prose documentation instead, never saved in any form. Applying it
retroactively to the whole draft catalog also removed
"documentation shall not contain fabricated content" (judging fabrication
isn't mechanical) and narrowed four more entries to the checkable core of
what they originally claimed — full detail in the catalog's own
"Considered and rejected" section.

---

## Conflict became a gate, not a periodic audit

Previously: a best-effort *post-hoc* scan for matching key names across
policies, run on a schedule. Now: **Gate 3**, checked *before* a policy is
ever saved —

- **Structural** (mechanical, exact): does a policy already exist at this
  same scope level, scope target, and topic? If so, reject outright — no
  ambiguity is possible about which one would govern.
- **Semantic** (Claude CLI, best-effort): does the proposed text
  contradict any *existing* policy's text? Nothing can prove or disprove
  this with certainty for natural language — flagged possibilities are
  surfaced to the user to resolve, never silently merged, never silently
  allowed.

---

## Policy & Compliance is out of user reach

A user policy's scope target and gating topic can never resolve to Policy
& Compliance itself — checked at Gate 3, rejected outright if violated.
The referee can't be altered by the rules anyone using it can write.
Framework policies are exempt, and are expected to eventually check Policy
& Compliance's own conformance — the catalog's tier K already has
candidates for exactly that (`policy-one-to-one-test-script`, checking
this pillar against its own rule about itself).

---

## User-policy lifecycle

A user may create, edit, or remove a user-level policy at any time — no
restriction on timing. An edit isn't a lighter-weight path: it re-runs all
three gates, including the conflict check against every *other* policy
(a policy's prior version doesn't count against its own edit). Whether
little-workshop's own maintainers can edit or retire a *framework* policy
the same way is still open — "never overridable" was defined; "never
revised" wasn't.

---

Back to [index](README.md) · or revisit [9 — Policy & Compliance](09-policy-and-compliance.md)
