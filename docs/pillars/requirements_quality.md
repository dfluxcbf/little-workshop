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

## Importing existing projects

`tools/import_requirements.py` (2026-09-21) loads a project into this pillar
from real evidence, re-runnably (requirements, suites and links matched by key
or name; every run records a fresh batch of results; `--dry-run` writes
nothing):

- `lreq` — a project tracked by the old `little-requirements` tool. Its
  database gives the requirements (draft stays draft, published is published),
  the suites and which tests verify which requirement; a JUnit report of a real
  run gives what the tests did. The old tool identified a test by the sha256 of
  its node id (`tests/test_x.py::Class::test`, or `classname::name` for
  vitest); the importer reproduces those ids and reports how many matched, and
  which known tests were missing from the run. Each suite's results are the
  tests that verify its requirements; tests no requirement points at go to a
  "Tests without a requirement link" suite so nothing real is left out.
- `register` — little-workshop's own `docs/requirements.md`, which has no
  per-requirement test links: milestones are created from its sections, `done`
  rows are published, and every done requirement is linked to one "Unit tests"
  suite holding the whole real run (so a failing test anywhere makes them all
  not done — coarse, and honest about it).

A suite passes only if every test in its latest run passed; a skipped test does
not count as passing.

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
- **M16 `little-requirements` parity, pure CLI-side** (2026-09-17):
  `lws requirements export`, `import`, `report --md/--csv/--json-file`,
  and `test-structure-create` match the old tool's own `requirement
  export`/`import`, `report --csv/--md/--json`, and `test-structure
  create`. All four are additions to `src/cli/main.py` only — no new
  Pillar Interface route, since each is either a local file-writing
  convenience over an existing read (`export`/`report`) or repeated
  calls to the Job types M10 already exposed (`import`, one
  `create_requirement`/`update_requirement` plus
  `link_requirement_to_suite` per item). `import` matches by the
  human-facing `key`, not a server-generated `id`, since little-workshop
  (unlike the old tool) generates requirement IDs itself.
  `set-verdict` was found to already have full functional parity via
  the existing `result record` command (`requirements.record_test_result`
  already writes `source="manual"`) — no new code needed. Three real
  gaps from the old tool are deliberately left un-ported: `workspace`/
  `install`/`update` (a client-side `.reqs`/`database.conf` config-file
  model with no equivalent in a server-authoritative daemon — the same
  "no equivalent" finding as `little-runner`'s own `.lrun/config.toml`),
  `project` create/delete/list/get (superseded outright by Registry &
  Portfolio, M2 — this pillar was never meant to own "project" at all),
  and `lock`/`unlock`/`unlock-server` (concurrent-writer file locking
  for N processes sharing one SQLite file — moot once a single daemon
  process is the only writer, per-request serialization already
  handled by this pillar's own `threading.Lock()`).

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

## EARS enforcement (2026-09-21)

`requirements.create_requirement`, and `requirements.update_requirement` when it changes the description,
refuse a description that is not in EARS form and reply with how to fix it (`requirements/ears.py`, a
deterministic pattern check: an optional `While`/`When`/`Where`/`If … then` prefix, then a determiner, a
system name and `shall`). A bulk import from another tool passes `imported: true` to skip the check; requirements
recorded before the rule keep their wording until edited.
