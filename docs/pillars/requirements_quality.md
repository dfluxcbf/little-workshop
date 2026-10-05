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
- `module-suites` — for a project already imported another way (`register`,
  typically): one Suite per real test module (`test_foo.py` -> suite
  `test_foo`, folding a class-based test's trailing class name into its
  module), recording that module's own results — never linked to any
  requirement, so a requirement's done/passing state is unaffected. Exists
  purely so the Dashboard's Report page can show tests grouped by real file
  instead of one flattened bucket (`REQ-000000215`, correcting `REQ-000000212`'s
  first pass, which reused the coarse `register` suite literally).

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

## UI tests with Playwright (2026-09-24)

A project's UI/UX tests are **pytest-playwright tests under a `ui` directory of its tests tree**
(`tests/ui/test_menu.py`, `tests/ui/flows/test_checkout.py`), using relative URLs (`page.goto("/")`). They
are ordinary pytest tests — collected, run and recorded like every other — with one thing added: they need
the app they test to be running.

When a sandboxed run (`requirements.run_tests`, by `suite_id` or `suite_ids`) includes any such file, the
project's pytest has pytest-playwright (`pytest -VV`'s plugin listing), and the project's
`manifest.deployment.toml` has a `[server.screenshot].command`, this pillar (`requirements/testrun.py`,
`ui_app`):

1. starts that command from the worktree on a free port (`{port}` substituted), its output to
   `.requirements-ui-app.log` in the worktree;
2. waits up to the manifest's `ready_timeout_seconds` for the port to listen — if it never does, the run
   fails with the command and the log's tail, rather than reporting every UI test as a failure;
3. runs pytest with `--base-url=http://127.0.0.1:<port>` added;
4. stops the app again, whatever the outcome, before the worktree is released.

The same scratch-port command Agentic Development's screenshots use is reused rather than adding a second
"how to start this app" setting. A free port instead of the screenshot's fixed `port + 1000` means a test
run never collides with an agent's screenshot or another test run. Without pytest-playwright or a launch
command, the UI tests run exactly as they are — against whatever base URL their own configuration gives
them — so a project that starts its own server in a fixture is unaffected.

## Marker sync (2026-10-04)

REQ-000000346. `requirements.sync_markers` reads the `# --- REQ-nnnnnnnnn: <title> ---` (or `// --- …`) section
markers of the project's test files (`requirements/markers.py`; files git tracks or would track, in a test
directory) and brings the pillar in line with them in one Job:

- A marked requirement the pillar lacks is created as a draft from its row in the project's
  `docs/requirements.md` (`| REQ-… | **Title.** text | status |`), EARS-checked like any other. A marker with
  no such row, or whose text is not EARS, is reported under `missing_text` and not created.
- Every marked requirement is linked to `params.suite_id`, or to the suite named "Unit tests".
- Nothing is deleted or unlinked. `params.dry_run` only reports.

It replaces one `workshop_requirements_create` and one `_link_suite` call per requirement.
`lws requirements sync-markers --project-id <id>`, `workshop_requirements_sync_markers`.

## A test Job's answer: counts, not every result (2026-10-05)

REQ-000000384. `requirements.run_tests` and `requirements.import_junit` record every result in this
pillar's store, as before, and answer their Job with `{suite_id, ran (imported for an import),
verdicts: {verdict: count}, failing, failing_total}`: at most 50 failing tests (`failed` or `error`),
each with the first 500 characters of its detail (`FAILING_SHOWN`, `DETAIL_SHOWN`). A run of several
suites answers `{suites: [{suite_id, name, ran, verdicts}], ran, verdicts, failing, failing_total}`, each
failing test naming its suite. Before, the answer carried every result (3,317 of them, 1.1 MB, for this
project's unit suite), and every list of Jobs and every reader of the Job carried them too. The results
themselves stay at `GET /v1/test_results`. Agentic Development's CI check reads the counts.

