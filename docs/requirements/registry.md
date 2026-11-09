# Requirements: Registry & Portfolio

Part of little-workshop's requirements register; conventions and the other parts are in [../requirements.md](../requirements.md). Sections keep the order and headings of the round that added them.

## M2 — Registry & Portfolio (bootstrap path)

| ID | Requirement | Status |
|---|---|---|
| REQ-000000022 | `project.bootstrap` scaffolds the standard docs/src/tests layout, `git init`s on `master`, commits it, and creates a `dev` branch — without depending on `lreq`/`lvx`/`.lvx` (a bootstrapped project is little-workshop's own from birth). | done |
| REQ-000000023 | `project.bootstrap` optionally creates a GitHub repository (via the `gh` CLI or a token-based REST call) and pushes `master`/`dev`, mirroring `bootstrap-little-project`'s two backends — never exercised against the real GitHub API in this repo's own tests. | done |
| REQ-000000024 | A successful bootstrap registers the project in the `projects` table (the authoritative registry) and creates its project-files directory as a consequence of that registration, never the other way around. | done |
| REQ-000000025 | Bootstrapping a name that's already registered fails cleanly (Job outcome `failed`, no partial registry row) rather than corrupting the existing entry. | done |
| REQ-000000026 | `project.remove` hard-deletes the project's `projects` row and its own dependent rows (`project_set_members`, `tickets`) and its project-files directory, immediately, with no soft-delete or undo — leaving the actual git repository on disk untouched. | done |
| REQ-000000027 | `project_sets` supports creating a set, listing sets, and adding/removing project membership. | done |
| REQ-000000028 | `tickets` supports creating a ticket against a project, listing by project, and updating status — the source of record for portfolio tracking even for a project with no GitHub remote. | done |
| REQ-000000029 | Registry & Portfolio's Pillar Interface follows the Job-dispatch convention (`POST /v1/jobs/<type>`) for `project.bootstrap`/`project.remove`, and exposes direct read endpoints for `projects`/`project_sets`/`tickets` that don't need a Job's audit trail. | done |
| REQ-000000030 | The `lws` CLI supports `project bootstrap`/`list`/`remove`; `project list` falls back to a direct database read when the daemon is down, matching the established CLI fallback convention. | done |

## M3 — Version Control & Release

| ID | Requirement | Status |
|---|---|---|
| REQ-000000041 | Every mutating command resolves its `project_id` to a repo path by calling Registry & Portfolio over a real Pillar Interface, its socket discovered through Core's registry — never a hardcoded path — and fails cleanly (never crashes) when Registry & Portfolio is unavailable. | done |
| REQ-000000043 | Proven end-to-end: Registry & Portfolio and Version Control & Release both running as genuine `systemd --user` units, with Version resolving the bootstrapped project through Core's registry and running a real feature branch (start, a real commit, rebase, squash, merge, patch bump) via dispatched Orchestration Jobs. | done |

## M4 — Deployment & Servers

| ID | Requirement | Status |
|---|---|---|
| REQ-000000053 | Proven end-to-end: Registry & Portfolio bootstraps a project and Deployment & Servers (both genuine `systemd --user` units) deploys it into a real, listening process — verified with a real socket probe, not a mock — and a stop genuinely frees the port. | done |

## M6 — Agentic Development

| ID | Requirement | Status |
|---|---|---|
| REQ-000000073 | Proven end-to-end against genuine `systemd --user` units (Registry & Portfolio, Version Control & Release, Agentic Development): a bootstrapped project carried through a real `agent.run_task` — a real `version.feature_start` call creating a real branch, a real seven-section report, a real async completion callback — with only the agent session itself stubbed (a fake `claude` executable that still honours the exact same result-file/completion-marker contract), never the real, costly, non-deterministic CLI. | done |

## M7 — MCP Server (minimal)

| ID | Requirement | Status |
|---|---|---|
| REQ-000000076 | Tool coverage as of this milestone: every mutating Job type and read endpoint already built for Registry & Portfolio, Version Control & Release, Deployment & Servers, and Agentic Development (M2/M3/M4/M6) — excluding internal-plumbing entry points (worktree provisioning, the self-redeploy/rollback confirm-timeout watchdog and its detached workers) built for other pillars to call, not for a human or agent session. Tools are namespaced `workshop_<pillar>_<operation>`. | done |

## M8 — Registry & Portfolio (import path)

| ID | Requirement | Status |
|---|---|---|
| REQ-000000082 | Unlike `project.bootstrap`, `project.import` never scaffolds, `git init`s, commits, or creates a GitHub repo — it only reads the repository's existing `origin` remote (if any) to populate `repo_url`, and never mutates the repository's own history. | done |
| REQ-000000083 | Exposed as `POST /v1/jobs/project.import` on Registry & Portfolio's own Pillar Interface (mirroring `project.bootstrap`'s Job-dispatch convention) and as the `workshop_project_import` MCP tool — the concrete "MCP-exposed entry point" this milestone's own text names. No new Agentic Development machinery was needed: once a project is registered via `project.import`, it's resolvable like any other, and a normal `workshop_agent_run_task` (M6/M7) is how any further "bring it into conformance" work actually gets done. | done |
| REQ-000000084 | Proven end-to-end against a genuine `systemd --user` Registry unit: a real, pre-existing git repository (never touched by `project.bootstrap`) gets registered via a real Orchestration-dispatched Job and, separately, via the real `workshop_project_import` MCP tool — the second proof also reading a real `origin` remote into `repo_url` — with the repository's own commit history provably unchanged by either path. | done |

