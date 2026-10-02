# little-workshop

Self-hosted daemon governing a project's lifecycle through ten pillars (`src/<pillar>`), each its own
process on a Unix socket. `lws` CLI, Dashboard and MCP server are thin stateless proxies over the
pillars. Python >=3.11, FastAPI, Bazel. Read `docs/design.md` and `docs/architecture.md` first, then
`docs/pillars/<pillar>.md` for the area you touch. Cross-project rules: `shared_project_standards/`
(its `little_workshop_standards/README.md` is the project-specific summary).

This file is `.claude/AGENTS.md` on purpose: Claude Code reads `AGENTS.md` (also as `.claude/AGENTS.md`) only
where a project has no `CLAUDE.md`, so never add a `CLAUDE.md` here, or this file stops being loaded.

## Commands

- Unit tests: `python3 -m pytest tests/unit` (network-free; the Definition-of-Done suite).
- Full suite: `bazel test //:test`; coverage gate (90%): `bazel test //:coverage`.
- UI tests: `tests/ui` (Playwright, relative URLs, `page` fixture).
- Install `lws` from source: `bazel run //:install`. Pillars are `systemd --user` units; state home is
  `~/.config/little-workshop`.

## Lifecycle goes through little-workshop itself (MCP server `little-workshop`)

This project is registered in its own system (project id `little-workshop`). The MCP server, registered
at user scope by `bazel run //:install` (`lws mcp serve`), exposes `workshop_<pillar>_*` tools that are
thin proxies for the same Pillar Interface calls as `lws`. Policies gate `branch_finish`, `release` and
`package_publish`, so use the tool, not the raw command (`.claude/settings.json` denies the raw forms):

| Instead of | Use |
|---|---|
| `git checkout -b feature/x` / merge to `dev` | `workshop_version_feature_start` / `_finish` (also `improvement_*`, `hotfix_*`) |
| `git tag`, hand-made release branches | `workshop_version_release_start`, `release_candidate_cut`, `release_finish` |
| `git push`, `gh repo create` | `workshop_project_connect_github` |
| `systemctl --user`, `systemd-run` for pillars | `workshop_deployment_redeploy_pillar`, `workshop_deployment_*` |
| `twine upload` | `workshop_build_publish_pypi` |
| pytest for the DoD report | `workshop_requirements_run_tests` (`test_command` `["python3","-m","pytest","tests/unit"]`) |
| guessing policy state | `workshop_policy_check_project`, `workshop_policy_gating_for_job` before finishing a branch |

`.claude/settings.json` pre-allows read-only tools (`*_list`, `*_status`, `*_get`, `*_report`), routine work
(branch starts, requirements, test runs) and the policy-gated actions (finish, release, deploy, publish): the
gate is the check. As little-workshop's own project it also pre-allows the platform administration that names
no project (policy author/activate/configure/remove/check-all/catalog-run, `workshop_deployment_redeploy_pillar`).
It prompts for decisions that stand in for a human (`workshop_policy_confirm`,
`workshop_agent_finish_task`/`skip_task`/`run_task`, `workshop_policy_projects_set`), scheduling,
project bootstrap/import/connect-github/remove, deletes, `requirements_publish`, `bazel run //:install` and
`bazel run //*_server`. Every MCP tool and every `lws` command must be classified in `claude_templates.py`
(tests enforce it against the live catalogs).
A policy failure leaves the action in `pending_confirmation`: report it, never bypass it. Prefer `workshop_version_status` and
`workshop_policy_results` over reconstructing state from git. The daemon may be down (`workshop_core_status`);
say so rather than falling back to raw git for gated actions. In agent runs (`claude -p`) branching is
done by little-workshop; follow the task's branching instructions.

## Scope: this project only

A session here works on `little-workshop` only. The settings pre-allow `lws` commands only as
`lws ... --project-id little-workshop`, and the hook `.claude/hooks/lws_scope_guard.py` denies an `lws` command
or MCP call that names another project (or leaves out the `project_id` of a tool where that means every
project). Work on other projects, and registering or removing them, belongs in a session started from
`little-projects`, which allows every `lws` command and every `little-workshop` tool.

## Bootstrapped projects

`project.bootstrap` scaffolds `.claude/settings.json` (the same table, bound to the new project's id, from
`src/registry/claude_templates.py`), `.claude/hooks/lws_scope_guard.py` and `.claude/AGENTS.md` for both layouts,
and no `CLAUDE.md`. Changing the routing table means updating `claude_templates.py` and regenerating this
repo's settings and guard from `claude_templates.claude_files("little-workshop", "python", **OWN_SETTINGS)`
(`OWN_SETTINGS`, this project's extras, is in `tests/unit/test_registry_claude_templates.py`); a unit test
fails when they drift.

## Rules that are easy to get wrong

- **Every commit raises `VERSION`** (policy `commit-bumps-version`). Run `tools/sync_versions.py` when it
  changes; never hand-edit the version in `pyproject.toml`.
- Commit subject style follows `git log` (`feat (-1): slug`, `impr`, `fix`); cite exactly one of
  `REQ-nnnnnnnnn` or `REQ-NONE`. Merges use `--no-ff`. Never push, open PRs or force-push unless asked.
- Branch from `dev` (`feature/` for milestones, `improvement/` otherwise); `master` is production.
- Behaviour changes ship with tests tagged with requirement ids. Bug fix = failing test first, then the
  root-cause fix, then the whole suite green.
- **Unit tests must never skip** inside a sandboxed worktree (no reliance on sibling projects). One skip or
  failure marks every requirement linked to the "Unit tests" suite as not done.
- Record DoD runs with `["python3","-m","pytest","tests/unit"]` only, never the default whole-`tests/` run.
  The run takes minutes and the MCP call can time out while the job continues: read the job status, don't retry.
- **Never run `tests/integration`, or a bare `tests`/`.` that pulls it in, beside the live install.** Its fixtures
  use the live `little-workshop-<pillar>.service` unit names and stop the live pillars (it did on 2026-09-21).
  Pass the explicit `tests/unit` path, for coverage runs too.
- User-triggerable jobs are typed `JobSpec` entries in `src/dashboard/job_catalog.py` (project, job, typed
  args). Never free-form or Claude-drafted.
- Changing `src/agent/templates/prompts/` changes every agent task: update
  `shared_project_standards/agent_task_standard.md` in the same change.
- Other projects' servers (little-meals, mindcheck, home) run on demand. Down is normal; don't restart them.
- Docs: no duplication between `design.md` (concepts), `architecture.md` (implementation) and
  `milestones.md` (status rows). Be honest about milestone status.

## Subagents (`.claude/agents/`)

- `test-runner`: runs the unit suite and reports only failures/skips.
- `standards-reviewer`: checks a diff against the rules above before commit.
- `pillar-researcher`: read-only lookup of how a pillar works, from docs and code.
