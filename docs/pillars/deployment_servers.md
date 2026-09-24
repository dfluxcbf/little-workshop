# Deployment & Servers

## Description

Deployment & Servers owns the lifecycle of every "Managed Process" in
this ecosystem — a project's own server, and, reflexively, little-workshop's
own pillars. It absorbs `little-runner`'s server-lifecycle mechanics:
worktree-based build-before-stop deploys, defence-in-depth stop,
Tailscale exposure, confirm-gated rollback, and — since M5 — the
cgroup-aware self-restart that lets it (and Core) redeploy themselves
without the redeploy killing the very process handling the request.

## Purpose & goal

"A running process — a project's server, a Claude CLI instance, or a
pillar of this system itself — stays running, and can be safely replaced
with a newer version without anything else noticing" (`docs/architecture.md`).
The core guarantee: a bad build or a bad install must never take a
healthy, currently-running process down.

## Responsibilities

- **`deployment.set_manifest`** — the one write path for a project's own
  `manifest.deployment.toml` (`GET /v1/manifest/{project_id}` reads it
  back), added 2026-09-17 as an M17 ecosystem-cutover finding: nothing
  exposed a way to configure a real project's server manifest at all
  before this — `deploy`/`stop`/`status` could only ever fail with "no
  manifest.deployment.toml" for any project actually being cut over from
  `little-runner`'s own `[server]` config. Validated via `parse_manifest`
  before being written, so a malformed manifest is rejected outright
  rather than silently stored and only discovered at the next real
  deploy.
- **`deployment.deploy`** — build+install a commitish in a throwaway git
  worktree, never the project's own checkout; only stop the currently
  running version and launch the new one once install genuinely
  succeeds. Backed by `servers`/`deployments` tables — a full audit
  trail, not just latest-state.
- **`deployment.stop`** — defence in depth: `systemctl stop` the unit,
  then `pgrep`/`pkill -TERM` by the configured match string, escalating
  to `pkill -KILL` past a timeout, then confirms the port is actually
  free.
- **Tailscale exposure** — `tailscale serve` after a successful deploy,
  torn down on stop; a no-op when `[server.tailscale]` isn't configured.
- **Confirm-gated deploys** (opt-in via `confirm_timeout_seconds`) — an
  independent `systemd --user` timer arms after a successful deploy;
  unconfirmed by the deadline, its own dispatch
  (`POST /v1/rollback-timeout/{project_id}`, called directly, never
  through Orchestration, so it fires even if Orchestration itself is
  down) rolls back to the last confirmed commit. `deployment.confirm`
  settles it early; `deployment.seed_confirmation` establishes the first
  baseline from whatever's currently deployed.
- **Self-redeploy** (`deployment.redeploy_pillar`, M5) — the identical
  build-before-stop sequence, generalized to little-workshop's own
  pillars (including Core and this pillar itself). A real
  `/proc/self/cgroup` check detects a redeploy targeting the very pillar
  handling the request and dispatches to an independent, detached
  `systemd-run` unit instead of running inline — the same cgroup-aware
  escape proven for `little-runner`'s own self-restart. Confirm-gating
  defaults on for `core`/`deployment` at a 600-second window.
- **Deployed-commit read** (`GET /v1/pillars`) — the commit each pillar
  was last redeployed from (`pillar_deployments`), this checkout's HEAD,
  and its 10 newest commits with each one's `VERSION`; the Dashboard's
  Pillars page renders them.
- **Externally managed servers** (`external = true` in `[server]`, with an
  optional `unit = "<systemd unit>"`; 2026-09-21) — for a server that
  something else owns, such as little-workshop's own dashboard bridge (a
  persistent systemd unit publishing the app on the tailnet). Deployment
  reports its status — port listening, matching processes, the named unit's
  state, tailnet mapping — but **refuses to deploy or stop it** (`deployment.
  deploy`/`deployment.stop` fail before touching anything), because stopping
  it would also switch off its tailnet route and cut off the very connection
  it is managed through. `GET /v1/status/{id}` returns `external` and
  `managed_unit`; the Dashboard shows such a server as up/down but offers no
  Launch or Stop, and does not print an unknown version.
- **Worktree provisioning as a shared capability** (`POST /v1/worktrees`,
  `DELETE /v1/worktrees/{id}`) — built in M4 explicitly for other pillars
  to call rather than each provisioning their own: Requirements &
  Quality's sandboxed test execution (M10) and Policy & Compliance's
  test-script sandbox (M12) are both meant to use it.
- **A self-redeploy refreshes `lws`** (2026-09-24): the pre-commit version
  hook, the MCP server and the self-redeploy workers all run the
  pipx-installed `lws`, which nothing updated — a fix to any of them sat
  undeployed until someone ran `bazel run //:install` (the pre-commit sync
  fix did). Redeploying a pillar now also upgrades `lws` to the deployed
  commit, in place with its venv's own pip (recreating the venv would pull
  it out from under the processes running from it), once per version, and
  reports it as `cli_note` in the redeploy's result; a failed upgrade never
  fails the redeploy. REQ-000000272.

## Interfacing

- **Calls**: Registry & Portfolio — resolves a `project_id`'s repository
  path via `common.registry_client`, discovered through Core, the same
  way Version Control & Release does. Real `git`/`systemd-run`/
  `systemctl`/`tailscale` subprocess calls otherwise
  (`deployment/gitutil.py`, `common/systemd.py`, `deployment/netutil.py`).
  For a pillar self-redeploy specifically, also reads/writes its own
  `pillar_deployments` table and resolves this repository's own source
  location (`deployment/workshop_repo.py`), never another pillar's PI.
- **Called by**: `lws`, the MCP Server, and Orchestration for every
  `deployment.*` Job type. A confirm-gate watchdog timer calls
  `POST /v1/rollback-timeout/{project_id}` on this pillar's own socket
  directly (via `lws deployment rollback-timeout`), bypassing
  Orchestration on purpose. Requirements & Quality (M10) and Policy &
  Compliance (M12) are each planned to call `POST /v1/worktrees` for
  sandboxed execution once built.
- **Discovered through**: Core, like every pillar — including for the
  self-redeploy path, where a dispatched worker resolves *this pillar's
  own* new socket to confirm it came back healthy, and resolves
  Orchestration's socket to report completion back
  (`common.pillar_socket`).