## M9 — Orchestration (scheduled tasks)

| ID | Requirement | Status |
|---|---|---|
| REQ-000000088 | `stop` is declared-with-probing-fallback: `DECLARED_UNSTOPPABLE_JOB_TYPES` (currently every Registry & Portfolio and Version Control & Release Job type) short-circuits with no network call; anything not declared falls through to a real `POST /v1/jobs/{job_id}/stop` on the target pillar, whose own response is authoritative. A stopped Job gets `status: "stopped"` and `error: "stopped by user"`. | done |

## M10 — Requirements & Quality

| ID | Requirement | Status |
|---|---|---|
| REQ-000000094 | `little-requirements`' Project→Suite→Requirement→TestResult model carried over into one pillar-owned, `project_id`-scoped SQLite database (`test_suites`, `requirements`, `requirement_test_mappings`, `test_results`), reachable only through this pillar's own Pillar Interface — never a shared file. "Project" itself is not this pillar's own concept: every table is keyed by the `project_id` Registry & Portfolio already owns. | done |
| REQ-000000103 | Proven end-to-end against genuine `systemd --user` Registry, Deployment, and Requirements units: a requirement's full draft→linked→tested→published→done lifecycle against real Jobs; `run_tests` genuinely provisioning a real Deployment-owned worktree and running a real `pytest` inside it (never the project's own working checkout) with real passed/failed results ingested from the real JUnit-XML report; and `import_junit` ingesting an externally-produced report against a real unit. | done |

## M11 — Build & Packages

