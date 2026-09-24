← [What the First Pass Missed](21-what-the-first-pass-missed.md) · deck 22 of 22 · back to [index](README.md)

# Deterministic Automation: Spending Claude Only on Judgment

This is a proposal only. Nothing in this deck is built yet. It lists
places where project management could run as plain code, either scheduled
or exposed as MCP tools, instead of a Claude session doing the same
mechanical work by hand. It ends with the three suggestions expected to
save the most tokens.

---

## What "deterministic automation" means here

- **Code, not a model.** A deterministic job gives the same output for
  the same input. It runs as an Orchestration Job, a `ScheduledTask`
  (M9) or a Dashboard catalog entry (`src/dashboard/job_catalog.py`).
  **No step inside it calls Claude**: no `agent.query` and no
  `agent.run_task`.
- **MCP entry points are part of it.** Each capability is also a
  `workshop_<pillar>_<operation>` tool, so a Claude session can *call*
  the code instead of *redoing* its work with its own reads, greps and
  shell runs. Claude triggers the tool, and the tool itself stays
  deterministic.
- **The same line the Agentic Development spec already draws.**
  [`agentic_development.md`](../docs/pillars/agentic_development.md#not-every-job-goes-through-this-pillar)
  already sends "a typed, deterministic action with one correct outcome"
  straight to the pillar that owns it. This deck applies that rule to
  the *inside* of an agent session as well.

---

## Where the tokens go today

A typical `agent.run_task` session spends tokens in three ways:

1. **Loading context.** The `milestone` intent prompt tells Claude to
   "read `docs/design.md`, `docs/architecture.md` and the milestone row".
   Those three files come to ≈ 98 KB (≈ 25k tokens) before any code is
   read. `docs/requirements.md` alone is 143 KB (≈ 36k tokens). All of
   `docs/` is ≈ 418 KB. Whatever is read stays in the context for every
   later turn, so it is paid again (as cache reads) on each one.
2. **Noisy tool output.** Raw `bazel test` / `pytest` logs, `git log`,
   `grep -r` results and server start-up output. Each is read in full, and
   read again every time the command is re-run.
3. **Sessions that should never have started.** Duplicate incidents, work
   that is purely mechanical (version bumps, lock-file refreshes) and runs
   against a broken base branch. Each of these costs a whole session, not
   just part of one.

The suggestions below each target one or more of these.

---

## The suggestions at a glance

| # | Suggestion | Targets | Trigger | New MCP tool (proposed) |
|---|---|---|---|---|
| 1 | Project context pack | Context | on commit + on demand | `workshop_project_context` |
| 2 | Test run with failure digest | Output | on demand | `workshop_requirements_test_digest` |
| 3 | Agent pre-flight gate & incident dedupe | Whole sessions | before every `agent.run_task` | `workshop_agent_preflight` |
| 4 | Definition-of-done / traceability check | Context + output | on demand, nightly | `workshop_requirements_dod_check` |
| 5 | Documentation consistency lint | Context | pre-commit + nightly | `workshop_docs_lint` |
| 6 | Pre-filled change report | Output | end of every run | *(internal to `agent.run_task`)* |
| 7 | Branch preview launcher & standard screenshots | Output | on demand | `workshop_deployment_preview_start/stop` |
| 8 | Mechanical-change catalog jobs | Whole sessions | catalog / schedule | `workshop_job_catalog_run` |
| 9 | Scheduled release & branch housekeeping | Whole sessions | recurring schedule | existing `workshop_version_*` |
| 10 | Code & ownership index | Context | on commit | `workshop_code_lookup` |
| 11 | Morning health digest | Whole sessions | nightly schedule | `workshop_core_digest` |
| 12 | Token accounting per run | Measurement | end of every run | `workshop_agent_usage` |

---

## 1 — Project context pack

**Today:** each session starts by reading several of the large docs and
exploring the tree to find the milestone row, the pillar it touches, the
requirements already written for it and the test layout.

**Deterministic version:** a generator that builds a compact,
*addressable* summary of a project from the files and pillar databases:

- the milestone overview table and the matching row, open questions
  still marked Open, and a table of contents of every doc with
  `file:line` anchors (headings only, no body text);
- the requirements for the given milestone or pillar only (id, EARS
  text, state, linked suite, last verdict), taken from Requirements &
  Quality rather than from the 143 KB markdown file;
- `VERSION`, the current branch and its base, the last N commits, the
  test targets and how to run them, and the project's
  `manifest.deployment.toml` summary.

It is rebuilt by the existing version hook on every working-branch commit
(`tools/on_version_bump.py` already runs there). It is served by
`workshop_project_context(project, milestone?, pillar?)` and inlined into
the `agent.run_task` prompt in place of "read design.md and
architecture.md". Claude still opens a full document when a section
needs it, and the anchors show it where to go.

---

## 2 — Test run with failure digest

**Today:** Claude runs the project's test command itself and reads the
entire output. A failing run is often hundreds of lines of tracebacks,
Bazel progress lines and captured logs, and the session re-runs the
suite several times on the way to green.

**Deterministic version:** Requirements & Quality already runs suites
(`requirements.run_tests`) and already parses JUnit XML
(`src/requirements/junit.py`). Add a *digest* shape to the result:

- totals (passed / failed / errored / skipped) and the duration;
- one entry per failing test: id, the `REQ-…` it is tagged with, the
  assertion line and the last few frames, with a hard cap on bytes;
- a diff against the **baseline** (the same suite on the base branch),
  so a failure that already existed is labelled as such and not chased.

Exposed as `workshop_requirements_test_digest(project, commitish,
suite_ids?, only_failed_from?)`. The last argument re-runs only what
failed last time, which is the loop a session actually goes through.
The full log stays on disk, and its path is returned for the rare case
that needs it.

---

## 3 — Agent pre-flight gate & incident dedupe

**Today:** `agent.run_task` starts a Claude session as soon as the Job
dispatches. If the base branch does not build, the Playwright browser is
missing, or the task is the fifth copy of the same incident, Claude finds
that out itself, at full session cost, and often ends `partial` or
`failed`.

**Deterministic version:** checks that run before `claude -p` is
invoked:

- **Environment:** clean checkout, base branch builds, baseline test
  run recorded (feeds #2's baseline diff), required tools present
  (`bazel`, `gh`, Playwright browser), no other run for the project.
- **Incident dedupe:** incidents from `common.incident` and the vendored
  `tools/incident_reporter.py` get a signature (exception type plus
  normalized top frames plus route). A repeat signature raises a counter
  on the existing incident and does not create a new task. A signature
  whose fix is already on an open branch is linked to that branch.
- **Mechanical deflection:** a task whose intent matches a catalog job
  (#8) is offered as that job instead.

A failed check stops the run with a typed reason in the Dashboard, and no
tokens are spent. Also exposed as `workshop_agent_preflight(project)` so
an interactive session can ask "is this project in a workable state?"
without finding out itself.

---

## 4 — Definition-of-done / traceability check

**Today:** at the end of milestone work Claude checks the definition of
done itself. It greps for `REQ-` tags in tests, cross-reads
`docs/requirements.md` against `docs/milestones.md`, and checks that
commits under `src/` cite an id, as the requirements policy asks.

**Deterministic version:** one report per branch:

- requirements cited in the branch's commits, and whether each is
  published, linked to a suite, and passing in its latest run (the
  Requirements pillar's existing DoD rule);
- commits touching `src/` without a `REQ-` citation (the mechanical
  half of the `commit-cites-requirement` draft policy);
- the milestone row's status compared with what the requirements say.

It runs nightly for every registered project and on demand as
`workshop_requirements_dod_check(project, branch)`. The output is a
short list of gaps, not the evidence itself.

---

## 5 — Documentation consistency lint

**Today:** consistency between docs is checked by reading them, by the
owner in review or by Claude re-reading several files. Deck 21 exists
because this kind of drift went unnoticed.

**Deterministic version:** a linter covering the facts that can be
checked mechanically:

- the `docs/milestones.md` overview table agrees with its `## Mn`
  sections and statuses, and the `docs/pillars/README.md` status table
  agrees with the milestones;
- `presentations/README.md` lists every deck, and the "deck N of M" and
  prev/next navigation lines are correct;
- relative links and `#anchors` resolve, every `OQ-n` and `REQ-n`
  referenced exists, and IDs are never reused.

It runs as a pre-commit hook plus a nightly `ScheduledTask`, and as
`workshop_docs_lint(project)`. Claude never has to re-read five files to
confirm that a number was updated in all of them.

---

## 6 — Pre-filled change report

**Today:** Claude writes all five JSON fields of the change report from
memory at the end of a long session.

**Deterministic version:** `agent.run_task` already renders the report
after the session. Code fills in the factual sections: *Executed tasks*
gets the commit list and diffstat grouped by directory, and *Test
results* gets the recorded digest from #2 (command, counts, failures).
Claude is asked only for *background*, *goal* and *technical decisions*,
which are the parts that need judgment. Where the two disagree, code
wins, which also closes a gap: a session that claims its tests pass
while the recorded run failed.

---

## 7 — Branch preview launcher & standard screenshots

**Today:** the UI prompt (`ui_playwright_mcp.md`) asks Claude to find
`[server.screenshot].command`, pick a free port, start the app, wait for
it, install the browser if it is missing, resize to 412×915 and then to
desktop, and stop everything afterwards. Every step produces output that
Claude reads.

**Deterministic version:** `workshop_deployment_preview_start(project,
branch)` returns `{url, pid}` once a health probe passes, and
`workshop_deployment_preview_stop` tears it down. An optional
`screenshots` argument captures a fixed list of routes at phone and
desktop sizes and returns the file paths. Claude only looks at the
pages whose behaviour it actually changed. The screenshot capture
little-workshop already does after a run (`src/agent/screenshots.py`)
is reused rather than written a second time.

---

## 8 — Mechanical-change catalog jobs

**Today:** some changes that have exactly one correct result still reach
Claude as tasks. The repository history shows version bumps
(`chore: bump version to 0.2.2`), lock-file refreshes, formatting passes
and "add a test suite for these REQs" wiring.

**Deterministic version:** new `JobSpec` entries in
`src/dashboard/job_catalog.py`, each committing on its own
`improvement/` branch through Version Control & Release:

- apply formatting / `ruff --fix` and commit;
- refresh `MODULE.bazel.lock` / dependency pins and run the tests;
- bump or sync `VERSION` (`tools/sync_versions.py` already exists);
- link a suite to its REQ tags (`requirements.link_suite` exists).

They can be scheduled as well (for example, a weekly dependency refresh).
The existing memory rule still holds: the catalog is a fixed menu with
typed arguments and is never drafted by Claude.

---

## 9 — Scheduled release & branch housekeeping

**Today:** release-cut, back-merge, branch audit and stabilize are all
Jobs already (`workshop_version_*`), but someone has to *decide* to run
them, and that decision often happens inside a Claude session.

**Deterministic version:** recurring `ScheduledTask`s with fixed rules:

- nightly `version.branch_audit` for every project, flagging merged
  branches, stale branches and branches that are behind;
- `version.stabilize` after each merge to `dev`;
- a weekly *proposal* to cut a release candidate when `dev` is ahead of
  `master` and the last test run was green. The owner confirms it, and
  nothing is released automatically.

---

## 10 — Code & ownership index

**Today:** Claude answers "where is Job type X handled?", "which pillar
owns this route?" and "which MCP tool maps to which endpoint?" with
repeated `grep -r` over `src/`, reading many irrelevant matches.

**Deterministic version:** an AST-based index rebuilt on commit, holding
the Job types registered per pillar (the `@app.post("/v1/jobs/…")`
routes), MCP `ToolSpec` names and the calls they make, CLI subcommands,
the tests that cover each module, and the requirement tags per test.
Served as `workshop_code_lookup(project, symbol | job_type | route)`,
which returns `file:line` locations only.

---

## 11 — Morning health digest

**Today:** "what's the status of everything?" is a question asked of
Claude, which then calls a dozen read tools and summarises them.

**Deterministic version:** a nightly `ScheduledTask` that writes one
structured digest per project covering failed and missed scheduled
tasks, open incidents grouped by signature (#3), policy verdicts
(`policy.check_all`), requirements without a passing test, stale
branches (#9), runs awaiting finish, and server status. The Dashboard
shows it and `workshop_core_digest` returns it. Claude reads one small,
already-aggregated object, or nobody asks at all because the Dashboard
already shows it.

---

## 12 — Token accounting per run

**Today:** nothing records how many tokens a run used, so no one can
measure whether any suggestion here works.

**Deterministic version:** `claude -p --output-format json` already
returns a `usage` block, and `agent/claude_invoke.py` already parses
that output. Store input, output and cache-read/cache-write tokens and
the turn count on each `agent_runs` row, and aggregate them by project,
intent and week in the Dashboard and in `workshop_agent_usage`. This is
the baseline the top 3 below should be measured against **before** any
of them is built.

---

## The top 3 for token savings

Ranked by *tokens saved per run × how many runs it applies to*:

1. **#1 Project context pack.** It applies to **every** session. Replacing
   ≈ 25–60k tokens of whole-document reads with a ≈ 3–5k-token targeted
   summary also shrinks every later turn, because the context those reads
   left behind is gone. This is the largest saving and it compounds.
2. **#2 Test run with failure digest.** It applies to every session that
   runs tests, which is nearly all implementation work, and several times
   per session. Capped, baseline-aware digests replace full logs and stop
   Claude chasing failures that already existed.
3. **#3 Agent pre-flight gate & incident dedupe.** A prevented session
   saves **100 %** of that session's tokens. Duplicate incidents and runs
   on a broken base branch are cheap to detect in code and expensive to
   find out inside a session.

Runner-up: **#4 DoD check**, which is large for milestone runs but only
applies to those.

*The figures above are estimates from document sizes (≈ 4 bytes per
token). #12 should be built first, or alongside the top 3, so each one's
real effect is measured rather than assumed.*

---

## Suggested order, if this goes ahead

1. **#12 token accounting.** Cheap, and without it nothing else can be
   evaluated.
2. **#3 pre-flight and dedupe.** Small code, and it prevents whole
   sessions.
3. **#2 test digest.** Builds on Requirements & Quality's existing JUnit
   parsing.
4. **#1 context pack.** The largest payoff, but it needs a decision on
   how much of the prompt it replaces, and it must be checked against
   #12's numbers so it does not remove context that sessions actually
   need.
5. The rest follow as ordinary milestones, each with its own
   requirements, per `docs/milestones.md`'s definition of done.

---

Back to [index](README.md) · or revisit [21 — What the First Pass Missed](21-what-the-first-pass-missed.md)
