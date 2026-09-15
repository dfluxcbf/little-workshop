← [The Test-Script Sandbox](13-test-script-sandbox.md) · deck 14 of 15 · next: [Who Owns a Project? →](15-project-ownership.md)

# Verdicts vs. Faults

Closes both questions deck 13 left open. Resolved 2026-09-15.

## A timeout is not a policy outcome

The earlier design treated a script timeout as roughly equivalent to a
failing check — routed through the same ten-minute human-override window
as a real `fail`. That was wrong, for a reason worth stating plainly:

> The ten-minute window is a deliberate-bypass card. It exists so that if
> a user actually intended to break a policy — they might have their
> reasons — they have ten minutes to say "continue anyway." A timeout has
> no determined violation behind it for anyone to knowingly bypass. There
> is nothing to say yes to.

The 30-minute ceiling is a different kind of thing entirely: a framework
design decision ("ensure scripts are not stuck"), not something Policy &
Compliance's own rule system produced an answer to — the same category as
Core refusing to let a crashed pillar spiral, not a policy verdict.
EARS-scoped checks are small by construction and are expected nowhere near
it.

---

## The corrected outcome tree

```
policy.check_project runs
   │
   ├─ VERDICT REACHED (script completed): pass | fail | not_applicable
   │     pass/not_applicable → gated Job proceeds, nothing else happens
   │     fail → pending_confirmation, ten-minute deliberate-bypass window,
   │            unanswered → action never happens ("probably a mistake")
   │
   └─ NO VERDICT (30-minute ceiling hit): a framework fault, not a policy
         outcome → script forcibly terminated → gated Job fails
         immediately, NO confirmation window (nothing to bypass) →
         surfaced distinctly from a policy failure, as something to go
         fix (most likely a bug in the generated script)
```

Two clocks, two different jobs: the 30-minute ceiling is self-protection
that happens *before* any verdict exists; the ten-minute window is a
bypass card that only ever starts *after* one does.

---

## Full approval, every time, no exception

The second open question — does a policy's file-read allowlist need
separate human review — dissolved once the actual answer to authoring
review was stated plainly:

> No policy shall be created or edited without full approval by the user
> — including both the EARS text and the generated script.

Given that, a *separate* allowlist-approval step would have been
redundant: the allowlist is just what the script the human is already
reviewing actually reads. **Gate 4** now sits at the end of
`docs/pillars/policy_compliance.md`'s authoring pipeline — after EARS
compliance, enforceability, and non-conflict, but before anything is
saved or active: the human reviews both artifacts together, every time,
for every creation and every edit, user-tier or framework-tier alike.
Claude CLI's role throughout authoring is drafting — every artifact it
produces stays a proposal until a human has looked at it.

---

Next: [15 — Who Owns a Project? →](15-project-ownership.md) · or back to [index](README.md)
