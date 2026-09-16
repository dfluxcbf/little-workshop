# Requirements & Quality

## Description

Requirements & Quality absorbs `little-requirements` wholesale: the
Project → Test Suite → Requirement → Test Result model, with a
draft/published lifecycle for requirements, carried into one
pillar-owned, `project_id`-scoped SQLite database
(`requirements/db.py`'s `test_suites`, `requirements`,
`requirement_test_mappings`, `test_results`, `use_cases`, `milestones`
tables) rather than N per-project SQLite files. "Project" itself is not
this pillar's own concept — every table is keyed by the `project_id`
Registry & Portfolio already owns.

Also implements `little-requirements`' own never-built M8 (a Use
Case/Milestone axis independent of test suites entirely — `use_cases`
and `milestones` are their own tables, each optionally linked from a
`Requirement`, with no requirement to link a suite at all) and E6
(JUnit-XML ingestion beyond pytest, and on-demand sandboxed test
execution) — work that was scoped in the standalone tool but never
landed.

## Purpose & goal

"Every behavior that matters has an auditable answer to 'is this
actually tested,' and every delivery checkpoint has an auditable answer
to 'is it done'" (`docs/architecture.md`).

## Responsibilities

- **The `little-requirements` model** — `TestSuite`, `Requirement`
  (`draft`/`published`), `TestResult` (`passed`/`failed`/`skipped`/
  `error`), plus the `use_cases`/`milestones` axis, all reachable only
  through this pillar's own Pillar Interface.
- **Draft/published lifecycle** — `update_requirement` is refused once
  a requirement's `status` is `published`; `publish_requirement` is the
  only way to lock it. There is no unpublish — the same "corrected
  forward, not rewritten" discipline this ecosystem's own requirements
  register already follows.
- **Eleven operations, as Orchestration Job types**:
  `create_test_suite`/`delete_test_suite`, `create_use_case`,
  `create_milestone`, `create_requirement`/`update_requirement`/
  `publish_requirement`/`delete_requirement`,
  `link_requirement_to_suite`/`unlink_requirement_from_suite`,
  `record_test_result` (manual), `import_junit` (E6: ingest an
  externally-produced JUnit-XML report), and `run_tests` (E6: sandboxed,
  on-demand).
- **Sandboxed test execution** (`run_tests`) — calls Deployment &
  Servers' worktree interface (`POST /v1/worktrees`, over a real Pillar
  Interface via the shared `common/worktree_client.py`, never a direct
  import into `deployment/`'s own tree) to provision an isolated
  checkout of a given `commitish`, runs the project's real test command
  inside it (`python3 -m pytest --junit-xml=...` by default,
  overridable), ingests the resulting report through the same
  `junit.py` parser `import_junit` uses directly, and always releases
  the worktree again — success or failure — so a failed run never leaks
  disk, and the project's own working checkout is never touched.
- **The definition-of-done report** (`GET /v1/report`) — this pillar's
  own half of the multi-pillar definition-of-done concept
  (`docs/pillars/policy_compliance.md`): a requirement is `done` only
  when it is `published` AND linked to at least one test suite AND
  every one of those suites' most recent run passed. `tested`/
  `passing`/`done` are all reported per requirement, not collapsed into
  one boolean, so a caller can see *why* a requirement isn't done yet.
  The *conflict-with-a-policy* half of the definition-of-done mechanism
  is Policy & Compliance's (M12), once that pillar exists to be the
  other party in the conversation.
- **Periodic test execution** ("run this project's suite every night")
  needed no mechanism of its own: it is just an Orchestration
  `ScheduledTask` (M9) whose `job_template` names `requirements.run_tests`
  — the scheduling mechanism built once in Orchestration, not
  duplicated here.

## Interfacing

- **Calls**: Deployment & Servers, for worktree provisioning during
  `run_tests`. Registry & Portfolio is not called directly — every
  operation is already `project_id`-scoped by the caller, the same way
  Version Control & Release resolves a project without this pillar
  owning that lookup itself.
- **Called by**: `lws requirements ...`, the `workshop_requirements_*`
  MCP tools, and Orchestration for every Job type above — including a
  recurring `ScheduledTask` (M9) for periodic test runs, no different
  from any other caller of `run_tests`. Policy & Compliance (M12) is
  planned to read this pillar's definition-of-done report as one input
  to its own conflict-check flow.
- **Discovered through**: Core, like every pillar.
