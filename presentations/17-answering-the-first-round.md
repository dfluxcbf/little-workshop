← [Tracking Open Questions](16-tracking-open-questions.md) · deck 17 of 17 · back to [index](README.md)

# Answering the First Round

Nine of the eleven items in `docs/open_questions.md` got real answers.
Several turned out to carry genuinely new design content, not just a
number to fill in. `OQ-2` is still open — a review, not a decision, and
the file it needs to review was handed over rather than answered from
memory. Resolved 2026-09-15.

## OQ-1 split into four separate answers

The single question about four narrowed catalog entries turned out to
need four different resolutions:

- **`doc-no-verbatim-duplication`** stays as originally scoped — but the
  answer also corrected what "no duplication" should mean at the *policy*
  level, which was never precisely stated before now: every policy has a
  **`target_pillar`**, the one pillar whose behavior it governs. A topic
  maps to exactly one `target_pillar`, always — two pillars can't each
  hold their own version of what should be one rule. Gate 3 now checks
  this alongside its existing scope-level check, both mechanical, both
  exact. See
  [`../docs/pillars/policy_compliance.md`](../docs/pillars/policy_compliance.md#policy-ownership-every-topic-belongs-to-one-pillar).
- **`milestone-requirements-linked-and-passing`** stays narrow, and
  surfaced something bigger: a definition-of-done is multi-pillar (each
  pillar a task touches has its own criteria), a user's own
  definition-of-done always implicitly includes policy compliance, and a
  conflict between the two doesn't get silently resolved either
  direction — it becomes a refinement conversation, the same
  Claude-CLI-mediated pattern EARS refinement already uses. Worked
  example: "keep the crashing server running anyway" conflicts with "a
  broken server gets replaced" — refinement proposes running the crashing
  version on a separate port, satisfying both.
- **`no-secret-pattern-in-commit`** became two policies instead of one:
  `secret-entry-point-tagged` (structural — a secret reference is tagged
  and the actual value lives outside the repo) as the primary mechanism,
  with `secret-scan-best-effort` (a Claude CLI review, held for
  confirmation rather than refused outright) as broader-but-fuzzier
  supplementary coverage.
- **`no-redundant-app-auth`** is withdrawn entirely — not narrowed, not
  kept. It was never a policy: little-workshop simply doesn't build a
  login mechanism for itself, a design decision already implied by "no
  multi-tenant auth," now stated plainly instead of dressed up as an EARS
  requirement.

---

## OQ-2 — still open, on purpose

The answer was a request, not a decision: the full catalog
(`docs/pillars/policy_catalog.md`) was handed over for review rather than
judged from a summary. This item stays open until that review actually
happens.

---

## OQ-3 — a checklist, not a one-line rule

"State a general principle" turned into eleven categories — reversibility,
external visibility, cost, blast radius, data sensitivity, access,
release lifecycle, policy interaction, frequency, actor/context,
detectability — each with a lean-gated and lean-ungated side, meant to be
applied together rather than any one being decisive. The closing rule
matters most: an new Job type that doesn't clearly match an existing
pattern is a signal to look closer, never a default to ungated. Full
checklist:
[`../docs/pillars/policy_compliance.md`](../docs/pillars/policy_compliance.md#deciding-whether-a-job-type-is-gated).

---

## OQ-4 through OQ-11 — the numbers

| | Resolution |
|---|---|
| OQ-4, log retention | Size-based, not age-based — a rolling window that trims the oldest lines once a log file passes its threshold. Fits a system hosted on one personal machine, where disk space is the actual constraint. |
| OQ-5, crash-loop bounds | 10 consecutive relaunch attempts, 1 minute apart. The 10th failure stops automatic retries and marks the pillar **faulted** — a new state, distinct from disabled or merely crashed. |
| OQ-6, health-probe timeout | Each pillar performs its own internal health analysis and returns the full report, not a bare ping. Core waits 5 seconds per probe, up to 10 retries. |
| OQ-7, project removal | Hard delete, immediate, cascading — every pillar purges its own data for that `project_id` through its own interface. No soft-delete, no undo. |
| OQ-8, PI deprecation window | Not a policy — "how long is reasonable" isn't something a test script can verify. Stays a judgment call for whoever owns the pillar, each time. |
| OQ-9, notification channel | **Remote Control** — the same mechanism Agentic Development already uses, and the one `little-runner` already proved for this exact shape of problem. Tied to the user's Claude account, not per-project config, which is what actually makes it universal where Dashboard and GitHub issues aren't. |
| OQ-10, per-pillar dependencies | Confirmed deferred to implementation — nothing flagged as unusual. |
| OQ-11, per-pillar build targets | `//:build`, `//:install`, `//:test`, `//:server` — the last one new to this ecosystem's vocabulary, since a pillar's artifact type (a long-running service) never existed here before. |

---

## A pattern worth naming

OQ-8's resolution is the third time this project has caught itself about
to define an unenforceable policy and pulled back — data-at-rest
encryption, "no fabricated documentation," and now PI deprecation timing
all hit the same wall: something that requires human judgment about
*reasonableness* rather than a checkable fact. The discipline is holding.

---

Back to [index](README.md) · or revisit [9 — Policy & Compliance](09-policy-and-compliance.md)