| ID | Requirement | Status |
|---|---|---|
| REQ-000000112 | Proven end-to-end against genuine `systemd --user` Registry, Deployment, and Build units: a real build command executed inside a real Deployment-provisioned worktree (with a file it wrote there provably absent from the project's own working checkout afterward); a real nonzero exit recorded and reported as a failed Job; a real wheel published and downloaded back byte-for-byte through the real PEP 503 index; a real `.vsix` published and downloaded through its own namespace, absent from the PEP 503 index; and the fork-safety gate refusing a real publish attempt from a real fork-flagged build run, leaving no blob on disk for it. | done |

## M12 — Policy & Compliance

| ID | Requirement | Status |
|---|---|---|
| REQ-000000113 | Three tiers of rules kept distinct exactly as specified: hard-coded invariants stay out of this pillar entirely (never a `Policy` row); a `Policy.created_by` of `framework` (little-workshop's own maintainers) is always scoped `all_projects` and force-set regardless of what was requested, never overridable by a user policy; a `Policy.created_by` of `user` is scoped to one project, one project set, or all projects and is free to be anything that doesn't conflict with a framework policy. `project_sets` itself stays Registry & Portfolio's own table, read live through its Pillar Interface (`policy/registry_client.py`), never mirrored here. | done |
| REQ-000000126 | Proven end-to-end against genuine `systemd --user` Registry, Deployment, Agentic Development (a fake `claude` executable — the same contract-preserving stub every prior agent-flow integration test already uses, never the real, costly, non-deterministic CLI), and Policy & Compliance units: the full authoring pipeline producing a real activated policy; a real Docker-sandboxed check against a real bootstrapped project recording a real `pass` verdict; a gated deploy blocked at `pending_confirmation` and resolved via a real confirm (the server genuinely starts); and the same gate resolved via a real timeout (the server never starts) — the "two clocks, never both on the same path" distinction proven both ways, not just designed. | done |

## M13 — Populate the policy catalog

| ID | Requirement | Status |
|---|---|---|
| REQ-000000131 | Proven end-to-end against genuine `systemd --user` Registry, Agentic Development (a fake `claude` executable — the same contract-preserving stub every prior agent-flow integration test already uses), and Policy & Compliance units: `lws policy catalog run` on a real catalog entry (`two-long-lived-branches`) produces a real draft through the real pipeline, a real `policy.activate` saves it as `created_by: framework`/`scope_level: all_projects`, and it's confirmed to be the winning policy for any project sight-unseen (framework beats everything). | done |

## M12 follow-up — Policy usability refinement (2026-09-16)

| ID | Requirement | Status |
|---|---|---|
| REQ-000000139 | Proven end-to-end against genuine `systemd --user` Registry, Deployment, Agentic Development, and Policy & Compliance units: a policy authored with `gate_points` genuinely gates a real `deployment.deploy` exactly as the original single-topic mechanism did (regression-proof of the generalized mechanism); disabling that same policy via `policy.configure` lets a previously-blocked deploy through for real, with `GET /v1/policies/gating-for-job` confirmed to stop listing it; and granting one project a waiver lets that project's real deploy through while a second, non-waived project on the identical policy still genuinely blocks at `pending_confirmation`. | done |

## M12 follow-up #2 — Additional gate-point categories (2026-09-16)

Requested directly, same day as the usability refinement above: the original six categories left some real Job types (`deployment.stop`, `deployment.redeploy_pillar`, `build.publish_pypi`/`publish_vsix`, `project.bootstrap`/`project.import`) either ungated or folded into a category whose blast radius didn't actually match.

| ID | Requirement | Status |
|---|---|---|
| REQ-000000140 | `policy.models.GatePoint` gains four values: `self_redeploy` (a pillar redeploying its own running unit — meaningfully riskier than an ordinary `deploy_redeploy`, split out rather than reusing it), `server_stop` (stopping a running server — previously ungated), `package_publish` (publishing a build artifact to the PEP 503 index or `.vsix` namespace — split out of `release`, since publishing a package and cutting a version release are different acts that can fail independently), and `project_registration` (bootstrapping or importing a project into the Registry — previously ungated). `orchestration/policy_gate.py`'s `JOB_TYPE_CATEGORIES` updated accordingly: `deployment.redeploy_pillar`→`self_redeploy`, `deployment.stop`→`server_stop`, `build.publish_pypi`/`build.publish_vsix`→`package_publish` (moved off `release`), `project.bootstrap`/`project.import`→`project_registration`. | done |

## M14 — Dashboard

| ID | Requirement | Status |
|---|---|---|
| REQ-000000145 | Proven end-to-end against genuine `systemd --user` Registry, Agentic Development (a fake `claude` executable, the same contract-preserving stub every prior agent-flow integration test already uses), Policy & Compliance, Orchestration, and Core units, plus Dashboard itself as a real `systemd --user` unit bound to a real socket: a real policy authored and activated through the pipeline is listed in the panel by its `ears_text`; the enable/disable checkbox (including the "no `enabled` field at all" shape a real unchecked checkbox actually posts) and gate-point multi-select each drive a real `policy.configure` Job, confirmed against a direct `policy_get` re-read; granting and revoking a real waiver through the panel's own form is confirmed the same way; and a second test with nothing but Dashboard running confirms every panel (`/`, `/projects`, `/jobs`, `/policies`) renders the unreachable banner instead of crashing. Unit coverage: 99% over `src/dashboard/` (`dashboard/app.py`/`client.py` at 100%; `__main__.py` at 93%, matching every other pillar's own entrypoint-coverage pattern in `tests/unit/test_main_entrypoints.py`). | done |

## M15 — Self-hosting / dogfooding

| ID | Requirement | Status |
|---|---|---|
| REQ-000000148 | little-workshop registered itself in its own Registry & Portfolio via a real `project.import` Job against its own real checkout (`/home/crist/little-projects/little-workshop`) — project name `little-workshop`, `repo_url` resolved live from `git remote` exactly as `project.import` does for any other repository, no special case. | done |
| REQ-000000150 | One real framework-policy candidate drafted through the actual four-gate pipeline's Gates 1-3, against the real Claude CLI (never the fake-stub tests use): `"The little-workshop repository shall not contain a .lvx/config.json file."`, formalizing `docs/governance_policy.md`'s independence principle into a mechanically-checkable, real, Claude-generated pytest script. **Deliberately left un-activated**: Gate 4 requires genuine human review and approval "no exception, no bulk shortcut" (REQ-000000133) — that applies to little-workshop's own framework policies exactly as it does to a catalog entry or a user's own policy, so this pass drafts and stops, leaving the draft for a maintainer's own `lws policy activate` call rather than self-approving on their behalf. A second candidate (`"a project named 'little-workshop' shall exist in Registry & Portfolio's project registry"`) hit `OQ-14`'s real dispatch-timeout defect and was not completed this pass. | done |

## M16 follow-up — parity survey and rejecting compatibility shims (2026-09-17)

| ID | Requirement | Status |
|---|---|---|
| REQ-000000164 | Proven at the unit level (`test_version_issue.py`, `test_version_schema_configstore.py`'s new cases, `test_version_app_extra.py`'s Job-and-endpoint round trip, CLI and MCP wiring in `test_cli_success_paths.py`/`test_cli_client_daemon_success.py`/`test_mcp_tools.py`/`test_mcp_client.py`) and end-to-end (`test_version_gitflow_end_to_end.py`: a real `feature/widgets` branch linked, listed, and unlinked over real Registry and Version Control & Release `systemd --user` units, plus a real rejection when linking a non-linkable `support/*` branch). | done |
| REQ-000000171 | **Documented, deliberate scope reduction**: three more `little-requirements` commands are left un-ported, each for a reason specific to little-workshop's own architecture rather than an oversight. `workspace`/`install`/`update` (a client-side `.reqs`/`database.conf` config-file model, mirroring each project's local view of a remote/local DB) has no equivalent in a server-authoritative daemon where every pillar is reached through its own Pillar Interface — the same "no equivalent" finding already recorded for `little-runner`'s `.lrun/config.toml` (REQ-000000169). `project` create/delete/list/get is superseded outright, not merely unported: this pillar was never meant to own a "project" concept of its own — Registry & Portfolio (M2) already is that authority, with every table here keyed by its `project_id`. `lock`/`unlock`/`unlock-server` (file-level write locking for N processes sharing one SQLite database) is moot once a single daemon process is the only writer; this pillar's own `threading.Lock()` already serializes every request. | removed 2026-09-21 — dropped by decision (`workspace`/`project`/`lock` commands is out of scope) |

