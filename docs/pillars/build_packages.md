# Build & Packages

**Not yet built.** Scheduled as `docs/milestones.md`'s M11, depending on
M1 (Core + Orchestration), M2 (Registry & Portfolio), and M4 (Deployment
& Servers, for worktree-based build execution). This document describes
the design as currently specified — see `docs/architecture.md` and
`docs/milestones.md` for the authoritative source; nothing here has
shipped code behind it yet. The only pillar that's optional in the same
way Agentic Development and Dashboard are — a project with no build/
publish step never starts it.

## Description

Build & Packages makes real a non-goal `little-runner` explicitly
disclaimed ("not a general CI system"): build/test Job execution via
Deployment & Servers' worktrees, with fork-safety-gated PR testing. It
also absorbs `little-pypi` wholesale for the package-hosting half: PEP
503 + `.vsix` hosting, always-on now that little-workshop is already a
persistent daemon — `little-pypi`'s standalone 5-minute-session
compromise only ever existed because it had no persistent process to
live inside.

## Purpose & goal

"A build artifact exists, is reproducible, and is reachable by anything
that needs to install it" (`docs/architecture.md`).

## Responsibilities (as designed)

- **CI-shaped Job execution** — build/test runs in a worktree, the same
  provisioning mechanism Requirements & Quality's sandboxed test
  execution uses, so this pillar doesn't invent a second way to get an
  isolated checkout.
- **Fork-safety-gated PR testing** — the specific guard `little-runner`
  never built as a general capability, now real: a PR from a fork gets
  tested without handing it write access to anything.
- **Package hosting** — PEP 503 (a real Python package index) and
  `.vsix` (VS Code extension) hosting, `little-pypi`'s own model carried
  over unchanged in shape, dropping only the session-lifetime compromise
  that no longer applies.

## Interfacing (as designed)

- **Calls**: Registry & Portfolio (resolve a project's repository path);
  Deployment & Servers (worktree provisioning for build/test execution);
  Version Control & Release (the version a build artifact should be
  tagged/published under).
- **Called by**: `lws`, the MCP Server, and Orchestration for every
  build/publish Job type — and, indirectly, whatever CI-trigger source
  replaces GitHub Actions' role for a project using this pillar. Once
  Orchestration's scheduled tasks (M9) exist, a periodic build is just a
  recurring `ScheduledTask` naming this pillar's own build Job type.
- **Discovered through**: Core, like every pillar.
