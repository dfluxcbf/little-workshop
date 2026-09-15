← [The Catalog, Confirmed](18-catalog-confirmed.md) · deck 19 of 19 · back to [index](README.md)

# Importing What Was Already Proven

A final sweep across every project in this ecosystem for design content
worth carrying into little-workshop that hadn't been mined yet — the root
feature documents (`docs/features/github_integration.md`,
`server_management.md`), `branch_audit.py` and its triggering hook, and a
last check of every sibling project's own docs. Most of what's absorbed
here already existed, proven, in `little-runner` — it just hadn't been
written down at this project's level of detail yet. Resolved 2026-09-15.

## The big piece: Agentic Development, fully specified

Every other pillar with real depth (Policy & Compliance) had its own
`docs/pillars/*.md`. Agentic Development — despite absorbing the richest
single source in the whole ecosystem — never got the same treatment.
[`docs/pillars/agentic_development.md`](../docs/pillars/agentic_development.md)
fixes that:

- **Not every Job goes through this pillar** — the root feature docs
  draw a real line: issues (and by extension, agentic tasks) are for work
  with genuine judgment calls; a typed, deterministic action like
  bringing a server up dispatches directly, no agent, no report, no
  review. Getting this wrong either direction has a real cost.
- **BranchingSpec**, inherited exactly: new/existing/auto, base
  validated against the branch type before the agent starts, the agent
  hard-forbidden from touching branches whenever branching was specified.
- **The completion marker** — `done`/`partial`/`failed`, missing always
  degrading to `partial`, never `done`. The one signal an automatic
  finish is allowed to trust.
- **Two review modes** — manual (default, explicit finish later) and
  auto-approve (still produces the full report, still refused if the
  agent didn't report success or its own tests failed).
- **The seven-section change report**, reused verbatim, written as a
  file outside the repository so it can never be accidentally committed.
- **Two GitHub-adapter techniques** worth keeping exactly as proven:
  actor lookup via the issue *timeline* (polling only shows current
  state, never who set it), and cursor files that turn polling into edge
  detection without ever needing an inbound port.

## Smaller imports

- **The server manifest's real shape** — `manifest.deployment.toml` now
  shows the actual `[server]` TOML fields inherited from `little-runner`,
  not just a description of the concept.
- **An ambient notification pattern, noted but not decided in.**
  `.claude/hooks/branch_audit_check.py` — a rate-limited, read-only,
  non-blocking `PostToolUse` hook — is a fourth notification shape this
  ecosystem already has proven, lighter than any of the three channels
  `docs/pillars/policy_compliance.md` settled on. Recorded as a real
  pattern worth considering later, deliberately not folded into the
  design by importing its existence alone.

## A new open question, caught in the act of writing

Specifying Agentic Development's review modes next to Policy &
Compliance's action-gating surfaced a real gap neither round noticed on
its own: nothing says what happens when a single Job is subject to both.
Logged as `OQ-12` rather than guessed at — this is `docs/open_questions.md`'s
own convention working exactly as designed, catching a question the
moment it surfaced instead of losing it the way `OQ-9` once was.

---

Back to [index](README.md) · or revisit [9 — Policy & Compliance](09-policy-and-compliance.md)
