## little-workshop

This project is registered in little-workshop as `little-workshop`, which runs its branches, releases, deploys and
publishes and checks its policies at each step. Use the `little-workshop` MCP tools (`workshop_<pillar>_*`) or
`lws`, naming the project every time (`project_id` `little-workshop`, `--project-id little-workshop`); the
`workshop-lifecycle` skill has the routes and the steps before a finish.
As little-workshop's own project, this session also administers little-workshop: it authors, activates
and configures policies and redeploys the pillars; registering projects belongs in `little-projects`.

The user's newest request outranks what was decided before. Design docs, house settings in instruction
files, Claude's memories, requirement rows and tests that pin a look record earlier choices, often
simplifications made for that stage; none is a reason to do less than, or other than, what the user
now asks. When one conflicts, follow the request and change the old item in the same work (edit the doc,
supersede or remove the requirement, update the test, rewrite the memory), and say what you changed.
Read the request and the files it names in full. When it is unclear, ask; never settle it with an
earlier decision.

These are checked, not remembered. When one refuses something, its message says what to do instead:

- Every commit: `.githooks/pre-commit` writes `VERSION` on `feature/` and `improvement/` branches, and
  `.githooks/commit-msg` runs the policies gating `commit` (`VERSION` raised and well-formed,
  no committed secrets, no test left citing a retired requirement) and refuses a commit that breaks one.
- A branch finish, release, deploy or publish: little-workshop runs the policies gating it and holds a failing
  action in `pending_confirmation`. Run the `policy-preflight` skill first, and report a held action rather
  than working around it.
- This session: `.claude/hooks/lws_scope_guard.py` refuses the raw git, systemd and publishing commands that
  would skip those checks, calls that name another project, and hand edits of what little-workshop generates
  (`VERSION`, `.githooks/`, its `.claude/` files) or of a `CLAUDE.md`.
- Decisions that stand in for a human (confirming a held action, finishing or skipping an agent task,
  scheduling) always prompt.
