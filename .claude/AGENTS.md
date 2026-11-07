# little-workshop

<!-- The briefing every session loads: keep it short. Path-scoped guidance goes in .claude/rules/, procedures in
skills, hard rules in hooks or policies. Never add a CLAUDE.md: Claude Code then skips this file. -->

Self-hosted daemon: eleven pillars (`src/<pillar>`), each a process on a Unix socket; `lws`, the Dashboard and the
MCP server are thin proxies over them. Python >=3.11, FastAPI, Bazel. little-workshop is its own project
(`little-workshop`, governance `full`), so its policies apply here. Read `docs/design.md`, `docs/architecture.md`,
then the `docs/pillars/` spec of the area you touch.

## Commands

- Tests: `python3 -m pytest tests/unit` (network-free; the `test-runner` subagent keeps the output short). Never
  `tests/integration` or `bazel test //:test` beside the live install: their fixtures stop the live pillars.
- Install from source: `bazel run //:install`. Pillars are `systemd --user` units, redeployed with
  `workshop_deployment_redeploy_pillars`.
- `tools/sync_policies.py` and `tools/sync_claude_files.py --checkout <dir>` show what differs from the
  generated policies and Claude files; `--apply` / `--write` change it.

## Working here

- Branch from `dev` (`feature/` for milestones, `improvement/` otherwise); `master` is production. An agent run
  follows its task's branching.
- Branch and policy state: `workshop_version_status`, `workshop_policy_results`, not git.
- A bug fix starts with a failing test, then the root-cause fix, then the whole unit suite green.
- `.claude/rules/` load with the files they cover. Subagents: `test-runner`, `standards-reviewer`,
  `pillar-researcher`.

@little-workshop.md

@../shared_project_standards/little_workshop_standards/README.md