## M16 follow-up — `little-runner`, the last absorbed tool (2026-09-17)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000177 | Proven at the unit level (`test_agent_github_adapter.py`: `pull_request`/`post_status`, success and failure paths; `test_agent_requirements_client.py`: the full seam-mocked success/failure/timeout matrix `test_agent_version_client.py` already established for its own pillar; `test_agent_flow.py`: `run_ci_check`'s success/failure/no-tests-ran/infra-failure/pass-through-params paths, all against a fake `GitHubAdapter`; `test_agent_app.py`: the real Job's accept-then-background-complete lifecycle and its two new read endpoints; `test_cli_success_paths.py`/`test_mcp_tools.py`: the CLI and MCP argument-translation paths for `ci-check`/`ci-list`/`ci-status` and `job queue show/release`) and end-to-end (`test_agent_ci_check_end_to_end.py`: a real `agent.ci_check` Job against real Registry, Deployment, Requirements, and Agentic Development `systemd --user` units — a real sandboxed `pytest` run through a real worktree, with only the GitHub CLI itself faked (no real GitHub remote to test against hermetically) — proving two real commit statuses (`pending` then `success`) were posted in order; `test_orchestration_lifecycle_end_to_end.py`: `lws job queue show/release` against a real queue-key mutex, including the stopped Job unblocking the queued one for real). | done |

## M16 follow-up — `little-runner`'s branch audit (2026-09-17)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000180 | Proven at the unit level (`test_version_branch_audit.py`: 11 tests against real git repos — clean, stale-merged, unmerged-but-recent, abandoned-past-threshold, unknown-naming both merged and unmerged, release/support exemption, detached HEAD, a stray worktree, a mid-rebase skip, and `audit_as_dicts`' own shape; `test_version_app_extra.py`: the real Job over the Pillar Interface, clean and with a real violation, and a clean failure on an unresolvable project) and end-to-end (`test_version_gitflow_end_to_end.py`: a real `version.branch_audit` Job against real Registry and Version Control & Release `systemd --user` units, finding a real merged `feature/done` branch after a real `git merge --no-ff`). | done |

## M16 follow-up — `little-runner`'s `stabilize run`, the last real gap (2026-09-17)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000184 | Proven at the unit level (`test_version_stabilize.py`: 11 tests against real git repos with a real bare `origin` remote — clean dev dry-run vs. execute, a dirty tree committed and pushed for real, a feature branch finished and pushed for real (dry-run and execute), an already-merged leftover branch actually deleted remotely and locally on execute but only reported on dry-run, release-branch and unknown-branch-name skips, a mid-rebase `needs_manual_resolution`, a missing-remote skip, and a real diverged-integration-branch `needs_manual_resolution` proven against a second real clone that pushed a genuinely conflicting commit; `test_version_app_extra.py`: the real Job over the Pillar Interface, a no-remote skip, a real dry-run result shape, and a clean failure on an unresolvable project) and end-to-end (`test_version_gitflow_end_to_end.py`: a real `version.stabilize --execute` Job against real Registry and Version Control & Release `systemd --user` units — a real feature branch really finished and pushed, and a real already-merged leftover branch really deleted from the real bare remote). | done |

## M16 follow-up — `little-runner`'s last item: ad-hoc sessions (2026-09-17)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000186 | Proven at the unit level (`test_agent_session_flow.py`: 14 tests against a fake `CommandRunner` — the dtach-missing/already-running/not-trusted refusals, a real launch's exact `systemd-run --scope` argv shape, a stale leftover socket file not blocking a new start, a clean stop's real `systemctl stop`+`reset-failed`+two `pkill` calls, and `is_trusted`'s own real-file-reading edge cases; `test_agent_app.py`: the two Jobs and the read endpoint over the Pillar Interface, including an unresolvable project and a surfaced `SessionError`; `test_cli_success_paths.py`/`test_mcp_tools.py`: the CLI and MCP argument-translation paths) and end-to-end (`test_agent_session_end_to_end.py`: a real `agent.session_start`/`session_stop` pair against a real Registry+Agentic Development `systemd --user` pair — real `dtach`, a real `systemd --user --scope` unit, a real refused double-start while one is already running, and a real stop that leaves no unit or process behind; only the Claude CLI itself is faked, matching this suite's existing convention). | done |

## Dashboard page-load sweep: Pillars page fixed (2026-09-23)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000248 | **A GitHub credential's status is cached, not re-checked on every page view.** Where the System Health secrets panel reports the GitHub credential's validity/expiration, Registry & Portfolio shall cache `github_token_status()`'s result for a bounded window (5 minutes) rather than shelling out to `gh` — including one real network round trip to GitHub's own API — on every `/v1/secrets` call. Root cause: after REQ-000000247 fixed the git-commits cost, `/pillars` was still measured at 1.3-2.0s live, entirely spent in this one call. A short TTL, not an eternal launch-time cache: a revoked/rotated token still surfaces within a few minutes, never only after a restart. | done |

