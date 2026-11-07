# Requirements standard

Sources: `docs/requirements_policy.md`, `docs/requirements.md` ("Conventions"),
`docs/pillars/requirements_quality.md`, policies
`requirement-marker-registered`, `requirement-marker-id-format`.

## Source of truth

Requirements are kept only by a project of `full` governance (a Registry setting; `light`, the default,
asks for none and skips the requirement policies). There, every implementation request is mapped to
requirements by the time its branch finishes, once the behaviour settles (exploratory and visual iteration
needs none):
- Commit messages cite the affected `REQ-nnnnnnnnn` ids, or `REQ-NONE` when a commit affects none (never
  both). Nothing checks a single commit any more (`commit-cites-requirement` was retired 2026-11-07): the
  finish lists the ids the branch cites and its markers add in the squash commit's `Requirements:` line.
- `REQ-NONE` covers refactors, typo fixes, version bumps written by a hook, and little-workshop's own
  bookkeeping commits (version bumps, finish and release state, changelogs). Merge commits never use
  it.

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

Last reviewed: 2026-11-07 (hand edit: little-workshop's register split per pillar; discrepancy notes resolved in `docs/requirements_policy.md`); before that 2026-10-24.
