# Requirements standard

Sources: `docs/requirements_policy.md`, `docs/requirements.md` ("Conventions"),
`docs/pillars/requirements_quality.md`, policies `commit-cites-requirement`,
`requirement-marker-registered`, `requirement-marker-id-format`.

## Source of truth

Every implementation request is mapped to requirements **before** it is implemented:

- new behaviour adds requirements;
- changed behaviour edits the affected requirements (a published requirement is locked, so a change
  is a new requirement that supersedes it);
- removed behaviour removes them.

Requirements are drafted as the first real task of a milestone, not speculatively ahead of need.

## Where they live

Preferred: the Requirements & Quality pillar (`lws requirements ...`, MCP
`workshop_requirements_*`): Project, then Test Suite, then Requirement, then Test Result, with a
draft/published lifecycle, plus use cases and milestones. little-workshop's own `docs/requirements.md`
is its interim register and is migrated into the pillar.

Never edit the requirements database directly.

## Identifiers

`REQ-` plus nine digits (`REQ-000000001`): sequential, assigned in creation order, never reused, never
chosen ahead of time. `REQ-NONE` is not an id.

## Wording: EARS

`<The system> shall <response>`, optionally prefixed by `While <state>,`, `When <trigger>,`,
`Where <feature>,` or `If <condition>, then`. The pillar refuses a non-EARS description with the
correction. Bulk imports (`imported: true`) are exempt; older requirements stay as they are until
edited.

## One requirement, one row

Short enough for a table cell; longer explanation links to the authoritative `docs/` section.

## Status

Two lifecycles, one rule: nothing is `done` on implementation alone.

- In the Requirements pillar a requirement is `draft` (mutable) or `published` (locked). The
  definition-of-done report (`GET /v1/report`) calls it `done` only when it is `published`, linked to
  at least one test suite, and the latest run of every linked suite passed; `tested`, `passing` and
  `done` are reported separately.
- In little-workshop's interim `docs/requirements.md` the status is `draft` to `done` (implemented,
  tested, test confirmed passing), nothing in between.

A milestone is done only when every one of its requirements is done.

## Commits cite requirements

- A commit that changes a file under `src/` cites at least one `REQ-nnnnnnnnn`, or `REQ-NONE` when it
  affects no requirement (any letter case accepted; write `REQ-NONE`).
- **Never both** a real id and `REQ-NONE` in one commit.
- `REQ-NONE` covers refactors, typo fixes, version bumps written by a hook, and little-workshop's own
  bookkeeping commits (version bumps, finish and release state, changelogs). Merge commits never use
  it.
- Enforced by policy `commit-cites-requirement` at `branch_finish`.

## Tests and requirements

Tests are tagged `@pytest.mark.requirement("REQ-nnnnnnnnn")`; the project's pytest configuration
registers the `requirement` marker (policy `requirement-marker-registered`) and every marker uses the
`REQ-` plus nine digits form (policy `requirement-marker-id-format`; format only, existence is not
checked). A bug-fix test carries the id of the behaviour it belongs to, or is left untagged with a
note in the report.

Non-pytest tests are imported as JUnit-XML (`workshop_requirements_import_junit`).

## Definition-of-done report

The report takes the **worst** verdict of the linked suite's latest run. See
[testing_standard.md](testing_standard.md) for how a run must be recorded.

## Discrepancy notes

- `docs/requirements_policy.md` still says `commit-cites-requirement` is "filed as a draft for
  approval"; the live policy is approved, enabled and gating `branch_finish`
  ([policy_register.md](policy_register.md)).
- `docs/requirements_policy.md`'s "Project-specific notes" say no test suites exist and that the
  Requirements pillar does not exist yet; the pillar is built (M10) and little-workshop's own
  requirements are imported into it. `docs/requirements.md` also cites
  `docs/policies/documentation_policy.md`, which does not exist.

Last reviewed: 2026-10-01.
