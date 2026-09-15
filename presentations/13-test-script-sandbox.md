← [Framework-Policy Lifecycle](12-framework-policy-lifecycle.md) · deck 13 of 13 · back to [index](README.md)

# The Test-Script Sandbox

Closes the gap named at the end of deck 11: a policy's enforcement
mechanism is AI-generated code, re-run unattended against real project
state, and "isolated worktree" only ever solved a concurrency problem, not
a safety one. Resolved 2026-09-15. Full detail:
[`../docs/pillars/policy_compliance.md`](../docs/pillars/policy_compliance.md#test-script-execution-sandbox).

## Four boundaries, one per resource

| Resource | Boundary |
|---|---|
| Network | Other pillars' Pillar Interfaces only — and only their *safe* (read/compute) endpoints. No external HTTP, no DNS, nothing that mutates something already running. |
| Filesystem (read) | Read-only, allowlisted. **Project-bound** policies: the target project's checkout + its `~/.config/little-workshop/projects/<project>/` files. **Framework-bound** policies: a strictly enumerated set of little-workshop's own files. Never both, never open-ended. |
| Filesystem (write) | Only the verdict itself (the same "result file outside the workspace" pattern Agentic Development already uses) and files already tagged `.policy` — Policy & Compliance's own write access, never broader. The project being checked is never written to, ever. |
| Resource bounds | An ordinary async Job (`depends_on`, not a blocking call), 30-minute ceiling — generous, since EARS-scoped checks are small by construction; the ceiling is against a hang or a bug, not an expected duration. |

---

## The natural mechanism already existed

Requirements & Quality's test execution already has an *optional* Docker
sandbox backend, designed but never mandatory. For policy checks
specifically, it stops being optional — a container's mount namespace is
what actually turns "an allowlist" into something enforced (bind-mount
exactly the granted paths, nothing else) and its network namespace is what
turns "PI-only" into something enforced (no route to anything except the
Unix-socket directory PI calls use). No new sandboxing primitive invented
— an existing, previously-optional one became load-bearing for the one
pillar that actually needed it to be.

---

## Two clocks, never running at once

The check's own 30-minute ceiling runs first, as an ordinary Job. Only
once it resolves — pass, fail, not-applicable, or timeout — does a failing
or timed-out result start the separate ten-minute human-confirmation clock
from [10 — Action-Gating](10-action-gating-confirmation.md). They're
sequential phases of the same flow, not two competing timers.

---

## Left open, genuinely

- **Timeout handling**: provisionally routed through the same
  pending_confirmation path as a real fail — same ten-minute override.
  A timeout more likely means a bug than a violation; offering the same
  "proceed anyway" for "unknown" as for "known and failed" isn't obviously
  right. Flagged, not decided.
- **Allowlist declaration**: should a policy's file-read allowlist be
  something a human reviews explicitly, alongside its EARS text — or is
  Claude CLI's own determination, trusted within the project-bound/
  framework-bound pool, enough? Flagged, not decided.

---

Back to [index](README.md) · or revisit [9 — Policy & Compliance](09-policy-and-compliance.md)
