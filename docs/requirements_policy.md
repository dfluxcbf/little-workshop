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

## Where they live

The Requirements & Quality pillar holds little-workshop's requirements (it is its own first user, per the
self-application principle in `docs/governance_policy.md`). `docs/requirements.md` and the per-pillar files
under `docs/requirements/` are the register that mirrors it: one row per requirement, created with the same
`REQ-#########` id as the pillar row (sequential, assigned in creation order, never reused).

## Requirements are the source of truth for every implementation request (2026-09-21)

- **Every implementation request is mapped to Requirements by the time its branch finishes**
  (2026-10-24; it read "before it is implemented"). Requirements are written once the behaviour
  settles, not ahead of exploratory or visual iteration, which needs none. A request that adds behaviour adds requirements; one that changes behaviour edits the
  affected requirements (a draft is edited in place; a published requirement changes by a new
  requirement that supersedes it in the same change, and never blocks a new request; reworded
  2026-10-24, it read "is locked"); one that removes behaviour removes them. The commits
  that implement the request cite the affected ids (`REQ-nnnnnnnnn`) in their messages.
  Judging that the mapping is *right* is a review matter. No policy checks the citations (2026-11-07:
  `commit-cites-requirement` was retired, since the squash at the finish discarded what it checked);
  the finish lists the ids the branch names in the squash commit's `Requirements:` line instead.
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
  row, marks the old one superseded in the pillar and in the register, and lists the
  tests still tagged with the old id; the policy `requirement-marker-not-superseded` refuses a
  commit that leaves one of them behind.
- **`REQ-NONE`: a commit that affects no requirement says so** (2026-09-24). A commit
  cites `REQ-NONE` instead of an id when it affects no requirement at all — a refactor, a
  typo fix, a version bump a version hook wrote into `src/`. It declares "no requirement
  affected"; it is not a label for any particular kind of change, and it is never cited
  alongside a real `REQ-nnnnnnnnn` id. little-workshop's own bookkeeping commits (version
  bumps, finish/release state, release candidates, changelogs) cite it; merge commits,
  which carry a branch's real changes, never do. `REQ-NONE` is recognised in any letter case (`Req-NONE`,
  `req-none`; amended 2026-09-24); requirement ids stay uppercase, and little-workshop's own
  commits write `REQ-NONE`.
- **Requirements follow EARS.** A description reads `<The system> shall <response>`, optionally
  prefixed by `While <state>,`, `When <trigger>,`, `Where <feature>,` or `If <condition>, then`.
  The Requirements pillar enforces this: creating a requirement or editing a draft's description
  is refused, with the correction, when it is not EARS (`src/requirements/ears.py`). Bulk
  imports of requirements written elsewhere (`imported: true`) are exempt, and requirements that
  predate the rule stay as they are until someone edits them.

## Project-specific notes

- The unit suite (`tests/unit`) is the "Unit tests" suite every requirement links to; tests carry
  `# --- REQ-nnnnnnnnn: <title> ---` sections, which `workshop_requirements_sync_markers` reads.
- Removed behaviour (2026-11-07): a draft requirement is deleted from the pillar and its row removed;
  a published one is superseded by one that states the removal.
