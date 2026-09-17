# little-workshop

The unified, self-hosted system governing the full software lifecycle —
requirements through deployment and monitoring — for every project in this
ecosystem, sized for one PC and up to two developers. Absorbs
`little-requirements`, `little-versions`, `little-runner`, and
`bootstrap-little-project` as internal modules of one daemon, rather than
replacing them with separate third-party peers.

See `docs/design.md` for the purpose, goals, and core concepts, and
`docs/architecture.md` for the pillar breakdown, data model, and feature
mapping from the tools it absorbs. Implementation is underway against the
17-milestone sequence in `docs/milestones.md`. Done so far:

- **M1 — Core + Orchestration**, the substrate every pillar depends on:
  `src/common`, `src/core`, `src/orchestration`, and a minimal `lws` CLI
  (`src/cli`), proven end-to-end against a real `systemd --user` session.
- **M2 — Registry & Portfolio (bootstrap path)**: `src/registry` —
  `project.bootstrap` (scaffold, `git init`, `dev` branch, optional
  GitHub repo/push), `project.remove` (cascading hard delete),
  `project_sets`, `tickets`, and `lws project bootstrap/list/remove`.
- **M3 — Version Control & Release**: `src/version` — Gitflow as a
  pillar, absorbing `little-versions` wholesale: `feature`/`improvement`/
  `hotfix`/`release`/`support` start/finish, release-candidate cut/list/
  promote, version hooks, and `.lvx/config.json` kept byte-compatible
  with the tool it replaces. No database of its own — git remains the
  store. The first pillar to call *another* pillar (Registry &
  Portfolio) over a real Pillar Interface, discovered through Core.
- **M4 — Deployment & Servers**: `src/deployment` — Managed Process
  lifecycle for *other* projects' servers, absorbing `little-runner`'s
  server-management mechanics: worktree-based build-before-stop
  deploys, defence-in-depth stop, Tailscale exposure, confirm-gated
  deploys with an independent watchdog timer, and worktree provisioning
  exposed as a shared capability for later pillars (M10, M12) to call
  into.
- **M5 — Self-redeploy (reflexive)**: `deployment.redeploy_pillar`
  generalizes M4's build-before-stop sequence to little-workshop's own
  pillars, including Core and Deployment & Servers itself — a real
  `/proc/self/cgroup` check detects a pillar redeploying *itself* and
  dispatches to an independent, detached `systemd-run` unit rather than
  stopping the very process handling the request, the same cgroup-aware
  escape `little-runner` already proved for its own self-restart.
  Confirm-gating defaults on for `core`/`deployment`; the dispatched
  worker reports completion back to Orchestration.
- **M6 — Agentic Development**: `src/agent` — Claude CLI as a
  first-class execution target, absorbing `little-runner`'s
  `issue_flow.py`/`branching.py`/`report.py`/`screenshots.py`: a
  `BranchingSpec` validated against Version Control & Release's own
  branch-type policy over a real Pillar Interface call, the
  `AGENT_STATUS: done|partial|failed` completion marker, the
  seven-section change report, two review modes (manual, auto-approve),
  and the GitHub adapter's two proven techniques (actor lookup via the
  issue timeline, cursor-based edge detection from polling). Runs for up
  to an hour, so its Job dispatches to a background thread and reports
  back asynchronously — the reason that path existed in the Job model
  since M1.
- **M7 — MCP Server (minimal)**: `src/mcp_server` — a hand-rolled,
  dependency-free JSON-RPC 2.0/MCP stdio server (`initialize`,
  `tools/list`, `tools/call`), the same proven shape `little_versions.mcp`
  already uses. Every mutating Job type and read endpoint built so far
  for Registry & Portfolio, Version Control & Release, Deployment &
  Servers, and Agentic Development is exposed as a `workshop_<pillar>_*`
  tool, each translating straight into a Pillar Interface call — no
  domain data of its own, the same thin-proxy role Dashboard will
  eventually play. Registered at user scope via `claude mcp add` inside
  `bazel run //:install`, per the root MCP policy. Never Core/systemd-
  launched — spawned on demand by whatever MCP client attaches it
  (`lws mcp serve`).
