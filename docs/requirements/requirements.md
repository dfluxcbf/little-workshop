# Requirements: Requirements & Quality

Part of little-workshop's requirements register; conventions and the other parts are in [../requirements.md](../requirements.md). Sections keep the order and headings of the round that added them.

## M10 — Requirements & Quality

| ID | Requirement | Status |
|---|---|---|
| REQ-000000095 | `little-requirements`' own never-built M8 — a Use-Case/Milestone axis independent of test suites entirely — implemented as two more `project_id`-scoped tables (`use_cases`, `milestones`), each optionally linked from a `Requirement` (`use_case_id`/`milestone_id`), with no requirement to link a suite at all. | done |
| REQ-000000096 | A requirement has a draft/published lifecycle: `update_requirement` is refused once a requirement's `status` is `published` (`"only a draft requirement can be edited"`), and `publish_requirement` is the only way to lock it — mirroring the draft/published convention this ecosystem's own `docs/requirements_policy.md` already establishes elsewhere. | done |
| REQ-000000098 | `record_test_result` records a single result manually (`source: "manual"`); `import_junit` — `little-requirements`' own never-built E6 half ("JUnit-XML ingestion beyond pytest") — parses a real `<testsuite>`/`<testsuites>` report (`requirements/junit.py`, no third-party XML library) into one `TestResult` row per `<testcase>`, mapping `<failure>`/`<error>`/`<skipped>` to `failed`/`error`/`skipped` and an absent one to `passed`, tagged `source: "junit"`. | done |
| REQ-000000100 | A definition-of-done report (`GET /v1/report`) implements this pillar's own half of the multi-pillar definition-of-done concept (`docs/pillars/policy_compliance.md`): a requirement is `done` only when it is `published` AND linked to at least one test suite AND every one of those suites' most recent run passed — `tested`/`passing`/`done` all reported per requirement, not just a single boolean, so a caller can see *why* one isn't done yet. | done |

## M15 — Self-hosting / dogfooding

| ID | Requirement | Status |
|---|---|---|
| REQ-000000149 | All 146 requirements in this file (M1 through M14, both M12 follow-ups) migrated into real Requirements & Quality `Requirement` rows for the `little-workshop` project — one real `Milestone` row per `##` section here (16 total), `status` mapped `done`→`published`/`not done`→`draft` (REQ-000000127 is the one `draft` row, matching its own "not done" status above). Requirements & Quality's own `done` field (tested *and* passing) is `false` for every migrated row — a documented, honest gap, not a claim of test coverage: no test-suite linkage was migrated, since this file's own markdown never encoded one. Requirements & Quality's first real user, per `docs/governance_policy.md`'s self-application principle. | done |

## M16 follow-up — parity survey and rejecting compatibility shims (2026-09-17)

