# Toolkit sources

Repositories of Claude Code skills, agents and plugins for the toolkit (docs/pillars/registry_portfolio.md,
"Toolkit"). The four marked **default** are registered by `lws toolkit seed`; add another with
`lws toolkit add-source <owner/repo>`. Nothing is installed into a project until you choose it, per project.

| Repo | Licence | Use | Where |
|---|---|---|---|
| **mattpocock/skills** (default) | MIT | Selected skills: `grill-me`, `grill-with-docs` (intent capture), `to-spec`, `implement-spec`, `diagnosing-bugs`, `tdd`, `handoff`, `writing-for-agents`. Skip `git-guardrails` (the guard does it) and `setup-pre-commit`. | All code projects |
| **DietrichGebert/ponytail** (default) | MIT | Its skills only (`ponytail-review`, `-audit`, `-debt`); never its hooks, statusline or band (per-turn injection costs tokens). | All code projects, little-workshop first |
| **BayramAnnakov/claude-reflect** (default) | MIT | Already installed at user scope: integrate rather than add (learned rules go to `.claude/rules/learned.md`, `/reflect-skills` for skill drafts). | User scope |
| **pbakaus/impeccable** (default) | Apache-2.0 | Critique, audit and polish of UI work. | Projects with a UI: the Dashboard, little-home, little-meals |
| cloudflare/security-audit-skill | MIT | On demand, for whole-repository audits (`/security-review` covers diffs). | little-home, little-meals |
| Leonxlnx/taste-skill | MIT | Overlaps impeccable, lighter (one skill): pick one per project. | Alternative to impeccable |
| OthmanAdi/planning-with-files | MIT | Not by default: per-turn re-injection adds tokens and overlaps plan mode. | Long unattended tasks only |
| Donchitos/Claude-Code-Game-Studios | MIT | Selected agents only (unity-specialist, unity-shader, unity-ui, game-designer), never all of it. | ethereal-lands, little-worlds |
| Unity-Technologies/skills | Unity Companion (Unity projects only) | The relevant skills (physics, navigation, tiles, localisation). | ethereal-lands, little-worlds |
| arjun988/blender-skills | MIT | On demand: pick the few the asset pipeline uses. | ethereal-lands assets |
| achimala/dream-loop | MIT | The image and 3D side. | little-render |
