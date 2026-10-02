# little-workshop

<!-- A briefing, loaded into every session: keep it short and about this project. Guidance for one part of the
tree goes in .claude/rules/ with `paths:` frontmatter (it loads when Claude reads a matching file), procedures go
in .claude/skills/, and a rule that must hold every time goes in a hook or a little-workshop policy, not in a
sentence here. -->

Self-hosted daemon that governs a project's lifecycle through ten pillars (`src/<pillar>`), each its own process
on a Unix socket; the `lws` CLI, the Dashboard and the MCP server are thin stateless proxies over them. Python
>=3.11, FastAPI, Bazel. little-workshop is registered in its own system (project id `little-workshop`) and is the
first user of every pillar, so its own policies apply to this repository too. Read `docs/design.md` (concepts) and
`docs/architecture.md` (implementation), then `docs/pillars/` for the area you touch.

This file is `.claude/AGENTS.md` on purpose: Claude Code reads `AGENTS.md` (also as `.claude/AGENTS.md`) only
where a project has no `CLAUDE.md`, so never add a `CLAUDE.md` here, or this file stops being loaded.

## Lifecycle goes through little-workshop

Branch finishes, releases, deploys and publishes go through the `little-workshop` MCP server (tools
`workshop_<pillar>_*`, registered at user scope by `bazel run //:install` as `lws mcp serve`) or `lws`, because
that is where policies are checked. `.claude/settings.json` denies the raw git, systemd and twine forms.

| To do this | Use |
|---|---|
| Start / finish a feature, improvement, hotfix | `workshop_version_<type>_start` / `_finish` (never `git merge`) |
| Cut or finish a release, tag | `workshop_version_release_*`, `workshop_version_release_candidate_*` |
| Redeploy one of little-workshop's own pillars | `workshop_deployment_redeploy_pillar` (never `systemctl --user`) |
| Publish a package | `workshop_build_publish_pypi` |
| Record a Definition-of-Done test run | `workshop_requirements_run_tests`, `test_command` `["python3","-m","pytest","tests/unit"]` |
| See what a gated action would hit, and why | the `policy-preflight` skill (`workshop_policy_evaluate`) |
| Know the current branch or policy state | `workshop_version_status`, `workshop_policy_results` (not reconstructed from git) |

The daemon may be down (`workshop_core_status`): say so rather than falling back to raw git for a gated action.
In agent runs (`claude -p`) little-workshop does the branching; follow the task's branching instructions.

## What enforces the rules

The repository's rules are Policy & Compliance policies (scripts in `tools/policy_scripts/`) and hooks, so they
are checked rather than remembered:

- Every commit: `.githooks/pre-commit` writes `VERSION` on `feature/` and `improvement/` branches, and
  `.githooks/commit-msg` runs the policies gating `commit` (VERSION raised and well-formed, `REQ-nnnnnnnnn` or
  `REQ-NONE` cited when `src/` changes) and refuses a commit that breaks one, printing the rule and the fix.
  Fix it and commit again; `--no-verify` is denied.
- A branch finish, release or publish: little-workshop runs the policies gating it and holds the action in
  `pending_confirmation` on a failure. Run the `policy-preflight` skill first; if the action is still held,
  report it with `workshop_policy_results` rather than working around it.
- This session's tool calls: `.claude/hooks/lws_scope_guard.py` denies `lws` and MCP calls that name another
  project, and `.claude/hooks/live_install_guard.py` denies test runs that would reach `tests/integration` (its
  fixtures stop the live pillars).
- Decisions that stand in for a human (`workshop_policy_confirm`, `workshop_agent_finish_task`/`skip_task`/
  `run_task`, `workshop_policy_projects_set`), scheduling, project registration and `bazel run //:install` prompt.

## Scope: this project only

A session here works on `little-workshop` only: `lws` is pre-allowed as `lws ... --project-id little-workshop`,
and the scope guard denies anything naming another project. Work on other projects, and registering or removing
them, belongs in a session started from `little-projects`. As little-workshop's own project this session may
administer little-workshop itself (author, activate and configure policies, `workshop_deployment_redeploy_pillar`).

## Working here

- Tests: `python3 -m pytest tests/unit` (network-free; the Definition-of-Done suite). Full suite and coverage gate
  (`bazel test //:test`, `//:coverage`) include `tests/integration`: run those only where no live install runs.
  `tests/ui` is Playwright. Use the `test-runner` subagent to keep long output out of this conversation.
- Install `lws` and the pillars from source: `bazel run //:install`. Pillars are `systemd --user` units; the
  state home is `~/.config/little-workshop`.
- Branch from `dev` (`feature/` for milestones, `improvement/` otherwise); `master` is production. Never push,
  open pull requests or force-push unless asked.
- Behaviour changes ship with tests tagged with requirement ids (`# --- REQ-nnnnnnnnn: ...` sections). A bug fix
  starts with a failing test, then the root-cause fix, then the whole unit suite green.
- Other projects' servers (little-meals, little-mindcheck, little-home) run on demand; down is normal.

Path-scoped rules in `.claude/rules/` cover tests, policies, versions, the Claude templates, agent prompts, the
job catalog and the docs; they load when you read files there. Subagents: `test-runner` (unit suite, failures
only), `standards-reviewer` (a diff against these rules), `pillar-researcher` (how a pillar works).

## Project conventions

@../shared_project_standards/little_workshop_standards/README.md