- **M8 — Registry & Portfolio (import path)**: `project.import` —
  the other half of M2, for an existing, already-checked-out repository.
  Deliberately mechanical, the same as `project.bootstrap`'s own
  registration step: it validates only that there's something real to
  register (a real git repository), never whether it conforms to
  policy — that judgment belongs to whoever drives the import (a human
  Claude Code session, or Agentic Development's own dispatch, the
  identical `workshop_project_import` MCP entry point either way), and
  later to Policy & Compliance (M12). No new Agentic Development
  machinery needed: once registered, a normal `workshop_agent_run_task`
  (M6/M7) is how any further conformance work actually gets done.
- **M9 — Orchestration (scheduled tasks)**: a `ScheduledTask` that
  produces an ordinary Job on its own timing (`once` or weekday-
  `recurring`), with `add`/`edit`/`remove`/`trigger`/`cancel`/`stop`/
  `postpone`/`queue` exposed identically on the Pillar Interface, `lws
  schedule ...`/`lws job stop`, and matching `workshop_schedule_*`/
  `workshop_job_stop` MCP tools. `stop` is declared-with-probing-
  fallback; missed firings are detected only at process startup and
  collapsed to the most recent occurrence; the routine tick runs on a
  10-minute in-process thread. `queue_key` gained a configurable
  concurrency capacity (default 1, unchanged from every prior caller's
  mutex behavior) instead of a second concurrency mechanism.
