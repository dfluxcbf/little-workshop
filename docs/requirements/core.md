# Requirements: Core

Part of little-workshop's requirements register; conventions and the other parts are in [../requirements.md](../requirements.md). Sections keep the order and headings of the round that added them.

## M1 — Core + Orchestration

| ID | Requirement | Status |
|---|---|---|
| REQ-000000002 | Core launches each enabled pillar as a transient `systemd --user` unit (`Restart=on-failure`), never an installed `.service` file. | done |
| REQ-000000003 | On Core's own startup, an already-running, healthy pillar is reconnected to, never relaunched. | done |
| REQ-000000004 | On Core's own startup, a disabled pillar that is still running is stopped; a missing, enabled, installed pillar is started fresh. | done |
| REQ-000000008 | Core exposes its live pillar registry over its own Unix-domain-socket interface (`/v1/status`), for the CLI (and, later, Dashboard/MCP) to query. | done |
| REQ-000000021 | The whole path — Core launching a real `systemd --user` unit, health-probing it over a real socket, and Orchestration dispatching a real Job to it over its Pillar Interface — is proven against a minimal reference pillar, not mocked. | done |

## M4 — Deployment & Servers

| ID | Requirement | Status |
|---|---|---|
| REQ-000000052 | Reflexive self-redeploy (this pillar, or Core, redeploying itself) is explicitly out of scope here — no cgroup-escape/self-restart-safety logic is ported; see M5. | done |

## M14 — Dashboard

| ID | Requirement | Status |
|---|---|---|
| REQ-000000143 | Four real, server-rendered panels consuming the decided visual identity directly (`presentations/assets/identity-tokens.css`, copied into `dashboard/static/` since a package's static assets can't reach outside its own tree): Overview (`/`, every pillar's live health from Core), Projects (`/projects`, Registry & Portfolio), Jobs (`/jobs`, Orchestration, optionally filtered by status), and the concrete, fully-specified Policy management panel (`/policies`, `/policies/{id}`) — every policy's `ears_text`, an `enabled` checkbox, a per-policy gate-point multi-select (all ten categories), a read-only view of the generated `test_script` (editing stays `lws policy activate`'s own job, per Gate 4), and per-project waiver grant/revoke with its required `reason` shown inline. Every mutation (`policy.configure`, `policy.waiver_grant`, `policy.waiver_revoke`) is an ordinary Job submitted to Orchestration through a real HTML form post — no new mechanism, this milestone surfaces what M12/M13's usability refinement already built. | done |

## M15 — Self-hosting / dogfooding

| ID | Requirement | Status |
|---|---|---|
| REQ-000000147 | A real, first-ever production `manifest.toml` written at `~/.config/little-workshop/manifest.toml`, pointing every pillar's `command` at the actual `pipx`-installed venv's own Python (`~/.local/share/pipx/venvs/little-workshop/bin/python3 -m <pillar>`) — the "populate real commands for a real install" step `docs/design.md`/`docs/architecture.md` assumed would exist by now but that no prior milestone had actually built (every integration test hand-constructs its own `Manifest`/`systemd.start` calls directly, never through this path). Core, Orchestration, Registry, Requirements & Quality, Policy & Compliance, Version Control & Release, Deployment & Servers, Agentic Development, and Dashboard all run as real `systemd --user` units under this manifest, self-hosted for the first time. | done |

## M17 — ecosystem cutover begins: `little-mindcheck` as pilot (2026-09-17)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000192 | Proven at the unit level (`test_deployment_app.py`: 6 new tests for `deployment.set_manifest`'s success/invalid-TOML/missing-required-field/missing-project-id/missing-toml paths and the new `GET /v1/manifest/{project_id}` read, both found and missing; `test_dispatch.py`: `dispatch_timeout_for("deployment.deploy")` now returns the raised ceiling, not the default; `test_cli_success_paths.py`/`test_mcp_tools.py`: the new CLI and MCP argument-translation paths) — the deploy/stop verification itself was proven for real against `little-mindcheck`'s own actual repository and server (REQ-000000191), not against a throwaway fixture, since that was the entire point of the pilot. | done |

## Redeploying Core, and automation candidates without noise (2026-10-04)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000351 | **Redeploying Core restarts the installed Core instead of starting a second one.** When a redeploy of the `core` pillar is asked for while Core runs as the installed unit `little-workshop.service`, the Deployment & Servers pillar shall refresh the installed `lws` environment to the commit, shall restart that unit and wait for Core's socket to answer, shall start no transient Core unit and stop no process by name, and shall arm no confirmation window or rollback for it; and where Core is not running as that unit the pillar shall redeploy it as it does any other pillar. | draft |

## Pillar crashes and the work they cut (2026-10-05)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000400 | **Every abnormal end of a pillar's process is a crash incident.** If the systemd unit of a pillar or of Core ends abnormally (its process exits with a failure or is killed by a signal or the out-of-memory killer or is killed after overrunning its stop or start timeout or keeps failing to start), then the Core supervisor shall record one incident of kind "crash" for that run of the unit with what systemd did, systemd's lines for that run and the last lines of the process's own log, also for a failure that happened while Core itself was down. | draft |
| REQ-000000401 | **A pillar shown crashed or faulted gets an incident that explains it.** When a pillar that Core marked crashed answers again or is marked faulted, the Core supervisor shall record one incident of kind "crash" for that outage with how long the pillar did not answer, its unit's state and the health probe's error at each check, what came of each relaunch and any failure systemd reported for its unit, also when no process failed. | draft |
| REQ-000000408 | **A pillar that runs but does not answer is restarted.** If a pillar's unit has been running for more than two minutes and the pillar does not answer Core's health check, then the Core supervisor shall stop the unit and start it again and record that in the outage's incident, and it shall not relaunch a unit that systemd is still starting or stopping. | draft |
| REQ-000000409 | **A busy pillar still answers its health check.** While a pillar is busy with work that holds its lock, the pillar's health endpoint shall answer within about a second and say that it is busy instead of waiting for the lock, so that Core never takes a busy pillar for a hung one. | draft |
