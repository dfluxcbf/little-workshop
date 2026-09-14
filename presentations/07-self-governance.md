← [Visual Identity — Decided](06-visual-identity.md) · deck 7 of 8 · next: [Core's Recovery & Configuration →](08-core-recovery-and-configuration.md)

# Independence & Self-Governance

little-workshop stopped following the super-repo's root policies for
requirements and version management, and started writing its own. This
deck records why, and what actually changed.

## The circularity this was avoiding

little-workshop's whole purpose is to eventually absorb and improve on
`little-requirements`, `little-versions`, `little-runner`, and
`little-pypi`. Under the ecosystem's normal rule, every submodule tracks
requirements through `lreq` and manages branches through `lvx` — including,
by default, little-workshop itself.

That's circular: a project cannot be required to depend on the tools it is
actively designing the replacement for. Every requirement written into
`lreq`'s database, every branch bumped by `lvx`'s exact policy, would be
governed by the thing little-workshop is supposed to eventually make
unnecessary.

---

## What actually changed

- `bundle-littles/BUILD.bazel`'s `setup_little_workshop_env` genrule,
  and the empty `req_db/little-workshop_reqs.db` it created — removed.
- `.lvx/config.json` — removed.
- `docs/requirements_policy.md` and `docs/version_policy.md` — rewritten
  from "points back to root policy" into full, standalone policies
  little-workshop actually follows.
- A new `docs/governance_policy.md` — the charter this deck summarizes.
- The super-repo's own `docs/README.md` gained an explicit "Exemption:
  little-workshop" section — this isn't little-workshop unilaterally
  opting out; the root documentation itself now sanctions it, the same way
  it already sanctions the super-repo's own root checkout being outside
  `lvx`/`lreq`'s management.

Full detail: [`../docs/governance_policy.md`](../docs/governance_policy.md).

---

## Three principles, not just an exemption

1. **Independence** — no other `little-*` project's tooling or database is
   *required*. Using one's ideas is still fine; depending on it is not.
2. **Evolution** — little-workshop may change its own rules as it learns,
   but never has *no* rule in force, and never changes one silently.
   Amendments are dated, in the document itself.
3. **Self-application** — anything little-workshop eventually enforces
   ecosystem-wide must already be true of little-workshop first. The same
   reasoning already behind Core redeploying itself with the exact
   mechanism used for every other pillar — now a governance rule, not just
   an architecture one.

---

## What was kept, on purpose

Not a rejection of every idea from the tools being superseded — just the
*mandatory dependency* on them:

| Kept (it's a good idea) | Dropped (it's a tool dependency) |
|---|---|
| Two branches, `master`/`dev` | Being managed *through* `lvx` |
| `docs/{design,architecture,milestones}.md` | — |
| Sequential `REQ-#########`-shaped IDs, as a pattern | Being minted *through* `lreq` |
| Bazel, the standard folder layout | (never in question — not "support for another project") |

---

## What this doesn't mean

- little-workshop isn't leaving the super-repo. It's still a submodule,
  still at `~/little-projects/little-workshop`, still pushed to
  `github.com/dfluxcbf/little-workshop`.
- little-runner's cross-repo branch audit still watches this repository for
  basic git hygiene — that's little-runner observing, not little-workshop
  depending on anything. Read-only, by that audit's own design.
- This doesn't retroactively judge `little-requirements`/`little-versions`
  as wrong for every other submodule — they're still the right call for
  everything that isn't in the business of replacing them.

---

Back to [index](README.md) · or revisit [2 — The Pillar Architecture](02-pillar-architecture.md)
