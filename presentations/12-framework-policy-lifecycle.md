← [Authoring Gates & Boundaries](11-authoring-gates-and-boundaries.md) · deck 12 of 12 · back to [index](README.md)

# Framework-Policy Lifecycle

The one question deck 11 left open. Resolved 2026-09-15.

## Two different claims, only one of them true

"Never overridden by a user policy" and "never revised" sound like the
same guarantee. They aren't:

- **Never overridden** — true, absolute, no exception. Nothing in the
  precedence chain, no confirmation dialog, no timeout ever lets a user
  policy win against a framework one.
- **Never revised** — false. little-workshop is still under active
  development, and a framework policy can turn out to be wrong,
  incomplete, or superseded by better project design the same way any
  other part of the system can. little-workshop's own maintainers can
  edit or remove one.

---

## What makes that not a loophole

A framework-policy change goes through the same three gates as authoring
one fresh — EARS-compliant, mechanically enforceable, non-conflicting —
*and* is treated as continuous development of little-workshop itself, not
a runtime toggle. That's not a new idea invented for this deck: it's
`docs/governance_policy.md`'s evolution principle, already written down —

> A change to any of them is: deliberate — made because something was
> learned, not by drift. Documented in the file itself, at the point of
> change. Dated, so a later reader can tell how long a rule has actually
> been in force.

— now stated as applying explicitly to `docs/pillars/policy_compliance.md`'s
framework policies too, not only to `governance_policy.md` itself. A
framework-policy edit belongs in the same place any other design decision
does — a milestone, a documented reason, a date — not indistinguishable in
the audit trail from a user tweaking their own project's policy on a
whim.

---

## Why this distinction matters in practice

Everything else in this ecosystem is *required* to obey a framework
policy unconditionally. That's exactly why changing one can't be casual —
the same authority that makes the rule binding is what makes a careless
change to it dangerous. "Hard while in force, revisable through real
development work" is the balance: little-workshop keeps the freedom to
improve on its own rules (the whole point of
[7 — Independence & Self-Governance](07-self-governance.md)), without that
freedom ever meaning a framework policy was quietly different yesterday
than it is today.

---

Back to [index](README.md) · or revisit [7 — Independence & Self-Governance](07-self-governance.md)
