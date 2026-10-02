---
paths:
  - "src/registry/claude_templates.py"
  - "src/registry/scaffold.py"
  - "src/registry/unity_templates.py"
  - "src/cli/main.py"
  - "src/mcp_server/tools.py"
  - ".claude/**"
---
# Claude Code configuration

- `src/registry/claude_templates.py` generates the `.claude/` files `project.bootstrap` scaffolds into every
  project: `settings.json` (the routing and permission table), the scope guard hook, the `policy-preflight`
  skill and the `AGENTS.md` briefing. This repository's own `settings.json`, scope guard and skill are
  `claude_files("little-workshop", "python", **OWN_SETTINGS)`; a unit test fails when they drift, so change the
  generator and regenerate, never the copies. `.claude/hooks/live_install_guard.py`, the rules, the subagents and
  `AGENTS.md` are this repository's own files.
- Every MCP tool and every `lws` command is classified exactly once in `claude_templates.py` (read-only,
  routine, gated, ask, platform, unlisted); tests check the lists against the live catalogs, so a new tool or
  command is classified in the same change.
- A project keeps its guidance in `.claude/AGENTS.md` and never has a `CLAUDE.md`, which would make Claude Code
  skip `AGENTS.md` (policy `claude-config-present`).
- `AGENTS.md` is a briefing loaded into every session: guidance for one part of the tree belongs in a
  `.claude/rules/` file with `paths:` frontmatter, a procedure in a skill, and a rule that must always hold in a
  hook or a policy.
