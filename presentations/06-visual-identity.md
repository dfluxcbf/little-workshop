← [Status & What's Next](05-status-and-roadmap.md) · deck 6 of 8 · next: [Independence & Self-Governance →](07-self-governance.md)

# Visual Identity — Decided

Added after the fact: this deck records an actual UX decision, not just a
plan. Three complete identity profiles were reviewed side by side, each
rendered against the same two real screens (Home/Portfolio, Pillars) with
the same underlying data — not mood boards. The full review, including the
two identities not chosen, is preserved in the decision record below.

## The three reviewed

1. **Mission Control** — dark, cyan→violet, monospace-forward. The
   continuity choice: extends little-runner's existing production dashboard
   language, which several sibling projects already share.
2. **Workbench** — deep walnut, pegboard, tool-tags for pillars, enamel-teal
   + brass accents. The most distinctive: literalizes the "workshop" name.
3. **Ledger** — schematic layout: title blocks, sheet numbers, grid-ruled
   tables. The most continuous with this project's own design documents and
   presentations.

All three were built dark-mode-only, by explicit direction.

---

## The decision

**Ledger's layout, Mission Control's color tokens, violet excluded.**

Two separate judgments, kept separate on purpose:

- **Layout** — Ledger's structure won: engineering title blocks, sheet
  numbers, grid-ruled bordered tables. This is what makes little-workshop's
  Dashboard read as a system of record — fitting, since the pillars it's
  presenting (Requirements & Quality, Policy & Compliance) are themselves
  about proof and audit trails.
- **Color** — Mission Control's palette won outright, and was copied
  **exactly**, not re-derived or approximated: the same `oklch()` neutral
  scale, the same cyan, the same ok/warn/fail. The only change is omitting
  Mission Control's violet — Ledger has no second accent to spend it on,
  and violet stays exclusive to Mission Control's own brand mark (should
  Mission Control's language ever resurface elsewhere in this ecosystem,
  that exclusivity is what would still tell the two apart).

This is the identity for little-workshop's Dashboard pillar going forward.
Workbench and Mission Control remain documented above as the alternatives
that were seriously considered and specifically why they weren't chosen —
not deleted, since that reasoning has its own value later.

---

## Design tokens (authoritative)

Colors are `oklch()`, matching this ecosystem's existing precedent (see
little-runner's own `docs/ui_design.md`: "Colors are oklch()... dark mode
only") — no hex fallback is carried, since every target browser supports
`oklch()` directly.

| Token | Value | Used for |
|---|---|---|
| `--bg` (void) | `oklch(16% 0.02 260)` | Page background |
| `--surface` | `oklch(21% 0.02 260)` | Cards, panels, frames |
| `--surface-2` | `oklch(25% 0.02 260)` | Table headers, nested chips |
| `--border` | `oklch(32% 0.02 260)` | Card/table borders, rules |
| `--ink` | `oklch(94% 0.01 260)` | Primary text, headings |
| `--ink-soft` | `oklch(74% 0.02 260)` | Labels, secondary text |
| `--ink-faint` | `oklch(54% 0.02 260)` | Muted/disabled text |
| `--accent` (cyan) | `oklch(78% 0.15 200)` | Links, the one primary action, project names |
| `--ok` | `oklch(78% 0.19 152)` | Passing/running/healthy |
| `--warn` | `oklch(80% 0.16 80)` | Degraded, needs attention |
| `--fail` | `oklch(68% 0.21 25)` | Failing, disabled-as-error |

**Not carried over:** Mission Control's violet (`oklch(66% 0.22 320)`) —
deliberately absent from this identity.

**Status color at low alpha**, for pill backgrounds: `oklch(<ok|warn> / .15)`
— e.g. `oklch(78% 0.19 152 / .15)` for a passing status pill's background,
full-strength `--ok` for its text. Never convey state by color alone: every
pill also carries its label (`OK`/`WARN`/`FAIL`/`DISABLED`).

### Typography

| Role | Face | Notes |
|---|---|---|
| Display / brand | Big Shoulders, 700 | Uppercase, tight tracking — the one place this identity raises its voice |
| Body / UI | IBM Plex Sans | Tables, labels, running text |
| Code / data / mono | IBM Plex Mono | Sheet numbers, status pills, anything tabular-numeric, title-block metadata |

### Layout conventions (kept from Ledger, unchanged by the recolor)

- **Title block** — every screen's top bar carries the product wordmark
  left, a small right-aligned monospace block right (sheet name, revision/
  scale), separated by a `1.5px` rule in `--ink` — an engineering-drawing
  convention, not a generic app header.
- **Grid-ruled tables, not row-divider tables.** Every cell gets a full
  `1px solid var(--border)` border, header row on `--surface-2` — spec-sheet
  density, distinct from a lighter borderless list.
- **Sheet numbers** for anything enumerable (the pillar list is "Sheet
  00–10," matching Core + the ten pillars exactly) — literal, not
  decorative, since the count is real and fixed.
- **Pillar health strip** — one small bordered pill per pillar in the top
  bar, dot + label, `--ok`/`--warn`/`--fail`/muted-`--ink-faint` for
  disabled — the fastest-scanning summary of system state, always visible.

---

## Where this came from

Full side-by-side review (all three identities, both mockup screens each,
the recoloring steps in order): the design-review session that produced
this decision. This deck is the durable record inside the project itself —
kept here rather than only in that review, per the same principle
`docs/design.md` and `docs/architecture.md` already follow: prose policy
lives here, not only in conversation.

---

Back to [index](README.md) · or revisit [2 — The Pillar Architecture](02-pillar-architecture.md) for what this Dashboard is actually presenting
