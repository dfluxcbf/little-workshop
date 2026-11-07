Draft the Claude Code skill described above, for this project's own sessions:

1. Write `.claude/skills/<name>/SKILL.md` with the name the description gives: frontmatter `name` and a one-sentence `description` that says when to use the skill (Claude reads only that line to decide), then the steps, short and imperative.
2. Base the steps on the evidence given (the calls the sessions repeated, in their order); use the project's own routes (little-workshop MCP tools, `lws`, its scripts) rather than raw commands, and say where judgment is needed.
3. Put a deterministic step in a script beside it (`.claude/skills/<name>/scripts/`) only when it saves real work; a skill of instructions alone is fine.
4. Never edit the files little-workshop generates (`.claude/settings.json`, `.claude/little-workshop.md`, its hooks and skills); no change outside `.claude/skills/<name>/`.
5. Do not run the skill's actions for real. In the summary, say what the skill does, when Claude should use it, and what you left out. The owner reviews the draft on its branch.
