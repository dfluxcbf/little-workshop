# Requirements: Policy & Compliance

Part of little-workshop's requirements register; conventions and the other parts are in [../requirements.md](../requirements.md). Sections keep the order and headings of the round that added them.

## M4 — Deployment & Servers

| ID | Requirement | Status |
|---|---|---|
| REQ-000000051 | Worktree provisioning (`POST /v1/worktrees`, `DELETE /v1/worktrees/{id}`) is exposed as a shared capability other pillars call into, not something the deploy flow keeps to itself — the thing Policy & Compliance's test-script sandbox (M12) and Requirements & Quality's sandboxed test execution (M10) will call rather than each provisioning their own. | done |

## M12 — Policy & Compliance

| ID | Requirement | Status |
|---|---|---|
| REQ-000000114 | Scope precedence resolved per (topic, project): framework (if one exists) > project-specific user policy > a project-set user policy the project belongs to > an all-projects user policy (`policy/precedence.py`'s `resolve_winning_policy`). A project belonging to more than one policy-bearing project set on the same topic — an ambiguity the design leaves open — resolves deterministically (lowest `scope_target`), not by insertion or dict order. | done |
| REQ-000000115 | Topic ownership: a topic always maps to exactly one `target_pillar`, checked mechanically at both Gate 3a (`find_by_scope`: the same scope-level+target+topic already exists) and its sibling 3a2 (`find_topic_owner_conflict`: the topic already exists targeting a *different* pillar) — both reject outright, never guess. A user-created policy may never target Policy & Compliance itself (`target_pillar == "policy"`); a framework policy is exempt. | done |
| REQ-000000118 | The test-script execution sandbox (`policy/sandbox.py`) runs every check inside a real Docker container: a once-built `little-workshop-policy-sandbox` image (`python:3.12-slim` + `pytest`), read-only bind mounts for exactly the policy's own project-bound (the project's checkout plus its `~/.config/little-workshop/projects/<id>/` files) or framework-bound (`src/`, `docs/`) allowlist, `--read-only` plus a `tmpfs /tmp` for the container's own filesystem, and a hard cleanup (`docker rm -f`) that runs unconditionally — verified for real to actually stop an orphaned container a Python-level timeout alone does not reach (killing the `docker run` client process never cascades to the daemon-owned container; only the explicit `rm -f` does). | done |
| REQ-000000119 | Three outcomes per check — `pass`/`fail`/`not_applicable` — parsed from pytest's own real summary line (`1 passed`/`1 failed`/`1 skipped`), proven for real against a genuinely read-only mounted project (a script attempting to write into it gets a real `OSError`, not a mocked one) and a genuinely hung script (a 30-minute ceiling, tested at a much shorter duration, that reliably kills the container with no orphan left running). A `policy.check_project` Job is `"succeeded"` at the Job level for any determined verdict, including `fail` — only the sandbox's own ceiling being hit is a genuine Job `"failed"`, matching the specification's "no verdict at all is a framework-level fault, not a policy outcome." | done |
| REQ-000000120 | **Documented simplification**: the sandbox's network isolation is `--network none` (no network access at all, including to other pillars' Pillar Interfaces) rather than the specification's finer-grained "Pillar-Interface-only, safe-endpoints-only" proxy — building that proxy (classifying every PI endpoint safe/mutating and enforcing it at the sandbox's network boundary) is out of this pass's scope. A generated test script cannot currently call any pillar's PI from inside the sandbox. | done |
| REQ-000000122 | Two real endpoints resolve a `pending_confirmation` Job: `POST /v1/jobs/{id}/policy_confirm` dispatches it for real, exactly as if the check had passed (`lws job policy-confirm`); `POST /v1/jobs/{id}/policy_confirm_timeout` — the watchdog's own callback — settles it `failed` with `error: "policy check unconfirmed"` if still pending (idempotent no-op otherwise, the same "already settled" pattern the existing deploy-confirmation watchdog uses) and cascades that failure to anything depending on it. Proven end-to-end for real: a gated `deployment.deploy` reaches `pending_confirmation` with the server genuinely not started, a real confirm dispatches it and the server genuinely starts, and — in a separate real run — the real timeout endpoint settles it failed with the server genuinely never started. | done |
| REQ-000000123 | **Documented simplification**: which Job types declare which gating topics is deliberately small in this pass — `docs/open_questions.md`'s `OQ-3` leaves the full mapping to each pillar's own future work; `deployment.deploy` on topic `deployment-policy` is this pass's own concrete, real proof of the mechanism, not a claim every gated Job type across every pillar is enumerated yet. Likewise, a Policy & Compliance pillar that's unreachable at Job-creation time is treated the same as "no policy currently governs this topic" (dispatch proceeds ungated) rather than the specification's full fail-closed-on-unreachable-policy-pillar behavior — building indefinite-queuing-until-reachable is out of this pass's scope. | done |

