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

- `register` — little-workshop's own register (`docs/requirements/*.md`), which has no
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
- **Superseding is one operation** (REQ-000000448, 2026-10-24) —
  `supersede_requirement` (`workshop_requirements_supersede`, `lws
  requirements supersede`) takes a draft or published requirement and
  a new key and text: it creates the new draft with the old one's
  category, use case, milestone and suite links, marks the old one
  `superseded` (`superseded_by` names the new key), marks the old row
  of the project's register "superseded by" and adds the new row after
  it in the same file (`docs/requirements.md`, or the `docs/requirements/*.md`
  part that holds it where the register is split), and returns the test files whose markers still cite
  the old key. A superseded requirement cannot be published and is left
  out of the definition-of-done report; the policy
  `requirement-marker-not-superseded` refuses a commit that leaves a
  test citing it. Cheap on purpose: a newer request changes the row
  instead of being bent to fit it.
- **Eleven operations, as Orchestration Job types**:
  `create_test_suite`/`delete_test_suite`, `create_use_case`,
  `create_milestone`, `create_requirement`/`update_requirement`/
  `publish_requirement`/`supersede_requirement`/`delete_requirement`,
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
  The exception is a Windows-hosted Unity project (REQ-000000516): `run_tests` by `suite_id` runs its
  `tools/run-unity-tests` in the checkout itself (the editor's `Library/` reused), refusing when the
  checkout is not at the `commitish` or a Unity Editor has the project open (`Temp/UnityLockfile` and a
  `Unity.exe` naming its path), and records the JUnit report as `junit` results; `suite_ids` is refused
  for it (the editor runs all its tests at once).
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
  create`. All four are additions to `src/cli/commands/requirements.py` only — no new
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
  `run_tests`. Registry & Portfolio, only by `run_tests`, to learn
  whether the project is a Windows-hosted Unity one.
- **Called by**: `lws requirements ...`, the `workshop_requirements_*`
  MCP tools, and Orchestration for every Job type above — including a
  recurring `ScheduledTask` (M9) for periodic test runs, no different
  from any other caller of `run_tests`. Policy & Compliance (M12) is
  planned to read this pillar's definition-of-done report as one input
  to its own conflict-check flow.
- **Discovered through**: Core, like every pillar.

## EARS enforcement (2026-09-21)

Advice, never a refusal, since 2026-10-24 (REQ-000000450: a form check that refuses only costs turns of
rewording). `requirements.create_requirement`, `update_requirement` when it changes the description, and
`supersede_requirement` keep a description that is not in EARS form and return how to fix it in the result's
`warnings` (`requirements/ears.py`, a
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
  register (`docs/requirements.md` and its `docs/requirements/*.md` parts; `| REQ-… | **Title.** text | status |`), EARS-checked like any other. A marker with
  no such row (or an empty one) is reported under `missing_text` and not created; one whose text is not EARS
  is created all the same and listed under `not_ears`.
- Every marked requirement is linked to `params.suite_id`, or to the suite named "Unit tests".
- Nothing is deleted or unlinked. `params.dry_run` only reports.

It replaces one `workshop_requirements_create` and one `_link_suite` call per requirement.
`lws requirements sync-markers --project-id <id>`, `workshop_requirements_sync_markers`.

## Per-requirement verdicts (2026-11-07)

A recorded test result carries `requirement_key`: the requirement whose `# --- REQ-nnnnnnnnn: … ---` section of
the registered checkout's Python test file holds the test (`markers.test_sections`, read when `import_junit` or
`run_tests` records a run; a test belongs to the nearest marker above it, and any other `# --- … ---` header,
`REQ-NONE`'s included, ends a section). The JUnit `classname::name` is matched by module (or module of its class)
and function name, without pytest's `[parameters]`. In `/v1/report`, a requirement's verdict on a suite is the worst
of that suite's latest-run results tagged with its key, so one failing test no longer fails every requirement of
the suite; a suite whose latest run has none of its tests falls back to the whole run, as before. Each row says
which basis it used (`basis`: `own tests` | `suite`) and `own_tests` (`total`, `failing` names); the Dashboard's
Report shows it. Without a readable checkout (or for other languages) nothing is tagged and every verdict is the
suite's. REQ-000000500.

## A test Job's answer: counts, not every result (2026-10-05)

REQ-000000384. `requirements.run_tests` and `requirements.import_junit` record every result in this
pillar's store, as before, and answer their Job with `{suite_id, ran (imported for an import),
verdicts: {verdict: count}, failing, failing_total}`: at most 50 failing tests (`failed` or `error`),
each with the first 500 characters of its detail (`FAILING_SHOWN`, `DETAIL_SHOWN`). A run of several
suites answers `{suites: [{suite_id, name, ran, verdicts}], ran, verdicts, failing, failing_total}`, each
failing test naming its suite. Before, the answer carried every result (3,317 of them, 1.1 MB, for this
project's unit suite), and every list of Jobs and every reader of the Job carried them too. The results
themselves stay at `GET /v1/test_results`. Agentic Development's CI check reads the counts.

## Requirement conflicts (2026-10-30)

REQ-000000473. `requirements/conflicts.py` compares a project's requirements with code alone, so the same
requirements always give the same conflicts. Each description is read as EARS statements: each sentence, and
each `, and the <system> shall …` within one, keeping the sentence's `While`/`When`/`Where`/`If` clauses. Words
are compared in lower case, without punctuation and without `a`/`an`/`the`. Two requirements of one project
conflict when:

- **Same key** (`duplicate_key`): each later copy of a key is paired with the first copy;
- **Contradiction**: a statement in each has the same conditions, system and response, and one says `shall`
  where the other says `shall not` or `shall never`;
- **Different values**: same conditions, system and polarity, and the same response apart from its numbers.

The older requirement is the one created first. A conflict's id hashes its kind, the two requirement ids and
the statements involved, so every analysis finds a conflict under the same id. The table
`requirement_conflicts` holds each one as `open`, then `resolving` once the user has chosen a resolution, then
`resolved` when an analysis no longer finds it (because a requirement was changed or deleted). A resolved
conflict that comes back is `open` again.

The running pillar analyzes a project once its requirements have settled (REQ-000000473). Every requirement
write (`insert_requirement`, `update_requirement`, `delete_requirement`, and a backup import) stamps the project
in `conflict_scan_due (project_id, changed_at)`, which survives a restart. Once a minute
(`CONFLICT_SCAN_POLL_SECONDS`) the pillar analyzes each registered project whose stamp is at least 15 minutes old
(`CONFLICT_SCAN_QUIET_SECONDS`) and removes the stamp unless a change re-stamped it meanwhile, so a burst of edits
leads to one analysis. Nothing is analyzed at startup or on a timer, and an unchanged project never again.
`requirements.analyze_conflicts` analyzes one project, or all of them when no project is given, on demand; it is in the Dashboard's New Job catalog as "Check requirement conflicts". Reads:
`GET /v1/conflicts?project_id&status`, `GET /v1/conflicts/{id}` (with both requirements as they are now, or
None once deleted), and `GET /v1/conflicts/version`, which the Dashboard's live stream polls. MCP:
`workshop_requirements_conflicts`.

On the Dashboard, each project with open conflicts is one item in the attention list, linking to
`/requirements/conflicts` (Documentation → Conflicts). A conflict's page shows the two requirements side by side
and offers **Older requirement wins**, **Newer requirement wins** or **Another approach**; the last one needs
text saying what to do. Submitting starts Agentic Development's `agent.resolve_requirement_conflict` on the
conflict's project. That job builds an ordinary `agent.run_task` (intent `task`, manual review unless the user
chose auto-approve) from the conflict and the decision (`agent/requirement_conflicts.py`), then records the
decision and the Job on the conflict through `requirements.decide_conflict`. The task tells Claude to change
requirements only through this pillar: a draft can be edited, a published one is deleted and, where some of it
still holds, replaced under the next free key. The conflict is resolved when the next analysis no longer finds
it. A conflict still `resolving` can be resolved again from its page, for example when its task failed.

**Semantic check (REQ-000000523).** Optional and user-started only: the New Job catalog's "Check requirement
conflicts (semantic)" runs `requirements.semantic_conflicts`, which sends the project's published and draft
requirements to Claude (`agent.query`, no file access; `requirements/semantic.py`) and returns the pairs it
suspects conflict in meaning, each with its texts, summary and reason, in the Job's result; nothing is recorded.
The Job's page lists them with **Record as a conflict**, which runs `requirements.record_semantic_conflict`: an
`open` conflict of kind `semantic` (the same pair and texts again reopen it). The lexical analysis neither finds
nor resolves such a conflict; it resolves it once either requirement was deleted, superseded or edited after it
was recorded. Deciding and resolving it work as for any other conflict.


## Backup, import and deletion of a project's requirements (2026-11-01)

`requirements/transfer.py`. `GET /v1/projects` lists every project id with stored data and its counts;
`GET /v1/export/{project_id}` returns a JSON backup (`format` `little-workshop-requirements`, `version` 1: use cases,
milestones, test suites, requirements with their suite ids, test results; conflicts are derived and left out).
`requirements.import_project` puts a backup into the project id in `project_id`, which may be the original, a new one
or a registered project's: every row gets a new id, and a requirement whose key the target already has gets the next
free key (the trailing number moves past the highest taken one, keeping its width; a key without a number gets
`-2`, `-3`), the old and new keys returned. An invalid file writes nothing (one transaction).
`requirements.delete_project_data` deletes everything stored for a project id, irreversibly. The Dashboard's
[Data management](dashboard.md) page is the user-facing side. The periodic analysis and `requirements.analyze_conflicts`
without a project cover registered projects only.
