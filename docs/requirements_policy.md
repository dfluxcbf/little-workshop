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

- **Every implementation request is mapped to Requirements by the time its branch finishes**
  (2026-10-24; it read "before it is implemented"). Requirements are written once the behaviour
  settles, not ahead of exploratory or visual iteration, which needs none. A request that adds behaviour adds requirements; one that changes behaviour edits the
  affected requirements (a draft is edited in place; a published requirement changes by a new
  requirement that supersedes it in the same change, and never blocks a new request; reworded
  2026-10-24, it read "is locked"); one that removes behaviour removes them. The commits
  that implement the request cite the affected ids (`REQ-nnnnnnnnn`) in their messages.
  Judging that the mapping is *right* is a review matter; the mechanical part — commits under
  `src/` cite an id — is the `commit-cites-requirement` policy, filed as a draft for approval in
  the app. Since 2026-10-24 it gates the branch finish only, never a single commit: a citation is
  form, not protection, and a check of form never refuses work in progress.
- **A requirement records a request; it never caps the next one** (2026-10-24). When the user
  asks for something that contradicts a requirement, a test or a design note, the request wins:
  the same change supersedes or removes the old row and updates its tests. The look of an
  interface (colours, sizes, materials, styles) is written as design notes, not requirements, so
  a new reference picture does not have to argue with a locked row. Added after a long task kept
  earlier simplifications over the user's reference images.
- **Publish late** (2026-10-24). A round's requirements stay drafts, edited in place, while the
  work iterates; they are published when the branch finishes or the release is cut, the point
  where the definition-of-done report counts them. Publishing mid-work only makes the next change
  a supersede.
- **Superseding is one call** (2026-10-24): `workshop_requirements_supersede` creates the new
  row, marks the old one superseded in the pillar and in `docs/requirements.md`, and lists the
  tests still tagged with the old id; the policy `requirement-marker-not-superseded` refuses a
  commit that leaves one of them behind.
- **`REQ-NONE`: a commit that affects no requirement says so** (2026-09-24). A commit
  cites `REQ-NONE` instead of an id when it affects no requirement at all — a refactor, a
  typo fix, a version bump a version hook wrote into `src/`. It declares "no requirement
  affected"; it is not a label for any particular kind of change, and it is never cited
  alongside a real `REQ-nnnnnnnnn` id. little-workshop's own bookkeeping commits (version
  bumps, finish/release state, release candidates, changelogs) cite it; merge commits,
  which carry a branch's real changes, never do. `commit-cites-requirement` accepts either
  and refuses both together. `REQ-NONE` is recognised in any letter case (`Req-NONE`,
  `req-none`; amended 2026-09-24); requirement ids stay uppercase, and little-workshop's own
  commits write `REQ-NONE`.
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