## M13 — Populate the policy catalog

| ID | Requirement | Status |
|---|---|---|
| REQ-000000129 | `policy/catalog.py` parses `docs/pillars/policy_catalog.md`'s own markdown tables directly (59 entries, sections A-K) into structured `CatalogEntry` rows (`topic`, `ears_text`, `source`, `section`, `section_title`) — the catalog document itself stays the single source of truth; nothing here duplicates its content into a second, driftable copy. | done |
| REQ-000000130 | `lws policy catalog list` / `workshop_policy_catalog_list` lists every entry; `lws policy catalog run <topic> --target-pillar <p>` / `workshop_policy_catalog_run` looks the entry up by topic and dispatches the identical `policy.author` Job any hand-written policy uses, with `scope_level: all_projects` and `created_by: framework` filled in automatically — matching the catalog's own "Resolved 2026-09-15 §1: framework tier, for everything in this catalog." `target_pillar` is supplied by the caller, since the catalog document itself doesn't state one per entry (see REQ-000000132's documented gap). The resulting draft still requires the identical `policy.activate` call, with explicit human approval, as any other policy — this tool only ever reaches Gate 3; there is no shortcut around Gate 4. | done |
| REQ-000000132 | **A real defect found and fixed via a live run against the real Claude CLI** (not the deterministic fake-stub tests, which couldn't have caught this): `policy.author`'s script-generation prompt (`policy/gates.py`) never stated where the sandbox actually mounts a project's checkout, so the real Claude guessed at conventions that don't exist (an `LWS_PROJECT_ROOT` env var, walking up from the working directory looking for `.git`) — either of which finds nothing, since the sandbox's own cwd is `/policy`, not inside the checkout. A compliant project's own check would have failed regardless. Fixed by stating the real, fixed mount paths (`/project`, `/project-files`, `/framework/src`, `/framework/docs`) explicitly in the prompt, with a regression test (`test_generate_test_script_states_the_real_sandbox_mount_paths`) locking in that the prompt keeps saying so. Re-run live against the real Claude CLI after the fix (entry `version-file-single-source`, target_pillar `version`): produced a correct script using the literal `/project` path, activated for real, and checked for real via the real Docker sandbox against a real bootstrapped project — verdict `pass`, 2/2 real pytest assertions passed. | done |
| REQ-000000133 | **Documented, deliberate scope reduction from "run all ~59 entries"**: Gate 4 requires genuine human review and approval of every policy's EARS text and generated test script, by design, no exception, no bulk shortcut — the milestone's own "each one earns its policies row the same way any future policy will" is fundamentally incompatible with unattended bulk population. This pass delivers the real, reusable tool (`lws policy catalog run`/`list`, `workshop_policy_catalog_*`) plus one entry actually carried through the full pipeline to a real, activated policy as a genuine working proof (REQ-000000131/132) — not all ~59 entries authored and activated. The remaining catalog entries stay queued as real future work, one topic at a time, through the identical tool. | done |

## M12 follow-up — Policy usability refinement (2026-09-16)

Requested directly: individual projects may need different policies, or may not be able to fulfill one at all — users need real control over which policies apply and how they gate, without weakening the framework tier's core guarantee into a casual toggle.

| ID | Requirement | Status |
|---|---|---|
| REQ-000000134 | Every `Policy` gains a real `enabled: bool` (default `true`, `policy/db.py`'s `set_policy_configuration`). A disabled policy is skipped entirely by precedence resolution and by `policy.check_all`'s own fan-out — never checked, never gates anything — without being deleted; re-enabling restores it exactly as it was. Toggling `enabled` is a new, lightweight Job type (`policy.configure`) that never re-runs any of the four gates, since it changes nothing about the requirement's own meaning. | done |
| REQ-000000135 | **Per-project waivers** (`policy/models.py`'s `PolicyWaiver`, a new `policy_waivers` table): an explicit, auditable exception — `policy.waiver_grant` requires a non-empty `reason`, `policy.waiver_revoke` removes it, both ordinary Job types. Precedence resolution (`policy/precedence.py`) skips a policy this project holds a waiver against and falls through to the next-lower-precedence policy on the same topic — exactly as if the waived one didn't exist, for that project only. This is deliberately a *different* mechanism from scope-shadowing: a framework policy still can never be silently overridden by a lower-scope policy; a waiver is a distinct, explicit, reversible act, granted per project, never a change to the policy itself. | done |
| REQ-000000136 | **Gating behavior is now a per-policy, user-chosen set of flags** (`policy.models.GatePoint`: `build_install`, `server_launch`, `deploy_redeploy`, `release`, `branch_finish`, `project_removal`), not a fixed Job-type-to-topic table. Chosen at authoring time (`policy.author`/`policy.activate`'s own `gate_points` param) or reconfigured afterward without re-authoring (`policy.configure`'s `gate_points` param — same lightweight, gate-free path as `enabled`). Editing a policy's EARS text/script (`policy.activate` with `policy_id`, `gate_points` omitted) never silently strips its existing gating configuration — only an explicit `gate_points` (including an explicit empty list) changes it. | done |
| REQ-000000137 | `orchestration/policy_gate.py`'s `JOB_TYPE_CATEGORIES` replaces the single-topic `GATED_JOB_TOPICS` table: each Job type declares which of the six categories it belongs to (`build.run`→`build_install`; `deployment.deploy`→`server_launch`+`deploy_redeploy`; `version.release_start`/`release_candidate_cut`/`release_candidate_promote`/`release_finish`+`build.publish_pypi`/`publish_vsix`→`release`; `version.release_finish`/`feature_finish`/`improvement_finish`/`hotfix_finish`/`support_finish`→`branch_finish`; `project.remove`→`project_removal`) — this pass's own real, partial coverage, not a claim every Job type everywhere is categorized (`OQ-3` stays open for the rest). A new Policy & Compliance read endpoint, `GET /v1/policies/gating-for-job`, resolves every currently-winning (enabled, non-waived) policy across every topic whose own `gate_points` intersects the requested categories — a Job can now be gated by more than one policy at once, one `policy.check_project` dependency per gating policy, not just a single fixed one. `maybe_gate` holds a Job at `pending_confirmation` if *any* of its governing checks comes back `fail`. | done |

## M12 follow-up #2 — Additional gate-point categories (2026-09-16)

| ID | Requirement | Status |
|---|---|---|
| REQ-000000141 | Proven via the existing generalized mechanism (REQ-000000136/137's category-based gating is Job-type-agnostic by construction — no new dispatch code path was needed, only new map entries and enum values), regression-tested at the unit level: `JOB_TYPE_CATEGORIES`'s four new mappings and `GatePoint`'s full ten-value membership are each asserted directly (`tests/unit/test_orchestration_policy_gate.py`, `tests/unit/test_policy_db.py`); the full unit suite (1463 tests) and the existing real `systemd --user` integration suite both still pass unchanged, confirming the expansion is additive. | done |

## M15 — Self-hosting / dogfooding

| ID | Requirement | Status |
|---|---|---|
| REQ-000000152 | **Documented, deliberate scope reduction**: the evolution principle was not formalized as a policy this pass — every mechanically-checkable candidate considered either restated something Gate 2 would reject as requiring human judgment (`"changes are deliberate"`) or was already fully satisfied by this document's own existing convention (a dated `## Amendments` entry), making a policy redundant with the document it would be checking. Left genuinely open, not silently skipped — a maintainer may find a better-scoped candidate later. Version tracking also stays on plain `git`, at the maintainers' own discretion, per `docs/milestones.md` M15's own text. | done |

## Testing fortification — +15% integration tests (2026-09-17)

| ID | Requirement | Status |
|---|---|---|
| REQ-000000154 | **Documented, deliberate scope reduction**: `self_redeploy` is the one 2026-09-16 gate-point category still without a real end-to-end gating proof — layering policy-gating onto `test_self_redeploy_end_to_end.py`'s own real self-redeploy mechanism (which physically stops and relaunches a real `systemd --user` unit) was judged too fragile to combine safely with new gating setup in this pass; left as real, tracked future work rather than forced through. | removed 2026-09-21 — dropped by decision (self_redeploy end-to-end gating proof is out of scope) |

## M12 follow-up #3 — Answering OQ-13/14/15 (2026-09-17)

| ID | Requirement | Status |
|---|---|---|
| REQ-000000155 | **OQ-13, answered — no code change.** Policy waivers stay exactly as built: `policy.waiver_grant`/`waiver_revoke` remain ordinary, ungated Jobs; a `reason` and `created_at` are the whole audit trail. Confirmed directly rather than added to as friction — the single-user household trust model (`docs/architecture.md`'s "no multi-tenant auth") already covers this. | done |
| REQ-000000156 | **OQ-14, answered.** `orchestration/dispatch.py` gains `JOB_TYPE_TIMEOUTS` (a per-Job-type override table, mirroring `orchestration/policy_gate.py`'s `JOB_TYPE_CATEGORIES` pattern) and `dispatch_timeout_for(job_type)`, consulted by `dispatch_ready_jobs` and `policy_gate.confirm` before calling `dispatch_job` — `policy.author` gets 120s, `policy.activate` gets 60s, matching the values `cli.client`'s own callers already used client-side. `DEFAULT_DISPATCH_TIMEOUT_SECONDS` (20.0, renamed from the old bare literal) is untouched for every other Job type, including a direct `dispatch_job` call that doesn't go through either lookup path. | done |
| REQ-000000157 | **OQ-15, answered — two real rules, not just a Gate-time restriction.** (1) `project_registration` may only be used in a policy's `gate_points` when that policy is framework-bound (`target_pillar == "policy"`) — enforced in `policy/app.py`'s new `_validate_gate_points_against_target`, called from `policy.author`, `policy.activate`, and `policy.configure` alike, rejecting a project-bound attempt outright with a clear error rather than letting it fail confusingly at check time. (2) `project_registration` is no longer in `orchestration/policy_gate.py`'s `JOB_TYPE_CATEGORIES` at all — `project.bootstrap`/`project.import` are never pre-dispatch-gated. Instead, a new `POST_COMPLETION_VERIFY_JOB_TYPES` table and `verify_after_completion` function fire a real `policy.check_project` Job *after* the real registration has already succeeded, fire-and-forget (no `depends_on` wiring back to the now-finished Job), called from `dispatch_job`'s own success branch. A `fail` verdict is recorded in `policy_check_results` exactly like any other check; nothing about it is enforced, since the action it would have gated already happened. | done |
| REQ-000000158 | Proven at the unit level (`test_policy_app.py`: Gate-time rejection/acceptance across `policy.author`/`policy.activate`/`policy.configure`; `test_orchestration_policy_gate.py`: `project.bootstrap`/`import` confirmed absent from `JOB_TYPE_CATEGORIES`, present in `POST_COMPLETION_VERIFY_JOB_TYPES`, and `verify_after_completion`'s own fire-and-forget behavior; `test_dispatch.py`: `dispatch_timeout_for`'s per-type lookup and `dispatch_ready_jobs`'s real use of it) and end-to-end (`test_policy_end_to_end.py`: a real `project.bootstrap` dispatches immediately despite an always-failing framework policy, with the resulting `fail` verdict recorded afterward on the *next* pump and the project still genuinely registered; authoring a project-bound `project_registration` policy is rejected with a real error). | done |

## M15 follow-up — the independence-principle draft was reviewed and rejected (2026-09-17)

| ID | Requirement | Status |
|---|---|---|
| REQ-000000159 | **REQ-000000150's draft, reviewed directly and rejected — not activated.** "No `.lvx/config.json`" was a description of the `little-versions` migration's state at the time `docs/governance_policy.md` was written, never intended as an ongoing invariant for a policy to check. The independence principle's own text already says why encoding it as a policy would have been wrong: "'Independent' means *not required to depend on*, not *forbidden from using*." Once the migration is complete, a stray `.lvx` directory's presence or absence is simply irrelevant — nothing in little-workshop reads it either way, so there is nothing left to check. The draft file is removed from `docs/pending_policy_drafts/` (never having been activated, nothing to undo); `docs/governance_policy.md` gains a dated amendment recording the rejection and its reasoning, without rewriting the 2026-09-16 entry that recorded the draft's own creation. | done |

## M17 — ecosystem cutover begins: `little-mindcheck` as pilot (2026-09-17)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000190 | **A second real, previously-undiscovered defect, the same OQ-14 class**: `deployment.deploy` genuinely shells out to a real `bazel run //:install` from a freshly provisioned worktree — on a cold cache this took long enough to blow past `orchestration/dispatch.py`'s 20-second default dispatch timeout, misreporting "target pillar unreachable" for a deploy that was actually still running and later succeeded on its own. Found the hard way, doing `little-mindcheck`'s own real pilot deploy. Fixed exactly like `policy.author`/`policy.activate` before it: `deployment.deploy` added to `JOB_TYPE_TIMEOUTS` (300s), and the CLI's/MCP's own `deployment.deploy` client calls raised to a matching 300s so the daemon-to-daemon fix isn't undone by a shorter client-side timeout one hop further out. | done |

## Workbench usability: ordering, workspace session, reporting, bootstrap, scheduling (2026-09-21)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000207 | **Full pillar names.** The Pillars page shall show each pillar under its full name with capital letters, such as Policy & Compliance, instead of its short identifier. | done |

## Policy draft review: gate points completed, a Gate 1 parsing bug fixed (2026-09-22)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000216 | **Gate 1 finds its own marker anywhere in the reply.** When Gate 1 parses a model's reply, the Policy pillar shall locate the `EARS_OK`/`EARS_REFINED` marker anywhere in the reply, using the last one if several appear, instead of requiring it at the very start. Found while reviewing pending policy drafts: `production-version-stable`'s stored `ears_text` had leaked the model's own reasoning preamble because the old regex only matched the marker at the start of the reply. | done |

## System health, automatic bug tasks, coverage policy, data catalog (2026-09-22)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000221 | **Python test coverage policy (90% threshold).** Where a project is a Python project in this ecosystem's own convention, the `python-test-coverage` policy shall run its real pytest suite with statement coverage measurement and shall fail when coverage is below 90 percent, judging only the current run. Runs inside the policy sandbox (`--network none`), which gained image `:3` — this ecosystem's own common runtime stack (fastapi, uvicorn, pydantic, httpx, jinja2, python-multipart, pyyaml, apscheduler) plus `pytest-cov`/`coverage`, since a project's own third-party dependencies can't be installed at check time. A project needing something outside that shared stack fails the check with a clear message rather than silently passing. Filed as a pending draft (`branch_finish`, `concerns: true` for this limitation), with a pre-filled project recommendation (little-spectrum ungated). Audited for real against every registered project's own checkout: little-workshop 95%, little-meals 97% (both via `tests/unit`, never `tests/integration`/`tests` wholesale against little-workshop's own live install); little-home is a TypeScript/Node project (no `pyproject.toml`, correctly skipped); little-mindcheck's suite fails to even collect here (a missing `itsdangerous`, a local environment gap, not a coverage finding); little-spectrum out of scope per standing instruction. | done |

## Policy sandbox isolation and change-driven checking (2026-09-23)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000241 | **A policy check never re-runs for an unchanged project and policy.** Superseded: the fingerprint is now the committed tree of the policy's declared inputs when it has them (REQ-000000482). | superseded by REQ-000000482 |
| REQ-000000482 | **A policy check never re-runs on unchanged inputs.** When policy.check_project is asked to check a project against a policy and the most recently recorded verdict for that policy and project was reached on the same fingerprint, the Policy & Compliance pillar shall return that verdict without running the sandbox and record it as a new result; the fingerprint shall be the committed git tree of the policy's declared inputs, or the project's HEAD commit when the policy declares no inputs, together with the policy's last amendment time, and an explicit force parameter shall always run the sandbox. | draft |
| REQ-000000242 | **The policy sandbox cannot starve the host's own live pillars.** Where the Policy pillar runs a check inside its Docker sandbox, the container shall be capped on CPU, memory and process count (`--cpus`, `--memory`, `--pids-limit`), so a slow or runaway check — even several running at once — can never take enough of the host's resources to affect little-workshop's own pillar processes' health-probe responsiveness. The `python-test-coverage` policy's own script additionally prefers a project's `tests/unit` directory over its full `tests` dir when present, since anything under `tests/integration` can only ever time out inside a `--network none` sandbox. | done |

## Sandbox pids-limit regression fixed (2026-09-23)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000245 | **The sandbox's own process-count cap never manufactures false test failures.** Where the policy sandbox runs a project's own real test suite, the `--pids-limit` given to `docker run` shall be high enough that a legitimate, subprocess- and thread-heavy suite (real git commands, real `TestClient`/uvicorn instances) never has an individual test fail merely because it couldn't fork/spawn under the cap. Root cause of both Needs-Attention failures this fixes: REQ-000000242's original 256 was picked without ever running little-workshop's own ~2150-test suite against it — 889 tests failed and 68 errored purely from hitting that ceiling, dragging measured coverage from a real 95% down to a reported 69%. Raised to 2048, verified clean (2 unrelated, pre-existing sandbox-environment failures only, real 95% reported) — still a real ceiling against an actual fork bomb, just not one a normal test run can trip. | done |

## Claude-native policy enforcement (2026-10-02)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000299 | **The commit-msg hook refuses a commit that breaks a policy gating commit.** Superseded: the hook runs only a local secret scan; the other commit policies gate the finish (REQ-000000490). | superseded by REQ-000000490 |
| REQ-000000300 | **Policies can be evaluated without recording or holding anything.** Superseded: a preflight now records its verdicts on committed state, and reuses fresh ones (REQ-000000483). | superseded by REQ-000000483 |
| REQ-000000483 | **A preflight records verdicts the gate reuses, and holds nothing.** When policy.evaluate runs for a project and a gate point, the Policy & Compliance pillar shall return, for every winning policy gating that point, a verdict with its reason and the topics that would block, without holding a Job, and shall judge another checkout only if it shares the registered checkout's repository; while the judged paths have no uncommitted change and no pending commit is evaluated, the pillar shall reuse a recorded verdict reached on the same fingerprint instead of running the sandbox, and shall record each new verdict under that fingerprint. | draft |
| REQ-000000484 | **A check that runs the test suite records the Unit tests run.** When a policy check leaves a JUnit report in its sandbox output directory, the Policy & Compliance pillar shall submit a requirements.import_junit Job that records the report under the project's test suite named "Unit tests", so the Definition-of-Done run needs no second run of the suite. | draft |
| REQ-000000302 | **A policy check records why it failed.** When a policy check's script completes, the Policy & Compliance pillar shall record as the result's detail the assertion messages and skip reasons the script printed, with pytest's summary line, rather than only the last lines of its output. | draft |
| REQ-000000303 | **Every policy is code in the repository.** The little-workshop repository shall keep every policy's script in tools/policy_scripts/ and its EARS text, target pillar, scope and gate points in tools/policy_scripts/policies.json, and tools/sync_policies.py shall report how the live policies differ from them and, with --apply, replace each differing policy in place and create each missing one. | draft |
| REQ-000000307 | **Commit-level policies judge the pending commit.** Superseded: commit-bumps-version and branch-commit-dev-suffix are dropped (REQ-000000492). | superseded by REQ-000000492 |
| REQ-000000309 | **Projects are checked for the commit hook and the Claude configuration.** Superseded: the commit hook runs the local secret scan (REQ-000000493). | superseded by REQ-000000493 |

## Policies as the hard layer of Claude instructions (2026-10-02)

Proposed, not yet registered in the Requirements pillar (ids pending; `—` until they exist). Amends
REQ-000000307 (the commit-level scripts now also include unity-meta-files-paired and
lfs-tracked-files-are-pointers) and REQ-000000309 (claude-config-present now accepts a root AGENTS.md
and also requires the policy-preflight skill and the absence of .claude/CLAUDE.md and CLAUDE.local.md; in a
second pass the same day, also the briefing's import of .claude/little-workshop.md, the generated rule and
workshop-lifecycle skill, the guard registered for every tool it judges, and no CLAUDE.md or CLAUDE.local.md
anywhere in the project). The claude-instructions-valid row below also covers, since that second pass,
imports outside the project, rule fields other than paths, skill and subagent descriptions, and a limit of
250 lines loaded at session start.

| ID | Description | Status |
| --- | --- | --- |
| — | **Claude instructions are checked to load as written.** When a branch of a selected project is finished, the Policy & Compliance pillar shall check that every @path import in its AGENTS.md and .claude/rules/ files resolves to a file, that every path-scoped rule's frontmatter parses and each of its patterns matches a file in the project, that every script a skill names and every hook script .claude/settings.json runs exists, and that no instruction file loads more than 200 lines (claude-instructions-valid). | draft |
| — | **A Unity asset and its .meta are committed together.** When a commit of a selected Unity project adds or deletes a file or folder under Assets/, the Policy & Compliance pillar shall fail the branch finish's check unless the commit adds or deletes that file's or folder's .meta with it and adds or deletes no .meta apart from its asset (unity-meta-files-paired). | draft |
| — | **Files routed to Git LFS are committed as pointers.** When a commit of a selected project adds or changes a file that its .gitattributes routes to Git LFS, the Policy & Compliance pillar shall fail the branch finish's check unless the commit stores the file as a Git LFS pointer (lfs-tracked-files-are-pointers). | draft |
| — | **The requirement-citation policy covers a Unity project's source.** Retired: `commit-cites-requirement` was dropped; a full-governance branch's requirement ids are listed in its squash commit (REQ-000000499). | retired |
| — | **ethereal-lands' generated files match their generators.** When a branch of ethereal-lands is finished, the Policy & Compliance pillar shall check that `tools/update_design_index.py --check` and `tools/elm-schema.py --check` both pass in the checkout (design-index-and-schema-current). | draft |
| — | **A commit introduces no secret.** When a commit of a selected project adds or changes a file, the Policy & Compliance pillar shall fail the branch finish's check (and the commit-msg hook its local scan, REQ-000000490) if the commit introduces a private key, a provider token or API key in a recognisable format, or an environment file other than a template, ignoring matches the file's previous version already held and lines marked pragma: allowlist secret (no-committed-secrets). | draft |
| — | **A recorded verdict keeps its reasons under coloured output.** When a policy script's pytest output carries terminal colour codes, the Policy & Compliance sandbox and the policy-preflight fallback shall still record the assertion messages and skip reasons as the check's detail; both shall run pytest with --color=no. | draft |

## Reading only what is shown: attention and cleanup (2026-10-05)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000390 | **The latest policy results are read, not their history.** The Policy & Compliance pillar shall list, when asked, only the latest check result of each policy and project (optionally only the failing ones) and policies without their test scripts, so that the Dashboard's attention banner and policy pages read only the results and policies they show. | draft |

## Temporary policy bypass for the user (2026-10-05)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000393 | **The user can raise a temporary policy bypass from the CLI.** WHEN the user runs `sudo lws bypass_policies true [--time minutes]` in a terminal, the Policy & Compliance pillar shall set the bypass_policies flag TRUE for that many minutes (default 2, refused above 30 or below 1) and set it back to FALSE when the time elapses; the command shall fail without the super user and when invoked by Claude, and the pillar shall refuse any origin but the user's terminal CLI; `lws bypass_policies false` shall lower the flag at once. | draft |
| REQ-000000396 | **Turning the policy bypass on and off is audited.** The Policy & Compliance pillar shall log every turning on, turning off and lapse of the bypass, and each violation it let through, with the time, the actor and the origin. | draft |

## Pillar crashes and the work they cut (2026-10-05)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000405 | **A policy check cut by a restart runs again.** If the Policy & Compliance pillar stops while a policy check is running, then the pillar shall remove the check's sandbox container as it stops and record no verdict for the cut check, and when it next starts it shall run the check again from the start and complete its Job as usual. | draft |

## Claude configuration: the newest request outranks earlier decisions (2026-10-24)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000449 | **No test cites a retired requirement.** Where the project keeps a `docs/requirements.md` register, the policy `requirement-marker-not-superseded` shall refuse a commit that leaves a test citing a requirement whose row the commit marks superseded or removed, in `docs/requirements.md` or in any `docs/requirements/*.md` part of a split register, or that changes a test file citing any such requirement. | done |
| REQ-000000451 | **Checks of form warn; checks of behaviour and safety refuse.** The policy `claude-instructions-valid` shall report an instruction file over 200 lines or a session start over 250 lines as a warning, never a failure, and the policy `commit-cites-requirement` shall gate only the branch finish, never a single commit. | done |

## Versioning without churn (2026-11-07)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000490 | **The commit-msg hook refuses a commit that adds a secret, locally.** When a commit is made in a checkout whose .githooks/commit-msg little-workshop installed, the hook shall scan the commit's staged changes for secrets in its own process (`lws policy scan-secrets`, the no-committed-secrets detection, with no daemon and no Docker) and refuse the commit when it adds one, printing each finding redacted and the fix; if lws is missing or predates the scan, the hook shall warn and let the commit through. | draft |
| REQ-000000492 | **Commit-level policies judge the pending commit.** Each commit-level policy script shall judge the pending commit that POLICY_COMMIT_DIR describes against the checkout's HEAD when that variable is set, and HEAD against its parent when it is not; the commit-level scripts are version-format and commit-cites-requirement. | draft |
| REQ-000000493 | **Projects are checked for the commit hook and the Claude configuration.** When a branch of a selected project is finished, the Policy & Compliance pillar shall check that its .githooks/commit-msg runs the local secret scan (`lws policy scan-secrets`) with core.hooksPath set to .githooks (commit-policy-hook-installed), and that it carries .claude/AGENTS.md and a .claude/settings.json registering the scope guard but no CLAUDE.md (claude-config-present). | draft |

## Requirements and policies only when necessary (2026-11-07)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000496 | **Requirement work is asked only of full-governance projects.** While a project's governance is `light`, the system shall skip, as not applicable, every policy that requires `full` governance at each gate, preflight and check, and shall leave the requirement instructions out of the project's generated Claude files (shared briefing, workshop-lifecycle skill, scaffolded AGENTS.md) and of the prompts of its agent tasks; while it is `full`, the system shall run those policies and include those instructions. | draft |