- **M10 — Requirements & Quality**: `little-requirements`' full model
  (Project→Suite→Requirement→TestResult, draft/published) absorbed as
  one pillar-owned, `project_id`-scoped database, plus its own
  never-built M8 (a Use-Case/Milestone axis independent of test suites)
  and E6 (JUnit-XML ingestion beyond pytest, and sandboxed on-demand
  test execution via Deployment & Servers' worktree interface) actually
  built. Eleven operations exposed identically on the Pillar Interface,
  `lws requirements ...`, and matching `workshop_requirements_*` MCP
  tools. A definition-of-done report (`GET /v1/report`) marks a
  requirement done only when published, linked to a test suite, and
  that suite's latest run passed — Policy & Compliance's (M12) own
  half of the same concept is the *conflict-with-a-policy* side, still
  to come. Periodic test runs needed no mechanism of their own: just an
  Orchestration `ScheduledTask` (M9) naming `requirements.run_tests`.
- **M11 — Build & Packages**: `little-runner`'s disclaimed "not a
  general CI system" non-goal lifted — `build.run` runs a project's own
  arbitrary build/test command inside a Deployment & Servers-provisioned
  worktree, never the project's own working checkout, recording a
  `BuildRun`. Fork-safety-gated PR testing: a `from_fork`-flagged run
  can still execute, but `build.publish_pypi`/`build.publish_vsix`
  check the run's *stored* record (never a caller-supplied claim) and
  refuse to publish anything built from one. `little-pypi` absorbed for
  package hosting — a real PEP 503 Simple Repository API computed live
  from this pillar's own database (metadata in the DB, blobs on disk,
  a deliberate modernization of `little-pypi`'s pure-filesystem model)
  plus its own `.vsix` namespace, deliberately outside the PEP 503
  index. Seven operations exposed identically on the Pillar Interface,
  `lws build ...`, and matching `workshop_build_*` MCP tools. Periodic
  builds needed no mechanism of their own either: just a `ScheduledTask`
  naming `build.run`.
- **M12 — Policy & Compliance**: three rule tiers (hard-coded invariants
  stay code, never policy; framework policies always win, never user-
  overridable; user policies scoped to a project/project-set/all-
  projects), scope precedence resolved per topic, and topic ownership
  (one `target_pillar` per topic, mechanically enforced). The four-gate
  authoring pipeline — EARS-compliance, mechanical-enforceability,
  non-conflict, then full human approval — as two Job types
  (`policy.author` drafts and never saves; `policy.activate` is the only
  path that ever writes a policy) calling a new, narrowly-scoped
  `agent.query` Job type on Agentic Development (text-in/text-out, no
  branch, no side effect — deliberately not a reuse of `agent.run_task`,
  whose flow always prepares a real branch). Every check runs inside a
  real Docker sandbox: a purpose-built image, read-only project-bound or
  framework-bound mounts, and a 30-minute ceiling that reliably kills an
  orphaned container. Action-gating extends the Job model with
  `pending_confirmation`: a gated Job type (`deployment.deploy` on
  `deployment-policy`, this pass's own concrete proof) doesn't dispatch
  until its governing policy passes, fails closed with a real 10-minute
  `systemd-run` watchdog (mirroring Deployment & Servers' own
  confirm-gate pattern) on a `fail` verdict, and a script that never
  completes is a framework fault, never routed through that window.
  Two documented simplifications: the sandbox's network is `--network
  none` rather than a Pillar-Interface-only proxy, and gated Job-type
  declarations are a small, real start, not `OQ-3`'s full mapping.
- **M13 — Populate the policy catalog (in progress)**: `lws policy
  catalog list`/`run` and matching `workshop_policy_catalog_*` MCP tools
  drive one `docs/pillars/policy_catalog.md` entry at a time through
  the identical `policy.author`/`policy.activate` pipeline any other
  policy uses — never a bulk import, since Gate 4's full human approval
  has no shortcut. One entry (`version-file-single-source`) carried all
  the way to a real, activated policy as a working proof, checked for
  real via the real Docker sandbox against a real bootstrapped project.
  That live run against the real Claude CLI (not the deterministic
  fake-stub tests) also caught and fixed a real defect: the
  script-generation prompt never stated the sandbox's actual mount
  paths, so a generated script would have silently found nothing to
  check even for a compliant project. The other 58 catalog entries stay
  queued as real future work through this same tool.

### Policy usability refinement (2026-09-16)

Requested directly, after M12/M13: individual projects may need
different policies, or may genuinely be unable to fulfill one — users
need real control without weakening the framework tier's core
guarantee into a casual toggle. Every policy gains a real `enabled`
toggle (`policy.configure`, no re-authoring — neither field is about
the requirement's own meaning) and **per-project waivers**
(`policy.waiver_grant`/`revoke`, a required `reason`, a new
`policy_waivers` table) — an explicit, auditable, reversible exception,
deliberately distinct from scope-shadowing: a framework policy still
can never be silently overridden by a lower-scope policy. Gating
behavior itself moved from a fixed Job-type-to-topic table to a
per-policy, user-chosen set of gate points (`build_install`,
`server_launch`, `deploy_redeploy`, `release`, `branch_finish`,
`project_removal`, plus `self_redeploy`, `server_stop`,
`package_publish`, and `project_registration` added the same day once
real Job-type coverage showed the original six conflated meaningfully
different blast radii) — a Job can now be gated by more than one policy
at once, resolved through a new `GET /v1/policies/gating-for-job`
endpoint. Proven end-to-end for real: a `gate_points`-configured policy
gates a real deploy exactly as the original single-topic mechanism did;
disabling it lets a previously-blocked deploy through; and a waiver
lets one project's deploy through while a second, non-waived project on
the identical policy still genuinely blocks.

### Answering OQ-13/14/15 (2026-09-17)

`OQ-13` (waiver review) stays exactly as built — no code change; the
single-user household trust model already covers it. `OQ-14` (a real
Claude-CLI-backed `policy.author`/`policy.activate` call occasionally
timing out at the fixed 20s dispatch ceiling) gets a per-Job-type
timeout table (`orchestration/dispatch.py`'s `JOB_TYPE_TIMEOUTS`),
consulted before every dispatch, leaving every other Job type's own
20s default untouched. `OQ-15` (`project_registration` can't be checked
against a project that doesn't exist yet) resolves in two parts:
gate-time validation now rejects a project-bound policy that tries to
gate `project_registration` outright, and `orchestration/policy_gate.py`
gained `verify_after_completion` — the category is verified *after* a
real `project.bootstrap`/`project.import` succeeds, fire-and-forget,
never gated before it.

### M16 — full functional parity with the absorbed tools (in progress)

Rejected compatibility shims outright, by direct decision: the five old
tools (`lreq`, `lvx`, `lrun`, `little-pypi`, `bootstrap-little-project`)
keep running their own original code for the whole transition, and
never call into `lws` or vice versa — see `docs/milestones.md` M16/M17
and `docs/architecture.md`'s CLI section for the corrected design. A
real, command-by-command survey of all ~90 subcommands across the five
tools found `little-versions` closest to full parity; its one gap
(standalone issue↔branch linking, previously only settable once at a
topic branch's own start time) is now closed: `version.issue_set`/
`issue_unset` and `GET /v1/issues`, reusing `branches.py`'s own
`issue_automation` flag as the single source of truth for which branch
types are linkable. The other four tools' gaps (see the published
parity ledger referenced from `docs/requirements.md`'s M16 section)
remain open.

`bazel build //:wheel`, `bazel run //:install`, `bazel test //:test`, and
`bazel test //:coverage` all work today (1679 tests — 1540 unit, 139 real
`systemd --user` end-to-end integration proofs (a deliberate +15%
expansion over the prior 119, 2026-09-17: real end-to-end proofs of the
three of four newly-added gate-point categories that don't hit a
project-registration chicken-and-egg problem (`OQ-15`), scope-precedence
resolved live against real Registry project sets, waiver/precedence
interaction, and a first MCP-level policy proof) — at 99% line coverage
over `src/`, measured with `python3 -m pytest --cov=src`; see
`scripts/bazel/run_pytest_coverage.sh` for a known Bazel-specific
limitation in that target's own reported percentage). Integration
coverage includes a genuine self-redeploy that stops and relaunches
Deployment & Servers' own unit from a freshly checked-out commit of this
repo, every agent review/branching-mode combination with only the agent
session itself stubbed, real Gitflow conflict/continue/abort cycles, a
cross-pillar cascading-removal check, and real MCP `tools/call` round
trips against live pillars. Three integration tests skip cleanly — they
don't fail the suite — when `lws` isn't pipx-installed yet (`bazel run
//:install`).

### Testing-fortification pass (2026-09-16)

Beyond the milestone work above, a dedicated pass hardened test coverage
across ten priority areas (ranked by complexity, security sensitivity,
and prior coverage gaps — Version Control & Release's gitflow engine and
Agentic Development's GitHub/Claude-subprocess surface ranked highest;
Core supervision and process entrypoints lowest) and added 65 real
integration tests. It found and fixed several genuine bugs along the way
— a Registry validation error crashing instead of failing cleanly, a
`version.feature_finish --abort` path leaving `.lvx/state.json` dirty,
Orchestration's dispatch timeout being too tight for real subprocess-
backed jobs under load — the same "real end-to-end tests are the actual
arbiter of correctness" discipline every milestone above already
followed.

A follow-up pass raised integration coverage further, from 72 to 101 real
`systemd --user`/real-socket end-to-end tests: the remaining M9
scheduled-task operations (cancel/postpone/remove/edit/list/get, and
`stop` against a non-running Job), `queue_key` capacity edge cases, real
`lws schedule`/`lws job stop` and MCP `workshop_schedule_*`/
`workshop_job_stop` round trips, a full Improvement gitflow cycle and
multi-RC release-candidate listing, `deployment.stop`/status/confirmation
edge cases, and further Core supervision scenarios (a disabled pillar
never launched, a healthy reconcile loop staying a clean no-op). Along
the way it also locked in one more confirmed, undocumented gap —
Registry's `project_sets.name` `PRIMARY KEY` has no app-layer duplicate
check, so a second `project_sets` create with the same name raises a raw
500 rather than failing cleanly.

For the narrative version of the same decisions — cross-linked
presentations — start at `presentations/README.md`.

**Independent by design.** Unlike every other submodule in this ecosystem,
little-workshop is exempt from the super-repo's root policies — it is not
wired into `little-requirements`/`lreq` or `little-versions`/`lvx`. See
`docs/governance_policy.md` for why and `docs/README.md`'s "Exemption:
little-workshop" for where the super-repo itself sanctions it.

**What's still unresolved**: `docs/open_questions.md` is the single
tracker for every open design question and gap — check there before
assuming something is decided.
