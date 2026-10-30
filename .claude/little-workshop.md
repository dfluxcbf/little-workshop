## little-workshop

This project is registered in little-workshop as `little-workshop`, which runs its branches, releases, deploys and
publishes and checks its policies at each step. Use the `little-workshop` MCP tools (`workshop_<pillar>_*`) or
`lws`, naming the project every time (`project_id` `little-workshop`, `--project-id little-workshop`); the
`workshop-lifecycle` skill has the routes and the steps before a finish.
As little-workshop's own project, this session also administers little-workshop: it authors, activates
and configures policies and redeploys the pillars; registering projects belongs in `little-projects`.

These are checked, not remembered. When one refuses something, its message says what to do instead:

- Every commit: `.githooks/pre-commit` writes `VERSION` on `feature/` and `improvement/` branches, and
  `.githooks/commit-msg` runs the policies gating `commit` (`VERSION` raised and well-formed, a `REQ-nnnnnnnnn`
  or `REQ-NONE` citation when source changes, no committed secrets) and refuses a commit that breaks one.
- A branch finish, release, deploy or publish: little-workshop runs the policies gating it and holds a failing
  action in `pending_confirmation`. Run the `policy-preflight` skill first, and report a held action rather
  than working around it.
- This session: `.claude/hooks/lws_scope_guard.py` refuses the raw git, systemd and publishing commands that
  would skip those checks, calls that name another project, and hand edits of what little-workshop generates
  (`VERSION`, `.githooks/`, its `.claude/` files) or of a `CLAUDE.md`.
- Decisions that stand in for a human (confirming a held action, finishing or skipping an agent task,
  scheduling) always prompt.