| ID | Requirement | Status |
|---|---|---|
| REQ-000000172 | Proven at the unit level (`test_cli_success_paths.py`: 15 new tests covering `export`'s default/`--only`/`--suite` filtering, `import`'s create/link/skip-conflict/`--edit`/reject-published paths, `report`'s new file-writing flags and `--suite`/`--requirement` filtering with the pre-existing no-flags default left intact, and `test-structure-create`'s force/warn-on-existing/prompt-and-abort paths) and end-to-end (`test_requirements_end_to_end.py`'s new test: a real bulk `import` against a real Requirements & Quality `systemd --user` unit, re-importing the same payload confirmed as an idempotent no-op conflict rather than a duplicate, a real `export` and `--suite`-scoped `report` file-write, and a real `test-structure-create` against the real suite the same session created). | done |

## M17 — ecosystem cutover begins: `little-mindcheck` as pilot (2026-09-17)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000188 | **`little-mindcheck`'s existing `little-requirements` data, migrated** — 23 requirements (all `published`, all with at least one passing linked test case in the old tool's own SQLite DB) re-created in Requirements & Quality via `lws requirements import`, linked to a new "M2 v2 PWA" suite matching the old tool's own suite name, published, and given one real manual `passed` `TestResult` against that suite (the old tool's own per-test hashes — `test_case_id = sha256(test_node_id)` — are one-way and couldn't be recovered as readable test names, so the migration records the suite-level pass signal the old tool's own data actually carried, not synthetic per-test noise). `lws requirements report --project-id little-mindcheck` afterward shows `done_count: 23` of 23, exactly matching the old tool's own computed state before the cutover. | done |

## Workbench usability: ordering, workspace session, reporting, bootstrap, scheduling (2026-09-21)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000198 | **Requirements must follow EARS.** When a requirement is created or its description is edited, the Requirements pillar shall refuse a description that is not in EARS form and state how to correct it, except for requirements imported in bulk from another tool (`requirements/ears.py`). | done |

## Dashboard page-load sweep: Pillars page fixed (2026-09-23)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000249 | **The Definition-of-Done report resolves every requirement's suites and verdicts in a fixed number of queries, not one pair per requirement.** Where `/v1/report` builds a project's report, Requirements & Quality shall look up every requirement's linked suites and every suite's latest verdict via one batched query each (`suites_for_requirements`, `latest_results_for_suites`), never a Python loop issuing two queries per requirement. Root cause: even after REQ-000000246's index, the per-requirement loop was still ~420 separate round trips for little-workshop's own ~230 requirements (~0.5-0.6s), each carrying its own fixed per-query overhead regardless of indexing. Verified against a copy of the live data: 0.43s -> 0.011s (~40x) for the same report. | done |

## Playwright as standard: MCP for agents, UI tests run against the app (2026-09-24)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000255 | **UI tests run against the branch's own app.** When a Requirements & Quality sandboxed test run includes test files under a `ui` directory of the tests tree, and the project's pytest has pytest-playwright, and the project's `manifest.deployment.toml` has a `[server.screenshot].command`, the pillar shall start that command in the worktree on a free port, pass `--base-url` for it to pytest, and stop it after the run whatever the outcome; if the app never starts listening, the run shall fail with the command and the tail of the app's output. See `docs/pillars/requirements_quality.md`, "UI tests with Playwright". | done |

## Claude usage, automation: mining and workshop triggers (M21, 2026-10-04)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000346 | **The requirements the test markers name are created and linked by one Job.** When the `requirements.sync_markers` Job runs for a project, the Requirements & Quality pillar shall read the `REQ-nnnnnnnnn` section markers of the project's test files, shall create as a draft each marked requirement it lacks whose row in the project's `docs/requirements.md` is in EARS form, shall link every marked requirement to the given suite or the suite named Unit tests, shall report the marked requirements it could not create for want of a valid text, shall delete and unlink nothing, and shall only report when `dry_run` is set. | draft |

## Integration tests never beside the live install (2026-10-05)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000381 | **Integration tests never stop the live pillars.** While a `little-workshop-*` unit of the live install is active on the host, the integration test suite shall not be collected, whoever runs the tests (a release's test condition, a scheduled or requested test run, a person), unless the run sets `LITTLE_WORKSHOP_INTEGRATION_BESIDE_LIVE=1`, and shall say why it was left out; found when the weekly Release job's test run of 2026-10-05 ran it, restarted the live pillars and left the release itself running with no worker. | draft |

## Reading only what is shown; Claude's short answers watched (2026-10-05)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000384 | **Test runs answer with counts, not every result.** When a test run or a JUnit import records its results, the Requirements & Quality pillar shall answer its Job with how many tests ran and how many had each verdict (per suite where it ran several), and at most 50 failing tests with a short detail, never every result, which stays in its own store. | draft |

## Requirement conflicts, found by code and resolved by a Claude task (2026-10-30)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000468 | **Requirement conflicts are found by code and resolved by a Claude task the user directs.** Superseded: the comparison no longer runs every 15 minutes but once a project's requirements have settled (REQ-000000473). | superseded by REQ-000000473 |
| REQ-000000473 | **Requirement conflicts are found by code once a project's requirements have settled, and resolved by a Claude task the user directs.** When a project's requirements have not changed for 15 minutes since their last change, the Requirements & Quality pillar shall compare that project's requirements once, using deterministic code only, and shall not compare a project whose requirements have not changed since its last comparison, and record as a conflict two requirements with the same key, one whose EARS statement requires what the other's forbids under the same conditions, or the same statement with different numbers, and the Dashboard shall show each project's open conflicts as needing attention and offer on a conflict's page `Older requirement wins`, `Newer requirement wins` or `Another approach` with required guidance text, whose submission starts a Claude task on the project to resolve it. | draft |
| REQ-000000472 | **Requirements export and import resolve ids.** When the user imports a requirements backup, the Requirements & Quality pillar shall give every imported row a new id and renumber each requirement key the target project already has to the next free key. | draft |

## Claude configuration: the newest request outranks earlier decisions (2026-10-24)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000448 | **Superseding a requirement is one call.** When a draft or published requirement is superseded with a new key and text, the Requirements pillar shall create the new draft with the old one's category, use case, milestone and suite links, mark the old one superseded by the new key, mark the old row of the project's register (`docs/requirements.md` or, where the register is split, the `docs/requirements/*.md` part that holds it) superseded and add the new row after it in the same file, and return the test files whose markers still cite the old key; a superseded requirement shall not be published and shall be left out of the definition-of-done report. | done |
| REQ-000000450 | **The EARS form is advice, never a refusal.** When a requirement's description is not in EARS form, the Requirements pillar shall keep the requirement as written on create, update, supersede and marker sync and return the reason as a warning (marker sync lists it under `not_ears`), refusing only a missing description. | done |

## Requirements and policies only when necessary (2026-11-07)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000500 | **A requirement's verdict uses its own tests.** When a JUnit report is imported or a test run is recorded, the Requirements pillar shall tag each result with the requirement whose `# --- REQ-nnnnnnnnn: ... ---` section in the registered checkout's test file holds the test, and the Definition-of-Done report shall judge each requirement on the tagged results of its suites' latest runs, falling back to a suite's whole latest run where none of its results is tagged with the requirement, and shall say which basis each verdict used. | draft |

## Windows-drive projects as an explicit host (2026-11-07)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000516 | **A Windows-hosted Unity project's tests run through run_tests.** When `requirements.run_tests` runs a suite of a Windows-hosted Unity project (one with ProjectSettings/ProjectVersion.txt), the Requirements pillar shall run the project's `tools/run-unity-tests` (or the given test command) in its own checkout rather than a worktree, shall refuse with a reason when the checkout is not at the requested commit or a Unity Editor has the project open (its Temp/UnityLockfile present and a Unity.exe naming the project's path running), and shall record the JUnit report's results under the suite. | draft |

## Phase 6: closing the gaps (2026-11-07)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000523 | **A user-started semantic check lists suspected requirement conflicts, and one can be recorded.** When the user starts the New Job catalog's "Check requirement conflicts (semantic)" for a project, the Requirements & Quality pillar shall have Claude (agent.query, no file access) compare the project's published and draft requirements and shall return the pairs it suspects conflict in meaning, each with the two keys, the conflicting texts, a summary and a reason, recording nothing; when the user records a suspected pair from the Job's page, the pillar shall add it as an open conflict of kind `semantic` (recording it again reopens the same conflict), which the lexical analysis shall leave open until either requirement is deleted, superseded or edited after it was recorded. | draft |
