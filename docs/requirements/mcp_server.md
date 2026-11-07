# Requirements: MCP Server

Part of little-workshop's requirements register; conventions and the other parts are in [../requirements.md](../requirements.md). Sections keep the order and headings of the round that added them.

## M7 — MCP Server (minimal)

| ID | Requirement | Status |
|---|---|---|
| REQ-000000074 | The MCP Server is a hand-rolled, dependency-free JSON-RPC 2.0 / MCP stdio server (`initialize`, `tools/list`, `tools/call`) — the same proven shape `little_versions.mcp` already uses in this ecosystem, chosen over the `mcp` PyPI SDK so the pillar has no new runtime dependency and no risk of colliding with that package's own top-level `mcp` import name. The source package is named `mcp_server`, not `mcp`, for exactly that reason — Core's pillar registry still calls the pillar itself `"mcp"`. | done |
| REQ-000000075 | Every MCP tool is a thin translation from MCP arguments into a Pillar Interface call — a mutation becomes an Orchestration `POST /v1/jobs` (the same `create_job` convention the CLI already uses), a read becomes a direct `GET` on the owning pillar's socket, resolved through Core's registry (`common.pillar_socket.resolve_pillar_socket`), never hardcoded. The MCP Server holds no domain data of its own, matching Dashboard's identical "thin, stateless proxy" role in `docs/architecture.md`'s Components table. | done |
| REQ-000000077 | `mcp_server.client` is a deliberate duplicate of `cli.client`'s HTTP-over-Unix-socket glue, not a shared import — `docs/architecture.md`'s "no shared Python import path between two pillars' `src/` trees" rule, extended here to little-workshop's two thin proxies (CLI and MCP Server) the same way it already applies between every pair of pillars. Unlike `cli.client`, nothing falls back to a direct database/file read: an MCP caller is always a live session, so an unreachable pillar is simply the answer to report back. | done |
| REQ-000000078 | The MCP Server is never launched by Core/systemd — it's spawned on demand by whatever MCP client attaches it (`lws mcp serve`, over stdio), matching every real MCP server this ecosystem already runs. Core's manifest already defaulted `"mcp": False` with no launch `command` since M1, which — unchanged — is exactly the "not Core-supervised" state this pillar needs. | done |
| REQ-000000079 | Registered at **user scope** per the root MCP policy (`docs/policies/mcp_policy.md`), wired into `bazel run //:install`: `claude mcp remove little-workshop` (no forced scope, so a stale entry can't shadow the new one) followed by `claude mcp add little-workshop -s user -- lws mcp serve`; if `claude` isn't on `PATH`, the install prints the equivalent manual command instead of failing. | done |

## M9 — Orchestration (scheduled tasks)

| ID | Requirement | Status |
|---|---|---|
| REQ-000000092 | All eight operations and `stop` are exposed identically through the `lws schedule ...` / `lws job stop` CLI subcommands and matching `workshop_schedule_*` / `workshop_job_stop` MCP tools, following the exact HTTP-glue pattern already established for every other pillar capability — no shared import between `cli/` and `mcp_server/`. | done |

## M10 — Requirements & Quality

| ID | Requirement | Status |
|---|---|---|
| REQ-000000101 | All eleven operations and every read are exposed identically through `lws requirements ...` CLI subcommands and matching `workshop_requirements_*` MCP tools, following the exact HTTP-glue pattern (including registry-style local-database fallback for reads when the daemon is down) already established for every other pillar capability — no shared import between `cli/` and `mcp_server/`. | done |

## M11 — Build & Packages

| ID | Requirement | Status |
|---|---|---|
| REQ-000000110 | All seven operations and every read are exposed identically through `lws build ...` CLI subcommands and matching `workshop_build_*` MCP tools (publish commands base64-encode a real local file's bytes into the Job's `params`, the same "everything goes through `POST /v1/jobs/<type>` with a JSON body" convention every other mutation in this system already uses — no separate multipart-upload machinery invented for this pillar alone), following the exact HTTP-glue pattern (including registry-style local-database fallback for reads) already established for every other pillar capability. | done |

## M12 — Policy & Compliance

| ID | Requirement | Status |
|---|---|---|
| REQ-000000124 | All eleven operations and every read are exposed identically through `lws policy ...` / `lws job policy-confirm[-timeout]` CLI subcommands and matching `workshop_policy_*` MCP tools (`policy.author`'s draft is written to a JSON file and handed to `lws policy activate <draft.json>` for Gate 4, the same shape a human reviewing both the EARS text and the generated script before approving would actually use), following the exact HTTP-glue pattern (including registry-style local-database fallback for reads) already established for every other pillar capability. `agent.query` is deliberately excluded from both catalogs — internal plumbing for this pillar's own authoring pipeline, the same category `docs/architecture.md`'s MCP tool-catalog docstring already carves out for worktree provisioning and the self-redeploy/rollback watchdog. | done |

## M12 follow-up — Policy usability refinement (2026-09-16)

| ID | Requirement | Status |
|---|---|---|
| REQ-000000138 | All new operations exposed identically through `lws policy configure`/`waiver grant`/`waiver revoke`/`waiver list`/`gating-for-job` and matching `workshop_policy_configure`/`workshop_policy_waiver_*`/`workshop_policy_gating_for_job` MCP tools, following the same HTTP-glue and local-database-fallback pattern already established for every other pillar capability. | done |

## M14 — Dashboard

| ID | Requirement | Status |
|---|---|---|
| REQ-000000142 | `src/dashboard/` — a new pillar-shaped, thin, stateless proxy (`docs/architecture.md`'s Components table): a real FastAPI app bound to a Unix domain socket exactly like every domain pillar, holding no database of its own. `dashboard/client.py` is a deliberate duplicate of `cli/client.py`'s and `mcp_server/client.py`'s HTTP glue — the same "no shared Python import path between two `src/` trees" rule extended to this third thin proxy — with no local-database fallback on an unreachable pillar (matching MCP Server's own reasoning, not the CLI's). | done |

## M16 follow-up — parity survey and rejecting compatibility shims (2026-09-17)

| ID | Requirement | Status |
|---|---|---|
| REQ-000000163 | **`little-versions`' one parity gap, closed.** Standalone issue↔branch linking (functional parity with `lvx issue set/unset/list`, not literal command-surface parity): `version.issue_set`/`version.issue_unset` Job types and a `GET /v1/issues` read endpoint, backed by `configstore.py`'s new `remove_issue_link`/`list_issue_links` (the existing `set_issue_link`/`get_issue_link`, previously only called once from a topic branch's own `*_start`, are now reachable standalone). Linkability reuses `branches.py`'s own `issue_automation` flag per branch type (`feature`/`improvement`/`hotfix`) as the single source of truth, rather than a second, separately maintained list — a deliberate divergence from `little-versions`' own linkable set (`feature`/`release`/`hotfix`), since little-workshop's own branch taxonomy already added `improvement` as a type `little-versions` never had. Exposed through `lws version issue-set/issue-unset/issue-list` and `workshop_version_issue_set/unset/list` MCP tools. | done |

## Playwright as standard: MCP for agents, UI tests run against the app (2026-09-24)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000252 | **Agent runs get the Playwright MCP server.** When Agentic Development invokes Claude Code for an `agent.run_task` run, the pillar shall attach the Playwright MCP server through `--mcp-config` — a pinned `@playwright/mcp` version, headless, with an in-memory profile, the host's own installed Chromium when there is one, and its output directory outside the project checkout — unless `npx` is not on `PATH` or the run's own `playwright_mcp` param is `false`, in which case the run proceeds without it. See `docs/pillars/agentic_development.md`, "Playwright as standard". | superseded by REQ-000000480 |
| REQ-000000480 | **Agent runs on a project with a UI get the Playwright section and MCP server.** When Agentic Development invokes Claude Code for an `agent.run_task` run on a project with a UI (a `manifest.deployment.toml` among its project files, or a `tests/ui/` folder in its checkout), the pillar shall put the UI and UX (Playwright) section in the prompt and attach the Playwright MCP server through `--mcp-config` (a pinned `@playwright/mcp` version, headless, with an in-memory profile, the host's own installed Chromium when there is one, and its output directory outside the project checkout) unless `npx` is not on `PATH` or the run's own `playwright_mcp` param is `false`; for a project without a UI the run shall get neither the section nor the server. | done |
| REQ-000000253 | **Ad-hoc sessions get the Playwright MCP server.** When the user starts an ad-hoc Claude Code session on a project (`agent.session_start`), Agentic Development shall launch it with the same Playwright MCP server attached, its output directory under the sessions state directory, unless `npx` is not on `PATH`. | done |

## Policies as the hard layer of Claude instructions (2026-10-02)

| ID | Description | Status |
| --- | --- | --- |
| — | **Generated briefing section, rule and lifecycle skill.** The Claude files little-workshop generates for a project shall include .claude/little-workshop.md (the briefing section every project shares, imported by the project's AGENTS.md with an @ import), the path-scoped rule .claude/rules/claude-instructions.md, and the workshop-lifecycle skill holding the lifecycle routes and the steps before a branch finish; the guard shall be registered for Bash, the file-edit tools and the little-workshop MCP tools. | draft |

## Claude usage, collection: the Usage & Efficiency pillar (M19, 2026-10-04)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000334 | **Usage is read through lws, the MCP server and the Dashboard.** The CLI shall offer `lws usage summary`, `doctor`, `ingest-transcripts` and `configure-claude`; the MCP server shall offer `workshop_usage_summary` as a read pre-allowed in every project, `workshop_usage_doctor` and `workshop_usage_ingest_transcripts` for little-workshop's own sessions, and `workshop_usage_configure_claude` as a tool that always asks; and the Dashboard shall offer under System a Claude usage page that shows one week chosen from the weeks with data, filtered by project or origin, with plots of weighted tokens per day by token type, average context per request, cache hit ratio, long-context share, plan pressure and little-workshop against the other projects, comparison lines for the previous week, the next week and the four-week average when those exist, the per-project table, the heaviest sessions and the failed checks of the last doctor report. | draft |

## Claude usage, findings (M20, 2026-10-04)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000340 | **Findings are read through lws, the MCP server and the Dashboard.** The CLI shall offer `lws usage findings` and `lws usage analyze`; the MCP server shall offer `workshop_usage_findings` as a read pre-allowed in every project and `workshop_usage_analyze` for little-workshop's own sessions; and the Dashboard's Claude usage page shall list the open and accepted findings with their evidence, metric, estimated saving, recommended action and each experiment's before and after values, a button that opens the Optimize Claude usage job for the finding's project and a button that dismisses the finding, and shall list the resolved and dismissed ones with a button that reopens a dismissed finding. | draft |

## Claude usage, automation: mining and workshop triggers (M21, 2026-10-04)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000347 | **Candidates, triggers and the new Jobs are reached through lws, the MCP server and the Dashboard.** The CLI shall offer `lws usage candidates`, `lws usage mine-automation`, `lws trigger list`, `add`, `remove`, `enable`, `disable` and `install-defaults`, `lws requirements sync-markers` and `lws deployment redeploy-changed-pillars`; the MCP server shall offer the same as `workshop_usage_candidates` and `workshop_trigger_list` (reads), `workshop_requirements_sync_markers` (routine), `workshop_trigger_add` and `workshop_trigger_remove` (always asked), and `workshop_usage_mine_automation` and `workshop_deployment_redeploy_changed_pillars` (little-workshop's own); the generated `workshop-lifecycle` skill shall name the marker sync and, for little-workshop, the changed-pillars redeploy; and the Dashboard shall list the candidates on the Claude usage page with their class, cost, proposed form, recommended schedule and buttons Make it a task and Dismiss, and shall offer a Triggers page that lists the triggers and enables, disables and removes them. | draft |

## Asset review: Blender assets in the Dashboard (2026-10-09)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000426 | **A project chooses its visible asset folder and gets the asset-review MCP server.** When the project.configure_assets Job names a folder, the Registry & Portfolio pillar shall record it as the project's visible asset folder, refuse a folder that does not exist in the checkout or lies outside it, and declare the asset-review MCP server in the checkout's .mcp.json while keeping every other server in it, and when the folder is emptied it shall turn asset review off and remove that server. | draft |
| REQ-000000430 | **The asset-review MCP server exports a Blender file's model and previews.** When the asset_export tool of a project's asset-review MCP server is called for a Blender file in the visible asset folder, the server shall run Blender headless and write beside the file, in a folder named after it with the .view suffix, a glTF binary model, previews from the front, right, back, left, top and bottom and from two orthographic corners above and two below with the textures applied, the UV layout and the textures the materials use, and a manifest, shall list the folder's Blender files with their bundle state with asset_list, and shall refuse any path outside the folder. | draft |

## Windows-hosted projects reach little-workshop (2026-10-25)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000465 | **An HTTP gateway serves the MCP tools to a project's own code, as that project.** When a caller sends `GET /api/v1/tools` or `POST /api/v1/tools/{tool}` to the Dashboard with `Authorization: Bearer <token>`, the Dashboard shall answer 401 for a missing or unknown token and otherwise forward the call to the MCP pillar's HTTP gateway (`python -m mcp_server gateway`) as the token's project; the gateway shall list and run only the tools classified read-only, routine or gated in `common/tool_access.py`, fill the project into `project_id` (and `name` for the tools that name a project) when the caller left it out, refuse with 403 a tool classified ask or platform and any call naming another project, answer 404 for an unknown tool and 422 for missing or unknown arguments, and return the tool's result with whether it failed. | draft |
