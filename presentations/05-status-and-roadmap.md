← [What Each Old Tool Becomes](04-feature-mapping.md) · deck 5 of 8 · next: [Visual Identity — Decided →](06-visual-identity.md)

# Status & What's Next

## What's actually built today

- `little-workshop` scaffolded, registered as a submodule of the
  super-repo, and wired into `bundle-littles`' requirements DB.
- Verified against all 5 root policies for a design-only round.
- Pushed to GitHub: **`dfluxcbf/little-workshop`** (private), `master` as
  default branch, matching every sibling repo.
- Full, real content in `docs/design.md` and `docs/architecture.md` — no
  placeholder text anywhere.
- This presentations set, cross-linking the full narrative.

---

## What's deliberately not done yet

- **No `src/` implementation.** Every pillar described in deck 2 is a
  design, not a line of code.
- **No pillar manifest format finalized.** "A pillar's manifest entry has
  `enabled: true|false`" describes a concept, not a schema.
- **No SQLite schema DDL.** The per-pillar table lists in
  `../docs/architecture.md` are illustrative, not final.
- **No `presentations/` folder existed anywhere in this ecosystem before
  this round** — nothing here contradicts an existing convention; it's a
  new one, called out in `../docs/architecture.md`'s "Additional folders."

---

## Two design rounds, and why the second one mattered

- **Round 1**: absorb five tools into one process, one shared database,
  internal modules as code boundaries.
- **Round 2 (this one)**: a harder set of requirements — crash isolation,
  independent live updates, real enable/disable, component-like
  boundaries — showed round 1's model couldn't deliver them. Rather than
  patch around that, the process/data model was redone: **Core + ten
  pillars**, each its own process, its own database, its own interface.
- Nothing about *why* this project exists changed between rounds — only
  *how* the same absorbed capabilities are separated and run. See deck 1's
  goals list: unchanged.

---

## Constraint trade-offs, unchanged across both rounds

- Sized for **one PC, up to two developers** — not hypothetical growth.
- No message broker, no container orchestration, no multi-tenant auth.
- Worktree-based deploys, not Docker-first — every project already runs
  as a bare process.
- Ten pillars is a deliberate cost, accepted because the properties it
  buys (crash isolation, independent updates, real enable/disable) were
  explicitly required, not assumed.

---

## Next round: implementation planning

Explicitly out of scope for this round. When it starts, it will need,
in order:

1. The pillar manifest schema (name, socket path, health-probe endpoint,
   enabled flag, interface version) — Core's actual contract.
2. One pillar's Pillar Interface fully specified end-to-end, as the
   template every other pillar's interface follows.
3. Core's crash-relaunch logic, built and tested before any pillar depends
   on it being right.
4. Milestones, defined in `../docs/milestones.md` — currently, correctly,
   empty.

**Updated**: item 2 originally proposed Version Control & Release as the
narrowest starting point. Policy & Compliance was fully specified first
instead — see
[9 — Policy & Compliance: Rules for the Rules](09-policy-and-compliance.md)
and `../docs/pillars/policy_compliance.md` — because it needed its own
rules for what a rule *is* before anything else could lean on it. Version
Control & Release remains the next narrow, low-revision-risk candidate for
the same treatment.

---

Back to [index](README.md) · or revisit [1 — Vision & Goals](01-vision-and-goals.md)
