---
paths:
  - "src/registry/claude_templates.py"
  - "src/registry/scaffold.py"
  - "src/registry/unity_templates.py"
  - "src/cli/main.py"
  - "src/mcp_server/tools.py"
  - "tools/sync_claude_files.py"
---
# Claude Code configuration

- `src/registry/claude_templates.py` generates the `.claude/` files every project carries (`claude_files()`):
  `settings.json` (the routing and permission table and the guard hook), the guard `lws_scope_guard.py`, the
  shared briefing section `little-workshop.md` that each project's `AGENTS.md` imports, the rule
  `claude-instructions.md`, and the `workshop-lifecycle` and `policy-preflight` skills. `project.bootstrap`
  writes them into a new project; `python3 tools/sync_claude_files.py --checkout <project> --write` rewrites them
  in a registered one, merging the project's own settings entries back in. This repository's copies are
  `claude_files("little-workshop", "python", **OWN_SETTINGS)`; a unit test fails when they drift, so change the
  generator and rerun the sync, never the copies (the guard refuses hand edits of them).
- The guard is the hard layer of the instructions: it refuses a call naming another project, the raw commands in
  `DENY_REROUTED` (giving the `ROUTES` entry instead) and edits of generated files or a `CLAUDE.md`. A rule that
  must hold in every session goes there, with a test, rather than into a briefing.
- Every MCP tool and every `lws` command is classified exactly once in `claude_templates.py` (read-only,
  routine, gated, ask, platform, unlisted); tests check the lists against the live catalogs, so a new tool or
  command is classified in the same change.
- The policies `claude-config-present` and `claude-instructions-valid` (`tools/policy_scripts/`) check what this
  module writes: change them with it.
- `.claude/hooks/live_install_guard.py`, the other rules, the subagents and `AGENTS.md` are this repository's own.