## Unity project template (2026-09-24)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000274 | **Bootstrap offers a Unity project template.** When project.bootstrap is called with template "unity", the Registry shall scaffold the Unity layout (docs, README, VERSION, a Unity .gitignore and .gitattributes, CLAUDE.md, tools/run-unity-tests and its converter) instead of the Python/Bazel layout; when template is omitted, the Registry shall scaffold the "python" layout; when template names any other value, the Registry shall reject the request without writing any file. Exposed as the MCP tool's `template` and `lws project bootstrap --template`. | done |
| REQ-000000275 | **Unity template tracks binary assets with Git LFS and merges Unity YAML.** When the Registry scaffolds a Unity project, the Registry shall write a .gitattributes that routes binary asset types through Git LFS, marks Unity YAML assets and .meta files as text merged by the unityyamlmerge driver, and normalises line endings. | done |
| REQ-000000276 | **Unity template ignores generated folders.** When the Registry scaffolds a Unity project, the Registry shall write a .gitignore that excludes Library, Temp, Obj, Build(s), Logs, UserSettings, MemoryCaptures, IDE project files and the test runner's results, while keeping Assets, Packages and ProjectSettings tracked. | done |
| REQ-000000278 | **Unity template ships Claude guidance.** When the Registry scaffolds a Unity project, the Registry shall write a CLAUDE.md telling Claude sessions to drive the Editor through the Unity CLI rather than hand-editing scene YAML, never to touch generated folders, to keep .meta files paired with their assets, and to run tests through tools/run-unity-tests. | done |

## Connect a registered project to GitHub (2026-09-25)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000279 | **Connect a registered project to a new GitHub repository.** When project.connect_github runs for a registered project whose checkout has no origin remote, the Registry shall create the GitHub repository named after the project (private unless public is requested, through the given token or else a logged-in gh), add it as origin, push master and dev, and record its URL as repo_url; when neither a token nor a logged-in gh is available, the Registry shall fail without changing the checkout or the project record. | done |
| REQ-000000280 | **Connect adopts an existing origin remote.** When project.connect_github runs for a registered project whose checkout already has an origin remote, the Registry shall adopt that remote without creating a repository or pushing, and shall record its URL, normalised to https://host/owner/name, as repo_url in the registry and in info.registry.json. | done |
| REQ-000000281 | **Connect ensures the bootstrap labels and is repeatable.** When project.connect_github runs with ensure_labels (the default) and the repository's owner/name is known, the Registry shall ensure the labels project.bootstrap creates; when it runs again for an already-connected project, the Registry shall leave the project in the same state and report success. | done |
| REQ-000000282 | **Connect is reachable from MCP and the CLI, and refuses unknown projects.** When a caller invokes workshop_project_connect_github or `lws project connect-github NAME`, the system shall queue a project.connect_github Job for the Registry; when the project is not registered or its path is not a git repository, the Registry shall fail the Job naming the cause and change nothing. | done |

## Claude Code files in a new project (2026-10-01)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000293 | **Bootstrap scaffolds a .claude/settings.json that routes lifecycle through little-workshop.** When project.bootstrap scaffolds a project with either template, the Registry shall write .claude/settings.json that pre-allows the little-workshop MCP tools and lws commands for reading, routine work and policy-gated lifecycle actions, requires confirmation for those that stand in for a human decision or act across projects, and denies the raw git, systemd, twine and gh commands that little-workshop performs under policy. | draft |
| REQ-000000294 | **Bootstrap scaffolds .claude/AGENTS.md that routes lifecycle through little-workshop.** When project.bootstrap scaffolds a project with either template, the Registry shall write .claude/AGENTS.md telling Claude to route branch finish, release, deploy and publish through the little-workshop MCP server or lws and to name only the project's own id, and shall write no CLAUDE.md, because Claude Code reads AGENTS.md only where a project has none. Supersedes the root CLAUDE.md of REQ-000000274 and REQ-000000278 for the unity template, whose working rules move into .claude/AGENTS.md unchanged. | draft |
| REQ-000000295 | **little-workshop's own .claude/settings.json follows the scaffolded routing table.** The little-workshop repository shall keep a checked-in .claude/settings.json bound to the project id little-workshop whose deny rules, ask rules and MCP allow rules equal those the Registry scaffolds for a project, plus the platform-administration tools that only little-workshop itself needs. | draft |
| REQ-000000296 | **Scaffolded settings bind lws commands to the new project.** When project.bootstrap writes .claude/settings.json, the Registry shall bind every pre-allowed lws command that takes --project-id to the id of the project being scaffolded, and shall write no other project's id into the file. | draft |
| REQ-000000297 | **Scaffolded projects deny little-workshop calls that name another project.** When project.bootstrap scaffolds a project, the Registry shall write .claude/hooks/lws_scope_guard.py and register it as a PreToolUse hook in .claude/settings.json, and the hook shall deny a little-workshop MCP tool call or an lws command that names a project other than the scaffolded one. | draft |
| REQ-000000298 | **Bootstrap keeps per-machine Claude files out of git.** When project.bootstrap scaffolds a project with either template, the Registry shall add .claude/settings.local.json and .claude/worktrees/ to the project's .gitignore. | draft |

## Claude-native policy enforcement (2026-10-02)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000301 | **Scaffolded projects carry the policy-preflight skill.** When project.bootstrap scaffolds a project, the Registry shall write .claude/skills/policy-preflight (SKILL.md and scripts/preflight.py), which runs the policies a gated action would hit against the checkout and prints each failure with its rule and reason, and shall pre-allow that script in .claude/settings.json. | draft |
| REQ-000000305 | **Scaffolded AGENTS.md says what enforces the rules.** When project.bootstrap writes .claude/AGENTS.md, the Registry shall say in it that the commit-msg hook checks the commit-time policies, that the policy-preflight skill runs before a gated action, and that the scope guard keeps the session in the project. | draft |

