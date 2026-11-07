# Agent task standard

Source: `src/agent/templates/prompts/` (`common.md`, `requirements.md`, `task.md`, `bug.md`, `milestone.md`,
`docs.md`, `skill.md`, `interview.md`, `branching_auto.md`, `branching_specified.md`, `ui_playwright.md`, `ui_playwright_mcp.md`;
`update_docs.md` exists only on branch `feature/doc-review`) and `src/agent/flow.py`.

Applies to every unattended Claude run launched by Agentic Development
(`claude -p --dangerously-skip-permissions`, under Remote Control).

## What the run is given

Project id and checkout path, task title, intent (`task`, `bug`, `milestone`, `docs`, `skill`, `interview`), the task
description, the ground rules, branching instructions and the report path. Two sections are given only
when they apply:

- **UI and UX (Playwright)**, with the Playwright MCP server, only for a project with a UI: one with a server
  manifest (`manifest.deployment.toml`) or a `tests/ui/` folder.
- **Shared project standards**, a temporary read-only copy of `shared_project_standards/` (the root Markdown
  files plus its own project's folder, deleted when the run completes), only for a `docs` task or one whose
  title or description mentions standards, and never when the task was started with the standards option
  off. Read what applies; never edit or commit it. Any other task is **not** given these standards: name
  them in the task description when they matter.

## Ground rules

1. Follow the project's own `docs/*.md` conventions (design, architecture, requirements,
   milestones) and this folder.
2. **Definition of done** for milestone-shaped work: docs updated, code complete, automated tests
   written, the project's test suite green. Never claim `done` with any part missing.
3. Commit as you go. The project's commit-msg hook refuses a commit that adds a secret, printing the
   fix; the agent fixes it and commits again, never bypassing the hook.
   **Do not push**; do not create PRs, comments or labels.
4. Never delete or rewrite files unrelated to the task. When ambiguous, choose the most reasonable
   interpretation and state the assumption in the summary.
5. Work on the branch the run is already on, in the worktree little-workshop made for it (the live
   checkout is never the run's; a Windows-hosted Unity project is the exception): the specified branch, or
   `feature/agent-<title>` (milestone) / `improvement/agent-<title>` (everything else) from the integration
   branch.
6. Ask the owner (user-messaging tool) before a real technical decision or a large change; continue
   on best judgement if no answer arrives. Never block.
7. **Only for a project of `full` governance** (`requirements.md`, ground rule 8): create, update or
   supersede the requirements the change affects, tag the tests with `# --- REQ-nnnnnnnnn: ... ---`
   sections, and cite the ids (or `REQ-NONE`, never both) in commit messages; the finish lists them in
   the squash commit. A `light` project's prompt asks for no requirement work.

## Intent behaviour

- **task**: do what a careful colleague would; matching tests and docs; run the suite.
- **bug**: reproduce with a failing test (tagged with its requirement under rule 7), root-cause fix, whole suite green, update
  `architecture.md` (and `design.md` if a decision changed), state the root cause in the summary.
- **milestone**: read `design.md`, `architecture.md` and the milestone row; under rule 7, create
  missing requirements with real ids; implement, with the docs as context, not a limit (what the task asks wins,
  and the docs change with it); test; mark the row's status honestly.
- **skill**: draft one Claude Code skill in `.claude/skills/<name>/SKILL.md` (frontmatter `name` and a
  one-line `description` of when to use it, then short steps) from the evidence the description gives, using the
  project's routes; a script beside it only when it saves real work; no change outside that folder and no edit of
  generated files; nothing run for real. Started by the Usage pillar's "Draft as a skill" (improvement branch,
  manual review).
- **interview**: the bootstrap interview of a new project (`interview: true` on `project.bootstrap`, manual
  review): ask the owner one question at a time through the user-messaging tool and wait for each answer (rule 6's
  "never block" does not apply to these questions), with the `grill-me` skill when the toolkit installed it; write
  `docs/design.md` from the answers (purpose, users, concepts, scope, decisions, first milestone, open questions),
  requirements in `docs/requirements.md` only for `full` governance; no code.
- **docs**: edit the relevant files under `docs/` (a task description can name another folder, as
  this one does for `shared_project_standards/`); `design.md` for concepts and decisions, `architecture.md` for
  implementation, `milestones.md` for rows; no duplication; no code changes beyond a tiny forced fix.

## Doc-review runs

(Source: `update_docs.md` on `feature/doc-review`, not in this checkout's `dev`.)

Work comment by comment, each applying only to its highlighted section; certified sections stay
as they are (see [documentation_standard.md](documentation_standard.md)) unless a comment asks for what
one contradicts, where the comment wins. Commit on the current HEAD,
do not push or switch branches, and write the JSON result file last: it signals completion.

## Finish

1. Write the change report JSON at the path given (outside the repo; never commit it) with `status`,
   `background`, `goal`, `technical_decisions`, `executed_tasks`, `test_results`. It becomes the PR
   description; write for the project owner.
2. End the answer with a short summary, then as the very last line exactly one of
   `AGENT_STATUS: done`, `AGENT_STATUS: partial`, `AGENT_STATUS: failed`. A missing marker counts as
   `partial`; with auto-approve on, `done` merges the branch, so use it only for work that would
   survive review.

## Writing a good task description

State the project, what to read, what is out of scope or untouchable, what to create or improve, the
required standards (point at `shared_project_standards/`), and when the task is done. Name the
checkout location for a project that lives on the Windows side.

## Discrepancy notes

- The doc-review section above is compiled from `feature/doc-review`; on `dev` there is no
  `update_docs.md`.
- The template `docs.md` says "No code changes unless a doc change strictly requires a tiny
  accompanying fix"; the Update Standards task further forbids edits outside
  `shared_project_standards/`. The task description is the narrower rule.

Last reviewed: 2026-11-07 (hand edit: requirement work only for full governance, ground rule 7). Before
that 2026-10-24 (hand edit: docs are context, not a limit; a comment outranks a certified
section). Before that 2026-10-02 (hand edit: commit-time policy checks, mirroring
`src/agent/templates/prompts/common.md`).
