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
  exposed as a shared capability for later pillars (M9, M11) to call
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

`bazel build //:wheel`, `bazel run //:install`, and `bazel test //:test`
all work today (316 tests: unit plus real `systemd --user` end-to-end
proofs in `tests/integration`, including a genuine self-redeploy that
stops and relaunches Deployment & Servers' own unit from a freshly
checked-out commit of this repo, and a full agent-task run with only
the agent session itself stubbed). The self-redeploy integration test
skips cleanly — it doesn't fail the suite — when `lws` isn't
pipx-installed yet (`bazel run //:install`).

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