## Policies as the hard layer of Claude instructions (2026-10-02)

| ID | Description | Status |
| --- | --- | --- |
| — | **The guard refuses raw lifecycle commands with the route to take.** When a Claude session in a registered project runs a shell command that git merges, tags, pushes, cherry-picks, deletes a branch, hard-resets, cleans, commits without the hooks (--no-verify or -n) or overrides core.hooksPath, starts, stops, restarts or kills a systemd --user unit, runs systemd-run, twine, pipx install, gh repo create, gh pr create or merge, or gh release, in any spelling the hook can parse (global git options, bash -c), the generated PreToolUse guard shall refuse it with a reason naming the little-workshop route to take instead. | draft |
| — | **The guard refuses hand edits of generated files and of a CLAUDE.md.** When a Claude session in a registered project edits or writes VERSION, a file under .githooks/ that little-workshop installs, a .claude/ file little-workshop generates, or a file named CLAUDE.md or CLAUDE.local.md inside the project, the generated guard shall refuse it with a reason saying how that file changes instead. | draft |

## Claude usage, findings (M20, 2026-10-04)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000338 | **The Claude files of every project are synced by one Job, each on its own branch.** When the `registry.sync_claude_files` Job runs, the Registry & Portfolio pillar shall report for every registered project, or the one the Job names, which generated Claude files differ from the templates; and only when the Job's `write` parameter is true shall, for each project that differs and has no uncommitted changes, have the Version pillar start an improvement branch, write the files there, commit them, and leave the branch for the owner to finish, never merging it. | draft |

## Backlog (2026-10-04)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000369 | **Each project has a backlog.** The Registry & Portfolio pillar shall keep backlog entries per registered project, each with its primary entry as the user wrote it, the user's refinement notes, one refined entry, one task prompt and when each changed; shall add, list, read, edit and delete them through its Pillar Interface, the `lws project backlog` commands and the `workshop_project_backlog_*` MCP tools; and shall delete an entry, with every field it holds, when it is deleted by any of them, and every entry of a project when the project is removed. | draft |
| REQ-000000370 | **Refine entry.** When the user asks to refine a backlog entry, the Registry & Portfolio pillar shall have Claude, reading the project's checkout without being able to change it, write a refined entry from the primary entry, the current refined entry and the user's refinement notes that says where the change goes and how that part works today while taking the requirements from the user alone, and shall replace the entry's one refined entry with it; an entry deleted while Claude worked keeps nothing of the result. | draft |
| REQ-000000371 | **Generate task prompt.** When the user asks for a backlog entry's task prompt, the Registry & Portfolio pillar shall have Claude, reading the project's checkout without being able to change it, write a prompt for a Claude task from the refined entry (not the primary entry or the notes) that names the files to change and the tests to add or run, shall add nothing to it about the backlog (the task never closes the entry), and shall replace the entry's one task prompt with it. | draft |

## Reading only what is shown; Claude's short answers watched (2026-10-05)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000385 | **A backlog entry keeps what Claude is doing for it.** The Registry & Portfolio pillar shall keep on each backlog entry whether Claude is refining it or writing its task prompt, since when, with which Job, and why the last attempt failed, and the Dashboard's backlog pages shall show that without reading Jobs. | draft |
| REQ-000000387 | **A deleted entry's Claude transcripts go with it.** When a backlog entry is deleted, the Registry & Portfolio pillar shall have the Agentic Development pillar delete the transcripts of every Claude session that worked on it, so that nothing of the entry is kept. | draft |

## Windows-hosted projects reach little-workshop (2026-10-25)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000466 | **A Windows-hosted project's briefing says how its Windows code reaches little-workshop.** When little-workshop writes the shared briefing of a project whose checkout is on a Windows drive, the Registry & Portfolio pillar shall add to `.claude/little-workshop.md` a section naming `lws.cmd` (installed with `lws windows install-shim`) and the HTTP gateway (`/api/v1/tools` with a Bearer token from `lws api token-create`, and what it may and may not do), and shall leave the briefing of a project checked out in WSL unchanged. | draft |

## Versioning without churn (2026-11-07)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000494 | **A generated hook whose script is missing lets the tool call through.** When a Claude Code hook command that little-workshop generates in .claude/settings.json runs in a checkout that does not hold its script (an older branch checked out), the command shall exit 0 and print that the hook did not run, instead of failing the tool call. | draft |

## Requirements and policies only when necessary (2026-11-07)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000495 | **A project's governance level is a Registry setting.** The Registry & Portfolio pillar shall keep a governance level per project, `light` or `full`, `light` for a project registered without one (including every project registered before the setting existed), and shall change it only through the `project.set_governance` Job (`lws project set-governance`, the MCP tool `workshop_project_set_governance`, the Dashboard's Settings page), refusing any other value. | draft |
| REQ-000000497 | **Generated Claude files keep a project-owned section.** When little-workshop rewrites a project's generated Markdown Claude files, the system shall keep what the checkout holds between the `<!-- project -->` and `<!-- /project -->` markers, shall never remove a rule, skill or agent file it did not generate, and the guard shall allow an edit of a generated Markdown file that changes nothing outside that section. | draft |
| REQ-000000498 | **Learned rules go to the project's learned-rules file.** The generated briefing and instructions rule shall say that a rule learned in a session (claude-reflect's learned rules included) is written as a bullet in `.claude/rules/learned.md`, and when the guard refuses a write of a CLAUDE.md it shall name that file as where a learned rule goes. | draft |

