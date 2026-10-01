# Agent task standard

Source: `src/agent/templates/prompts/` (`common.md`, `task.md`, `bug.md`, `milestone.md`,
`docs.md`, `branching_auto.md`, `branching_specified.md`, `ui_playwright.md`, `ui_playwright_mcp.md`;
`update_docs.md` exists only on branch `feature/doc-review`) and `src/agent/flow.py`.

Applies to every unattended Claude run launched by Agentic Development
(`claude -p --dangerously-skip-permissions`, under Remote Control).

## What the run is given

Project id and checkout path, task title, intent (`task`, `bug`, `milestone`, `docs`), the task
description, the ground rules, branching and UI instructions, and the report path. It is **not**
given these standards unless the prompt points at them.

## Ground rules

1. Follow the project's own `docs/*.md` conventions (design, architecture, requirements,
   milestones) and this folder.
2. **Definition of done** for milestone-shaped work: docs updated, requirements created or updated,
   code complete, automated tests written, the project's test suite green. Never claim `done` with
   any part missing.
3. Commit as you go; each commit cites `REQ-nnnnnnnnn` or `REQ-NONE`, never both. **Do not push**; do
   not create PRs, comments or labels.
4. Never delete or rewrite files unrelated to the task. When ambiguous, choose the most reasonable
   interpretation and state the assumption in the summary.
5. Branch per the task: specified branch, or `feature/` (milestone) / `improvement/` (everything
   else) from the integration branch.
6. Ask the owner (user-messaging tool) before a real technical decision or a large change; continue
   on best judgement if no answer arrives. Never block.

## Intent behaviour

- **task**: do what a careful colleague would; matching tests and docs; run the suite.
- **bug**: reproduce with a failing test, root-cause fix, whole suite green, update
  `architecture.md` (and `design.md` if a decision changed), state the root cause in the summary.
- **milestone**: read `design.md`, `architecture.md` and the milestone row; create missing
  requirements with real ids; implement; test; mark the row's status honestly.
- **docs**: edit the relevant files under `docs/` (a task description can name another folder, as
  this one does for `shared_project_standards/`); `design.md` for concepts and decisions, `architecture.md` for
  implementation, `milestones.md` for rows; no duplication; no code changes beyond a tiny forced fix.

## Doc-review runs

(Source: `update_docs.md` on `feature/doc-review`, not in this checkout's `dev`.)

Work comment by comment, each applying only to its highlighted section; certified sections stay
as they are (see [documentation_standard.md](documentation_standard.md)). Commit on the current HEAD,
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

Last reviewed: 2026-10-01.
