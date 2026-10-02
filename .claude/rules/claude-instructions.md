---
paths:
  - ".claude/**"
  - "**/AGENTS.md"
---
# Claude instructions in this project

Where an instruction lives decides when it loads, so put each one where it is needed:

- The briefing (`.claude/AGENTS.md`, or a root `AGENTS.md`) is read at the start of every session: what the
  project is, the commands Claude cannot guess, its pitfalls and where to start. `@path` imports load at launch
  too, so they organise the briefing without making it cheaper.
- Guidance for one part of the tree is a `.claude/rules/<topic>.md` file whose `paths:` frontmatter lists glob
  patterns; it loads when Claude reads a matching file. `paths` is the only field Claude Code reads there.
- A procedure, above all one with a script, is a skill: `.claude/skills/<name>/SKILL.md`, whose `description`
  says when to use it, with its scripts under `scripts/`. It loads when the task calls for it.
- A rule that must hold every time is a hook (`.claude/hooks/`, registered in `.claude/settings.json`) or a
  little-workshop policy (the commit hook and the gates run them), not a sentence: a sentence is advice.

When changing them, update or delete an existing line before adding one, and add only what the code does not
show (commands, pitfalls, reasons, conventions that differ from the defaults), checked against the repository.
The policy `claude-instructions-valid` fails an instruction file over 200 lines, more than 250 lines
loaded at session start, an `@` import that does not resolve, a rule pattern that matches no file, and a skill
or subagent without a description.

little-workshop generates `.claude/little-workshop.md`, this rule, the `policy-preflight` and
`workshop-lifecycle` skills, `.claude/hooks/lws_scope_guard.py` and the permission table and guard hook in
`.claude/settings.json` (entries the project adds beside them are kept). Change them in little-workshop's
`src/registry/claude_templates.py` and rewrite them with its `tools/sync_claude_files.py`; the guard refuses hand
edits. Never add a `CLAUDE.md`, `.claude/CLAUDE.md` or `CLAUDE.local.md`: Claude Code then stops reading
`AGENTS.md`.