## Toolkit: public skills, agents and plugins per project (2026-11-07)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000502 | **Toolkit sources are pinned GitHub repositories with discovered items.** When a GitHub repository is added as a toolkit source, the Registry & Portfolio pillar shall pin it to its default branch's newest commit, record its licence, and list its items (plugins from `.claude-plugin/marketplace.json`, skills from `SKILL.md` folders, agents and commands) with their descriptions and the files each would run (hooks, scripts), skipping other harnesses' copies and test data. | draft |
| REQ-000000503 | **Toolkit installs are recorded in the project's lock and removed exactly.** When toolkit items are installed into a project, the system shall install a plugin with Claude Code's own project-scope plugin commands and copy a skill, agent or command at the pinned commit into the project's `.claude/`, record each in `.claude/toolkit.lock.json` (source, commit, licence, items, files), never overwrite a file of the project's own, and on removal delete exactly the recorded files or uninstall the plugin. | draft |
| REQ-000000504 | **Toolkit items that run code are installed only when confirmed.** When a toolkit install or update includes files that run code, the Registry & Portfolio pillar shall refuse it without `confirm_code` and list those files (hooks, scripts, executables). | draft |
| REQ-000000505 | **Default toolkit sources and recommendations, never installed on their own.** The Registry & Portfolio pillar shall register the default toolkit sources (mattpocock/skills, DietrichGebert/ponytail, BayramAnnakov/claude-reflect, pbakaus/impeccable) on request, and recommend items per project from its kind (code projects: the selected mattpocock skills and ponytail's skills; projects with a UI: impeccable; Unity projects: none) and its usage findings, without installing anything. | draft |
| REQ-000000506 | **Generated settings enable the plugins the toolkit lock records.** When little-workshop generates or syncs a project's `.claude/settings.json`, the system shall enable the code-intelligence plugins of its languages and every `plugin@marketplace` its `.claude/toolkit.lock.json` records, and keep the plugins the project enabled itself. | draft |

## Windows-drive projects as an explicit host (2026-11-07)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000509 | **A project's host is a Registry field.** The Registry & Portfolio pillar shall keep a host per project, `wsl` or `windows`, set at bootstrap and import to `windows` when the checkout lies on a Windows drive under WSL's mount root (`[automount] root` in /etc/wsl.conf, `/mnt/` by default) and to `wsl` otherwise, and shall set it from the path for every project registered before the field existed. | draft |
| REQ-000000511 | **A Windows-hosted project's line endings are pinned.** When a Windows-hosted project is bootstrapped or imported or has its Claude files synced, the Registry & Portfolio pillar shall set core.autocrlf=false in its checkout and, unless it is a Unity project, make "* text=auto eol=lf" the first rule of its .gitattributes (written at bootstrap, on the sync's improvement branch otherwise); and when the user runs `lws project normalize-eol --project-id <id>`, it shall do the same and renormalize every tracked file's line endings once, committed on an improvement branch of its own. | draft |
| REQ-000000513 | **A Windows-hosted project's hooks run in WSL and on Windows, failing closed.** While a project's host is `windows`, the generated Claude Code hook commands shall run their scripts with `sh` through `.claude/hooks/run_hook.sh`, which shall use the first of `python3`, `py -3` and `python` that runs; when none runs, the scope guard hook shall exit 2 saying "little-workshop guard unavailable" and the other hooks shall exit 0 with a notice; and the project's `.mcp.json` asset-review entry shall start `lws` as `cmd.exe /c lws.cmd`. A project hosted in WSL shall keep its `python3` hook commands and `lws` entry. | draft |

