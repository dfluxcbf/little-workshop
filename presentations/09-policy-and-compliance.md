← [Core's Recovery & Configuration](08-core-recovery-and-configuration.md) · deck 9 of 10 · next: [Action-Gating: Policy Confirmation →](10-action-gating-confirmation.md)

# Policy & Compliance: Rules for the Rules

The first pillar specified end-to-end. Before Policy & Compliance can
check anything, it needed its own rules for what a rule *is* — this deck
is that meta-policy, in outline. Full detail:
[`../docs/pillars/policy_compliance.md`](../docs/pillars/policy_compliance.md).

## Three tiers, not one

| Tier | What it is | Who writes it | Can be overridden? |
|---|---|---|---|
| Hard-coded invariants | Code, not data (e.g. "the agent never touches branches") | little-workshop's own source | Never — not even a policy |
| Framework policies | Real EARS + test script, same pipeline as any policy | little-workshop's own maintainers | Never, by any user policy |
| User policies | Real EARS + test script | Whoever manages a project through little-workshop | Yes — by a more specific user policy, or any framework policy |

The middle tier is new this round: a framework policy isn't a special
code path, it's an ordinary policy with `created_by: framework`, forced to
all-projects scope, and flagged never-overridable.

---

## Scope, and how "overwrite" actually resolves

A policy targets one project, a project set (a new, user-managed grouping,
owned by this pillar since targeting policies is the only thing it's for
today), or all projects. Two policies only conflict if they share a
**topic** — a short, stable name for the concern they govern. Precedence,
per topic, per project:

```
framework policy on this topic         (always wins, if one exists)
  > user policy, this project specifically
  > user policy, a project set it belongs to
  > user policy, all projects
```

A higher policy shadows a lower one *only for the projects it actually
covers* — everything else still answers to the broader one.

---

## Optionality is EARS' job

"This policy doesn't apply because the project has no server" isn't a
flag on the policy — it's EARS' own **optional feature** pattern: `WHERE a
project declares a server, the project shall...`. The recommended (not
mandated) mechanical convention: a pytest-based test script uses
`pytest.skip()` for that case — a third outcome, **not applicable**,
distinct from pass and fail, using a mechanism this ecosystem already has
rather than inventing one.

---

## Authoring: two Claude CLI calls, two different scopes of trust

```
natural language  →  already valid EARS?
                         │ no → Claude CLI (via Agentic Development's PI):
                         │       rewrite into EARS -- text-in, text-out,
                         │       ONLY the refined text comes back, nothing
                         │       is applied automatically
                         │      → human reviews, approves or asks again
                         ▼
                   EARS-compliant text
                         │
                         ▼
       Claude CLI (via Agentic Development's PI):
          write the one test script that enforces it
                         │
                         ▼
       saved: EARS text + 1:1 test script + topic + scope + created_by
```

Neither call reimplements agentic execution locally — both go through
Agentic Development's own Pillar Interface, the same "a capability lives
in one pillar, everyone else calls it" rule this project already applies
everywhere.

---

## Enforcement reuses, rather than re-owns, sandboxing

A policy check needs an isolated checkout of the target project — the same
need Requirements & Quality's test execution already has. Policy &
Compliance doesn't provision its own worktrees; both pillars call
Deployment & Servers' interface for one. One owner, two consumers, no
duplicated mechanism.

Every `(policy, project)` check has three possible outcomes now: **pass**,
**fail**, or **not applicable** — not the two this project assumed before
this round.

---

## The "little-workshop itself" tab

A framework policy goes through the *identical* pipeline as a user
policy — same EARS check, same two Claude CLI calls, same 1:1 script. The
only differences are who's allowed to start it, and that the result is
locked to all-projects and never-overridable. This is also where
`../docs/governance_policy.md`'s three prose principles eventually become
real, checkable policies instead of prose only read by this project's own
maintainers.

---

## Left open, on purpose

- Whether a generated test script needs explicit human review before its
  policy goes active (mirroring Agentic Development's normal PR flow) —
  not decided here.
- The exact "informed immediately" channel beyond the Dashboard and a
  reused per-project GitHub-issue pattern — neither is universal (Dashboard
  can be off; not every project has a GitHub remote).

---

Back to [index](README.md) · or revisit [2 — The Pillar Architecture](02-pillar-architecture.md)
