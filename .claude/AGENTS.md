# little-workshop

<!-- A briefing, loaded into every session: keep it short and about this project. Guidance for one part of the
tree goes in .claude/rules/ with `paths:` frontmatter (it loads when Claude reads a matching file), procedures go
in .claude/skills/, and a rule that must hold every time goes in a hook or a little-workshop policy, not in a
sentence here. This file is .claude/AGENTS.md on purpose: Claude Code reads AGENTS.md only where the project has
no CLAUDE.md, .claude/CLAUDE.md or CLAUDE.local.md, and the guard refuses those. `@little-workshop.md` imports the
section every project shares (generated: .claude/little-workshop.md). -->

Self-hosted daemon that governs a project's lifecycle through eleven pillars (`src/<pillar>`), each its own process
on a Unix socket; the `lws` CLI, the Dashboard and the MCP server are thin stateless proxies over them. Python
>=3.11, FastAPI, Bazel. little-workshop is registered in its own system (project id `little-workshop`) and is the
first user of every pillar, so its own policies apply to this repository too. Read `docs/design.md` (concepts) and
`docs/architecture.md` (implementation), then `docs/pillars/` for the area you touch.

## Commands

- Tests: `python3 -m pytest tests/unit` (network-free; the Definition-of-Done suite, recorded with
  `workshop_requirements_run_tests` and `test_command` `["python3","-m","pytest","tests/unit"]`). The full suite
  and coverage gate (`bazel test //:test`, `//:coverage`) include `tests/integration`, whose fixtures stop the live
  pillars, so `.claude/hooks/live_install_guard.py` refuses them beside the live install. `tests/ui` is Playwright.
  The `test-runner` subagent keeps long output out of this conversation.
- Install `lws` and the pillars from source: `bazel run //:install` (it prompts). Pillars are `systemd --user`
  units, redeployed one at a time with `workshop_deployment_redeploy_pillar`; the state home is
  `~/.config/little-workshop`.
- Generated files: `python3 tools/sync_policies.py` (policies against the live pillar) and
  `python3 tools/sync_claude_files.py --checkout <project>` (a project's generated Claude files) show what
  differs; `--apply` / `--write` change it.

## Working here

- Branch from `dev` (`feature/` for milestones, `improvement/` otherwise); `master` is production. In agent runs
  (`claude -p`) little-workshop does the branching: follow the task's branching instructions.
- Read the branch and policy state from `workshop_version_status` and `workshop_policy_results`, not from git.
- Behaviour changes ship with tests tagged with requirement ids (`# --- REQ-nnnnnnnnn: ...` sections). A bug fix
  starts with a failing test, then the root-cause fix, then the whole unit suite green.
- Other projects' servers (little-meals, little-mindcheck, little-home) run on demand; down is normal.

Path-scoped rules in `.claude/rules/` cover tests, policies, versions, the Claude templates, agent prompts, the
job catalog, the docs and the instruction files; they load when you read files there. Subagents: `test-runner`
(unit suite, failures only), `standards-reviewer` (a diff against these rules), `pillar-researcher` (how a pillar
works).

@little-workshop.md

## Project conventions

@../shared_project_standards/little_workshop_standards/README.md
