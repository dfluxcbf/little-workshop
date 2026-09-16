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

`bazel build //:wheel`, `bazel run //:install`, `bazel test //:test`, and
`bazel test //:coverage` all work today (1086 tests — 1014 unit, 72 real
`systemd --user` end-to-end integration proofs — at 99% line coverage
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
