# Requirements policy

**little-workshop is exempt from the super-repo's
[requirements policy](../../docs/policies/requirements_policy.md)** — see
`docs/governance_policy.md` for why, and `docs/README.md`'s "Exemption:
little-workshop" for where the super-repo itself sanctions this. This file
states little-workshop's own requirements policy in full; it does not point
back to the root document, because it is not bound by it.

## What this means concretely

- There is **no** `little-workshop_reqs.db` and no dependency on `lreq`
  (`little-requirements`). Requirements are not tracked through the shared
  `bundle-littles/req_db/` mechanism every other submodule uses.
- The requirements-DB wiring this project previously had (a
  `setup_little_workshop_env` genrule in `bundle-littles/BUILD.bazel`, and an
  empty database file) has been removed as part of declaring this
  independence.

## What's tracked, and how, until the Requirements & Quality pillar exists

Right now, "requirements" for little-workshop live in **`docs/requirements.md`**
— resolved 2026-09-15, correcting this document's own earlier wording,
which said they'd be "recorded directly in `docs/milestones.md`." That
undersold what implementation-scale requirements actually need: dozens of
them across ten pillars, each wanting its own row, with `milestones.md`'s
"Requirements" column holding references to them rather than their text.
This is deliberately less structured than `lreq`'s
Project→Suite→Requirement→TestResult model — that's an accepted, temporary
cost, not an oversight. Adopting `lreq` now to avoid that cost would recreate
the exact dependency `docs/governance_policy.md` explains this project can't
carry while it's designing that dependency's eventual replacement.

The `REQ-#########` identifier concept is kept as a *pattern*, not a
mandate: sequential, unambiguous, assigned in creation order, recorded in
`docs/requirements.md` — never invented loosely, never reused, but also
never requiring `lreq` to mint it. See that file's own "Conventions"
section for the full shape.

## Requirements are the source of truth for every implementation request (2026-09-21)

- **Every implementation request is mapped to Requirements before it is implemented.**
  A request that adds behaviour adds requirements; one that changes behaviour edits the
  affected requirements (a published requirement is locked, so a change to one is a new
  requirement that supersedes it); one that removes behaviour removes them. The commits
  that implement the request cite the affected ids (`REQ-nnnnnnnnn`) in their messages.
  Judging that the mapping is *right* is a review matter; the mechanical part — commits under
  `src/` cite an id — is the `commit-cites-requirement` policy, filed as a draft for approval in
  the app.
- **Requirements follow EARS.** A description reads `<The system> shall <response>`, optionally
  prefixed by `While <state>,`, `When <trigger>,`, `Where <feature>,` or `If <condition>, then`.
  The Requirements pillar enforces this: creating a requirement or editing a draft's description
  is refused, with the correction, when it is not EARS (`src/requirements/ears.py`). Bulk
  imports of requirements written elsewhere (`imported: true`) are exempt, and requirements that
  predate the rule stay as they are until someone edits them.

## Project-specific notes

- No test suites exist yet (design-only round, no `src/`/`tests/` content).
- This section will be rewritten, not just appended to, the day the
  Requirements & Quality pillar can track little-workshop's own
  requirements — at which point little-workshop becomes its own first user,
  per the self-application principle in `docs/governance_policy.md`.
