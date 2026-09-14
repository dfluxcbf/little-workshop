# Governance policy

This document is little-workshop's own constitution — not a pointer to the
super-repo's root policies, unlike every other `docs/*_policy.md` file in
this ecosystem. It exists because little-workshop's job description makes
the normal arrangement circular: it is the project meant to eventually
absorb, and then improve on, `little-requirements`, `little-versions`,
`little-runner`, and `little-pypi` — the exact tools the root
[requirements policy](../../docs/policies/requirements_policy.md) and
[version policy](../../docs/policies/version_policy.md) mandate every other
submodule depend on. A project cannot be required to depend on the tools it
is designed to replace while it's designing their replacement. See
`docs/README.md`'s "Exemption: little-workshop" for the root-level
acknowledgment of this — this file does not unilaterally opt out; the
super-repo's own documentation sanctions it, the same way it already
sanctions the super-repo itself being outside `lvx`/`lreq`'s management.

## The independence principle

little-workshop does not require any other `little-*` project's tooling,
database, or process policy for its own governance. Concretely, today:

- No dependency on `little-requirements`/`lreq` or
  `bundle-littles/req_db/`. There is no `little-workshop_reqs.db`.
- No dependency on `little-versions`/`lvx`. There is no `.lvx/config.json`.
- No dependency on `little-runner`/`little-pypi` either, though neither
  was ever wired in — named here for completeness, since the same
  reasoning applies to all four.

"Independent" means *not required to depend on*, not *forbidden from using*.
See "What's kept, and why" below — this project uses whatever it finds
genuinely valuable from the tools it will eventually replace. It just
doesn't take that value as a mandatory dependency, and it never mistakes
"we used to do it this way" for a reason to keep doing it that way.

## The evolution principle

little-workshop is explicitly allowed to improve on its own definitions,
constraints, and policies over time — that's not a loophole, it's the
point of the project. But "allowed to change its rules" is not the same as
"allowed to have no rules": at every point in time, this document and its
siblings (`requirements_policy.md`, `version_policy.md`) state the actual
rule in force, not an aspiration. A change to any of them is:

1. **Deliberate** — made because something was learned, not by drift.
2. **Documented in the file itself**, at the point of change — never only
   in a commit message or a conversation.
3. **Dated**, so a later reader can tell how long a rule has actually been
   in force.

This document's own "Amendments" section at the bottom is the record for
changes to the governance policy itself; `requirements_policy.md` and
`version_policy.md` keep their own project-specific notes sections for
changes scoped to them.

## The self-application principle

Whatever little-workshop eventually enforces ecosystem-wide — through the
Policy & Compliance pillar, or any other — must already be true of
little-workshop itself first. This project does not get to design a rule
for everyone else that it hasn't already tried on its own repository. This
is not a new idea invented for this document: it's the same reasoning
already behind Core redeploying itself with the identical mechanism it uses
for every other pillar (see `docs/design.md`), just stated here as a
governance rule instead of an architecture one.

## What's kept, and why

Nothing here is rejected reflexively. Specific patterns from the tools
being superseded are kept because they're good ideas, independent of the
tool that first implemented them:

- **A two-branch model (`master`/`dev`)** — kept, managed with plain `git`
  directly rather than through `lvx`. See `version_policy.md`.
- **The `docs/{design,architecture,milestones}.md` habit** — kept in full;
  nothing about writing things down was ever the problem.
- **Sequential, unambiguous requirement identifiers** — kept as a concept;
  see `requirements_policy.md` for how they're tracked without `lreq`.
- **Bazel as the build tool, and the standard `docs/`/`src/`/`tests/`/
  `tools/` folder layout** — kept without qualification. Neither is
  "support for another `little-*` project" — both are the super-repo's
  generic engineering conventions, and this document's independence
  principle is about governance-tool dependency specifically, not about
  rejecting every convention on principle.

## What's not kept

- **Being required to run `lreq` against a shared database this project
  doesn't control the schema of.** If little-workshop needs structured
  requirement tracking before its own Requirements & Quality pillar exists,
  it will track requirements in a way it designs and owns — even if that's
  temporarily less capable than `lreq` — rather than adopt a dependency it's
  actively working to make unnecessary.
- **Being required to run `lvx` for branch/version management.** Plain
  `git`, used directly and carefully, is enough for two long-lived branches
  and a `VERSION` file — the same judgment the super-repo's own root
  checkout already makes about itself.

## Amendments

- **2026-09-14** — Governance policy established. little-workshop
  unwound its `bundle-littles` requirements-DB wiring and its
  `.lvx/config.json`, and declared independence from
  `little-requirements`/`little-versions` as mandatory dependencies, per
  this document and the corresponding root-level exemption in
  `docs/README.md`.
