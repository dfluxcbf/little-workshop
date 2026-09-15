← [Policy & Compliance: Rules for the Rules](09-policy-and-compliance.md) · deck 10 of 11 · next: [Authoring Gates & Boundaries →](11-authoring-gates-and-boundaries.md)

# Action-Gating: Policy Confirmation

Answers the question deck 9 left open: *when* does a policy check
actually run relative to the action it governs. Full detail:
[`../docs/pillars/policy_compliance.md`](../docs/pillars/policy_compliance.md#action-gating-checks).

## Not every Job — only ones a policy actually governs

Most Job types declare no gating topic and dispatch immediately, with
Policy & Compliance never in the loop at all. A Job type that *does*
declare one — `server.launch`, `server.deploy` — gets checked against
whichever policy currently wins, on that topic, for the target project,
synchronously, before it's allowed to run.

---

## Fail closed, with a human given the chance to override

```
gated Job about to dispatch
   → winning policy check fails
   → Job = pending_confirmation, user warned immediately
        confirmed within 10 minutes  → dispatch proceeds anyway
        no response in 10 minutes    → Job = failed, action NEVER happens
```

The ten-minute window isn't a new number — it's little-runner's existing
`confirm_timeout_seconds` default (600s), reused exactly. For a fresh
server launch, "never happens" means the server stays down. For a
replace, it means Deployment & Servers' build-before-stop sequence never
even starts — the old version was never touched, so there's nothing to
roll back. The timeout's default is always the option that changes
nothing.

---

## Two confirm-gates, kept as two things

| | Deploy confirmation (existing) | Policy confirmation (new) |
|---|---|---|
| Owner | Deployment & Servers | Orchestration |
| Arms | After the new version is already running | Before the action starts at all |
| Timeout default | Roll back to last confirmed commit | Never start |
| Scope | Server/pillar redeploys only | Any Job type with a gating topic |

Same skeleton — mark pending, arm a timeout, wait, do the safe thing on
timeout — but different questions at different moments. A gated
`server.deploy` can pass through both in sequence: policy confirmation
first (proceed at all?), then deploy confirmation once it's actually
running (keep it?).

---

## This supersedes an earlier hand-wave

`docs/design.md`'s original job-dispatch diagram just said a failing
pre-job policy check "fails → Job = failed" — written before this
mechanism existed. It's now specifically: fails → waits for a human →
*then* either proceeds or never happens, never an unconditional block.

---

## Left open

- No defined maximum runtime for the check itself — it's assumed fast
  enough to run inline, but that's an assumption, not a limit.
- Which Job types declare which gating topics isn't enumerated — that's
  each pillar's own work as its Job types get defined.

---

Back to [index](README.md) · or revisit [9 — Policy & Compliance](09-policy-and-compliance.md)