## Phase 6: closing the gaps (2026-11-07)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000519 | **A bootstrap can start an interview that fills docs/design.md.** When `project.bootstrap` runs with `interview: true` (a Dashboard checkbox, `lws project bootstrap --interview`, the MCP tool's `interview`), the Registry & Portfolio pillar shall, after scaffolding and version tracking, start an `agent.run_task` Job of intent `interview` with manual review and report its Job id (or why it did not start, without failing the bootstrap); and that task's prompt shall have Claude interview the owner one question at a time over Remote Control, with the project's `grill-me` skill when installed and plain questions otherwise, write docs/design.md from the answers, and write requirements only when the project's governance is `full`. | draft |
| REQ-000000520 | **An import syncs the Claude files and runs a preflight.** When `project.import` has registered a project, the Registry & Portfolio pillar shall submit a `registry.sync_claude_files` Job for it with `write: true` and run the policies gating `branch_finish` for it (Policy & Compliance's evaluate, nothing held), and shall return both outcomes in the import Job's result (`sync`, `preflight` with the blocking topics and each verdict), reporting an unreachable pillar there without failing the import. | draft |
| REQ-000000524 | **Backlog entries have a status and a priority the owner sets.** The Registry & Portfolio pillar shall keep a status (open, done, dropped) and a priority (high, normal, low) on each backlog entry, open and normal for an entry made before them, set through its Pillar Interface (PATCH), `lws project backlog update` and the `workshop_project_backlog_update` MCP tool, and shall list entries highest priority first, filtered by status (`lws project backlog list --status`, default open); the Dashboard's project backlog page shall filter by status (open by default) and sort by priority; a task launched from an entry shall never change its status, and when the run of the task last launched from an open entry succeeded (awaiting finish or finished, completion done), the entry's page shall offer "Mark done". | draft |
| REQ-000000532 | **A Job's own branch start runs and is waited for.** When a Registry & Portfolio Job that holds a project's queue (toolkit install, remove or update, normalize-eol, a Claude files sync) starts an improvement branch for that project, the system shall submit the `version.improvement_start` Job without the project's queue key and shall wait for it to settle before writing, so the branch start neither queues behind the Job that asked for it nor is read as failed while still queued. | done, superseded by REQ-000000534 |
| REQ-000000533 | **Toolkit items install together in one commit straight on the integration branch.** When a toolkit install or removal or update is applied to a project, the Registry & Portfolio pillar shall apply every selected item from any number of sources as one commit straight on the project's integration branch without starting or finishing a branch, shall write in a temporary worktree of that branch when the project's checkout is on another branch and leave that checkout untouched, shall refuse while the integration branch's checkout has uncommitted changes under `.claude/`, shall commit only the paths it wrote, and shall leave `.claude/` as it was when the change fails. | done |
| REQ-000000534 | **A Job's own branch start runs and is waited for.** When a Registry & Portfolio Job that holds a project's queue starts an improvement branch for that project, the system shall submit the `version.improvement_start` Job without the project's queue key and shall wait for it to settle before writing, so the branch start neither queues behind the Job that asked for it nor is read as failed while still queued (normalize-eol and the Claude files sync start such branches; toolkit changes do not, REQ-000000533). | done |
| REQ-000000535 | **The toolkit installs MCP servers into a project's .mcp.json.** When toolkit items include an MCP server, the Registry & Portfolio pillar shall list every server of a GitHub source's `.mcp.json` files and the servers a source is registered with (a repository outside GitHub only with its declared servers and a version, never fetched), treat each server as code that needs `confirm_code`, add it to the project's `.mcp.json` beside the project's own servers without replacing a server it does not own, record it in the toolkit lock, and on removal take out exactly that entry. | done |
| REQ-000000536 | **A repository that is one skill installs, and sources never overwrite each other's files.** When a toolkit source has its `SKILL.md` at the repository root, the Registry & Portfolio pillar shall offer the repository as one skill named by its frontmatter and install its files under `.claude/skills/<name>/`, and shall refuse any install that would overwrite files another source installed. | done |
| REQ-000000540 | **git and gh work without prompts.** The Registry pillar shall generate a `.claude/settings.json` for every project that pre-allows `git` and `gh` commands, `lws job stop|answer|policy-confirm`, `lws project connect-github <this project>` and the MCP tools `workshop_policy_confirm`, `workshop_job_stop`, `workshop_job_answer` and `workshop_project_connect_github` (which the HTTP gateway still refuses), and a guard that refuses only: a merge or cherry-pick while dev, master, main or production is checked out, a tag, a force push, `--mirror` or a deletion targeting one of those branches, deleting one of those branches locally, --no-verify, a core.hooksPath override, `gh pr merge`, `gh release`, `gh repo create`, `gh repo delete`, and the systemd, publishing and destructive commands it refused before, each refusal naming the route. | done |
| REQ-000000542 | **Short final reports.** The Registry pillar shall generate a shared briefing `.claude/little-workshop.md` that tells every session to end with a short report: what was done and how, plus only what the user must act on, with no option menus, no "what next" and no offers of further work unless that step was part of the agreed plan. | done |
| REQ-000000543 | **Workspace sessions read every project.** The Registry pillar's workspace sync (`tools/sync_workspace_claude.py`) shall pre-allow reading every registered checkout (`Read(//<checkout>/**)`) in the little-projects root session's settings and in little-workshop's own `.claude/settings.local.json`, without adding the checkouts as working directories. | done |
| REQ-000000544 | **The root writes in a project as that project's own session.** The Registry pillar's workspace sync shall pre-allow writes in every registered checkout in the little-projects root session and register a root guard that hands every write in a checkout, and every command that starts with `cd` into one, to that project's own guard. | done |
| REQ-000000545 | **little-workshop's writes in other projects are judged by their nature.** While the guard runs in little-workshop's own session (`--platform`), the guard shall allow, without a prompt, the `lws` and MCP calls that read another project or apply little-workshop in it (`version install-hooks`), shall refuse a hand edit of a file little-workshop generates in another project with the sync command as the route, and shall turn any other write or call acting on another project into a prompt that names what kind of change it is. | done |
| REQ-000000546 | **The root works in a project through a resumed sub-session.** When the little-projects root session runs `tools/project_session.py <project>`, the tool shall run the task in a headless Claude sub-session started in that project's checkout (the Windows `claude.exe` for a Windows-hosted project, edits accepted), shall resume the same sub-session on later calls until `--new` or `--forget`, shall allow one turn per project at a time, and shall print a report with the sub-session's final message, its id and its permission denials. | done |
| REQ-000000548 | **Skill scripts run on Windows-hosted checkouts and are pre-allowed.** Where a project is hosted on Windows, the Registry pillar shall generate the policy-preflight and policies-digest skills so that they run their scripts through `.claude/hooks/run_hook.sh`, and shall pre-allow both scripts in every project's settings in both the `python3` and the hook-runner form. | done |
