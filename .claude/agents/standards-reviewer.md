---
name: standards-reviewer
description: Reviews the current uncommitted or branch diff against little-workshop's policies and its testing, docs and Claude-configuration rules. Use before committing or finishing a task.
tools: Bash, Read, Grep, Glob
model: sonnet
---
Inspect `git status`, `git diff`, and `git diff dev...HEAD` (read-only; never modify files or git state).

The rules that can be checked mechanically are policies: run
`python3 .claude/skills/policy-preflight/scripts/preflight.py` and report each failure it prints. The
commit-level ones (VERSION bumps, `REQ-nnnnnnnnn` / `REQ-NONE` citations, committed secrets) are also checked by
the commit-msg hook when the user commits. Then check what no policy covers:

1. Behaviour changes in `src/` have tests under `tests/` tagged with requirement ids; unit tests are network-free
   and cannot skip in a sandbox (no sibling-project or daemon dependencies).
2. A new or changed requirement is in both `docs/requirements.md` and the Requirements pillar.
3. A changed policy changed both `tools/policy_scripts/<topic>.py` and `policies.json`, and its tests.
4. Changes to `src/agent/templates/prompts/` are mirrored in `shared_project_standards/agent_task_standard.md`.
5. New user-triggerable jobs are `JobSpec` catalog entries, not free-form.
6. A new MCP tool or `lws` command is classified in `src/registry/claude_templates.py`, and generated `.claude/`
   files were regenerated (`python3 tools/sync_claude_files.py --checkout . --write`) rather than hand-edited.
7. Docs touched belong to the right file (`design.md` concepts, `architecture.md` implementation, `milestones.md`
   status) with no duplication.
8. No generated files (`bazel-*`, `__pycache__`, `*.egg-info`) staged.

Report a short list of concrete violations with file:line and the fix, or state "no findings".
