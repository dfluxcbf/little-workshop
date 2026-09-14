← [index](README.md) · deck 1 of 8 · next: [The Pillar Architecture →](02-pillar-architecture.md)

# Vision & Goals

## The problem

- Five separate tools already exist and work: `little-requirements`,
  `little-versions`, `little-runner`, `little-pypi`, `bootstrap-little-project`.
- Plus informal tooling around them: `configure_submodules.sh`,
  `stabilize_branches.py`/`branch_audit.py`, a hand-maintained
  `docs/milestones.md` ecosystem table.
- Each is good at its one job. Nothing ties them into one lifecycle.

---

## Why not just adopt something that already exists?

- A full FOSS survey (requirements, test management, CI, package
  registries, project management, agentic dev, server management, task
  queues, health checks) found **no single free, self-hosted product**
  covering this combination.
- The closest — Tuleap, GitLab CE — cover the ALM half only, and still
  leave agentic development and this ecosystem's specific safety
  guarantees (branching control, confirm-gated self-redeploy) unaddressed.
- Assembling the combination from five or six third-party services would be
  a **net increase** in operational surface, not a reduction.

---

## The core principle

> Absorb, don't replace with peers.

`little-workshop` is not a sixth tool next to the other five. It is one
system that inherits each of their proven mechanisms — not reinvents them —
and gives them one lifecycle, one job model, and one policy layer.

---

## Goals

- One job model for every kind of work (requirement changes, version
  bumps, test runs, builds, deploys, agentic-dev sessions).
- Pipelines as first-class, inspectable objects — replacing a hand-run,
  hand-sequenced markdown table.
- Documentation policy as *executable* rules, not prose someone has to
  remember to re-check.
- Claude CLI as a first-class execution target, generalized beyond
  GitHub-issue-only triggers — every existing safety property kept.
- A real CI and test-sandboxing story — every current tool explicitly
  disclaims this today.
- Self-restart with a newer version of itself, inheriting
  `little-runner`'s already-proven mechanism.
- Stay small: sized for one PC and up to two people, not for hypothetical
  growth.

Full detail: [`../docs/design.md` → Goals](../docs/design.md#goals).

---

## Non-goals

- Not multi-tenant, not hosted, no role/permission model beyond "is this
  one of us."
- Not a general ALM product competing with Tuleap/GitLab CE.
- Not a distributed system — one host, always.
- Not a replacement for Git, Bazel, `gh`, or Claude Code itself.
- Not an implementation yet — this round is design only.

Full detail: [`../docs/design.md` → Non-goals](../docs/design.md#non-goals).

---

## The name

**little-workshop** — decided and confirmed. Already the live private
GitHub repo (`dfluxcbf/little-workshop`), `master` as default branch,
matching every sibling project.

A workshop holds every bench at once: the requirements bench, the test
bench, the release bench, the deployment bay. It doesn't overclaim
("workshop," not "platform" or "engine") and reads plainly next to every
other `little-*` name.

---

Next: [2 — The Pillar Architecture →](02-pillar-architecture.md)
