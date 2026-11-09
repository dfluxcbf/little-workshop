# Toolkit sources

Repositories of Claude Code skills, agents and plugins for the toolkit (docs/pillars/registry_portfolio.md,
"Toolkit"). The four marked **default** are registered by `lws toolkit seed`; add another with
`lws toolkit add-source <owner/repo>` (an MCP server outside GitHub: `lws toolkit add-source host/owner/repo --mcp NAME='COMMAND' --version --licence`). Nothing is installed into a project until you choose it, per project.

| Repo | Licence | Use | Where |
|---|---|---|---|
| **mattpocock/skills** (default) | MIT | Selected skills: `grill-me`, `grill-with-docs` (intent capture), `to-spec`, `diagnosing-bugs`, `tdd`, `handoff`, `writing-for-agents`. Skip `git-guardrails` (the guard does it), `setup-pre-commit` and `implement-spec` (its own integration branch, merger subagents and pull requests compete with little-workshop's branch finishes). | All code projects |
| **DietrichGebert/ponytail** (default) | MIT | Its skills only (`ponytail-review`, `-audit`, `-debt`); never its hooks, statusline or band (per-turn injection costs tokens). | All code projects, little-workshop first |
| **BayramAnnakov/claude-reflect** (default) | MIT | Already installed at user scope: integrate rather than add (learned rules go to `.claude/rules/learned.md`, `/reflect-skills` for skill drafts). | User scope |
| **pbakaus/impeccable** (default) | Apache-2.0 | Critique, audit and polish of UI work. | Projects with a UI: the Dashboard, little-home, little-meals |
| cloudflare/security-audit-skill | MIT | On demand, for whole-repository audits (`/security-review` covers diffs). | little-home, little-meals |
| Leonxlnx/taste-skill | MIT | Overlaps impeccable, lighter (one skill): pick one per project. | Alternative to impeccable |
| OthmanAdi/planning-with-files | MIT | Not by default: per-turn re-injection adds tokens and overlaps plan mode. | Long unattended tasks only |
| Donchitos/Claude-Code-Game-Studios | MIT | Selected agents only (unity-specialist, unity-shader, unity-ui, game-designer), never all of it. | ethereal-lands, little-worlds |
| Unity-Technologies/skills | Unity Companion (Unity projects only) | The relevant skills (physics, navigation, tiles, localisation). | ethereal-lands, little-worlds |
| arjun988/blender-skills | MIT | 94 skills through Blender MCP (retopology, uv-workflow, hard-surface, plus style and genre skills); 2.2k stars, one author. | little-render (all, 2026-11-08); ethereal-lands assets |
| EL4CTEO/blender-skills | MIT | 28 script-backed skills for headless Blender 5.2: `blender-retopology-uv`, hard-surface, texture baking, asset QA. The strongest topology/UV content; young (2026-10), two authors. | little-render |
| ahujasid/blender-mcp (MCP for Blender) | MIT | MCP server `uvx mcp-for-blender` (from its `.mcp.json`) driving a live Blender; 33k stars, 46 contributors. Needs its add-on (`uvx mcp-for-blender install-addon`); sends telemetry unless `DISABLE_TELEMETRY=true`. | little-render |
| projects.blender.org/lab/blender_mcp (Blender Lab) | GPL-3.0 | The Blender Foundation's MCP server (Blender 5.1+), registered with its server pinned to v1.0.3 (not on GitHub, never fetched). Needs the MCP add-on from the `https://lab.blender.org/` extensions repository. Runs LLM code in Blender without safeguards. | little-render |
| achimala/dream-loop | MIT | The image and 3D side. | little-render |
