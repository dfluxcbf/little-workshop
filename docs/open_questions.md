# Open questions & gaps

The single tracker for every unresolved design/implementation question
about this project — not a snapshot, a living document. Where a question
used to live scattered across `design.md`, `architecture.md`, and
`docs/pillars/*.md` as a "Left open" footnote, those sections now point
here instead of restating it, per this ecosystem's own documentation
policy ("don't duplicate content across files; link to the authoritative
one instead").

## How this document works

- Every open item gets a stable ID (`OQ-NNN`), assigned in raised order,
  never reused even if the item is later dropped rather than resolved.
- Each open item has four parts: **where it comes from** (the design
  round or gap that surfaced it), **what's actually being asked** (in
  plain terms, not just the original one-line summary), **why it
  matters** (what actually changes depending on the answer — nothing here
  is asked just to have an answer on file), and **possible
  interpretations** (a starting menu, not an exhaustive one — picking
  none of them and saying something else is completely fine).
- Then a **Your answer** field — write directly under it, in this file.
  Partial answers, a decision plus a "but," or a question back are all
  fine; this is a worksheet, not a form with a single right shape.
- Once an item has an answer, tell Claude to process this file: it moves
  the item to **Resolved** (with a date and a pointer to the deck/doc
  where the full reasoning gets written up), updates every doc the answer
  actually affects, and clears the field so the next round starts clean.
- **Convention, not a mechanically-enforced policy**: any Claude CLI
  session that surfaces a new open question or design gap while working
  on this project adds it here — as its own `OQ-NNN` entry, dated,
  before the session concludes — rather than leaving it as a one-off
  remark buried in a document or a conversation. This is *why* `OQ-9`
  was, for a time, the clearest example this project had of the practice
  this document exists to enforce. See "As a policy" below for why only
  half of this convention can actually be an enforced policy.

## Open

### OQ-12 — Do review-mode and action-gating compose on the same Job?

**Where this comes from.** Surfaced while writing
`docs/pillars/agentic_development.md` during a final import pass over the
whole ecosystem — not a pre-existing gap, a new one caught in the act of
writing, per this document's own "Claude CLI documents what it surfaces"
convention.

**What's actually being asked.** Agentic Development's review mode
(manual vs. auto-approve) answers "is the agent's *own* work good enough
to finish automatically." Policy & Compliance's action-gating
(`pending_confirmation`) answers a different question: "does a *policy*
allow this action to happen at all." Nothing says what happens when a
single Job is subject to both — e.g. an auto-approved agentic task whose
resulting action is also gated by a failing policy.

**Why it matters.** These two mechanisms were designed independently, in
different rounds, for different reasons, and were never checked against
each other on a single Job. Without an answer, an implementer has to
guess whether policy-gating happens before or after auto-approval
resolves, or whether auto-approve should be allowed to skip gating
entirely (it shouldn't, by every principle already established — a
policy is never optional — but that needs to actually be stated, not
assumed).

**Possible interpretations.**
- (a) Gating always resolves first, regardless of review mode —
  auto-approve only ever governs the agent's own report/test-result
  check, never a policy check. A gated Job stays gated no matter what
  review mode it was submitted under.
- (b) The two compose sequentially in a fixed order (e.g. policy gate,
  then review-mode check) with each step's own confirmation window,
  potentially stacking two human interactions for one Job.
- (c) Auto-approve implicitly pre-answers a gate's confirmation (since
  the human already said "proceed without me looking" at submission
  time) — rejected on its face by (a)'s reasoning above, but worth ruling
  out explicitly rather than leaving unstated.

**Your answer:**


---

## Resolved

Terse by design — full reasoning lives at the linked deck, never repeated
here.

| ID | Title | Resolved | Detail |
|---|---|---|---|
| — | Single-process, shared-database architecture → Core + ten independent pillars | 2026-09-14 | `presentations/02-pillar-architecture.md` |
| — | Independence from `little-requirements`/`little-versions` as mandatory dependencies | 2026-09-14 | `presentations/07-self-governance.md` |
| — | Core's own crash recovery (reconnect, don't relaunch) and the config/project-files ownership model | 2026-09-14 | `presentations/08-core-recovery-and-configuration.md` |
| — | Policy & Compliance specified end-to-end: three rule tiers, scope/precedence, EARS, authoring pipeline | 2026-09-15 | `presentations/09-policy-and-compliance.md` |
| — | When a policy check runs relative to the action it governs (action-gating, fail-closed, 10-minute override) | 2026-09-15 | `presentations/10-action-gating-confirmation.md` |
| — | Catalog tier defaulted to framework; enforceability (Gate 2) and conflict (Gate 3) added to authoring; Policy & Compliance placed out of user policies' reach; user-policy lifecycle defined | 2026-09-15 | `presentations/11-authoring-gates-and-boundaries.md` |
| — | Framework-policy lifecycle: hard while in force, but editable/removable by little-workshop's own maintainers as continuous development | 2026-09-15 | `presentations/12-framework-policy-lifecycle.md` |
| — | Test-script execution sandbox: PI-only network, allowlisted read-only filesystem, verdict-only writes, 30-minute async ceiling | 2026-09-15 | `presentations/13-test-script-sandbox.md` |
| — | A script timeout is a framework fault, not a policy verdict — never routed through the 10-minute override; Gate 4 (full human approval of EARS text and script) added | 2026-09-15 | `presentations/14-verdicts-vs-faults.md` |
| — | Registry & Portfolio is the main responsible pillar for project identity/lifecycle; bootstrap vs. Claude-CLI-driven import; project sets moved there from Policy & Compliance; registry row is authoritative over the project-files directory | 2026-09-15 | `presentations/15-project-ownership.md` |
| — | Introduced this tracker; caught OQ-9 going unresolved; split the tracking convention into an enforceable policy and an unenforceable one | 2026-09-15 | `presentations/16-tracking-open-questions.md` |
| OQ-1 | Enforceability-narrowed entries: kept `doc-no-verbatim-duplication` and `milestone-requirements-linked-and-passing`; upgraded `no-secret-pattern-in-commit` into a tagged-entry-point mechanism plus a best-effort Claude CLI scan; withdrew `no-redundant-app-auth` entirely as a design decision, not a policy. Also resolved policy *ownership*: every topic maps to exactly one `target_pillar`, checked at Gate 3. | 2026-09-15 | `presentations/17-answering-the-first-round.md` |
| OQ-3 | An eleven-category checklist for deciding whether a Job type is gated (reversibility, visibility, cost, blast radius, data sensitivity, access, release lifecycle, policy interaction, frequency, actor/context, detectability) — applied together, never defaulting to ungated on a new, unmatched Job type. | 2026-09-15 | `presentations/17-answering-the-first-round.md` |
| OQ-4 | Log retention is size-based, not age-based — a rolling window trimming the oldest lines past a size threshold. | 2026-09-15 | `presentations/17-answering-the-first-round.md` |
| OQ-5 | Crash-loop bounds: 10 consecutive relaunch attempts, 1 minute apart; the 10th failure marks the pillar **faulted**, a new state. | 2026-09-15 | `presentations/17-answering-the-first-round.md` |
| OQ-6 | Health probes are a full internal report, not a bare ping; 5-second timeout, up to 10 retries. | 2026-09-15 | `presentations/17-answering-the-first-round.md` |
| OQ-7 | Project removal is a hard delete, immediate and cascading across every pillar's own data for that project. | 2026-09-15 | `presentations/17-answering-the-first-round.md` |
| OQ-8 | PI deprecation window is not a policy — a judgment call for whoever owns the pillar, each time. | 2026-09-15 | `presentations/17-answering-the-first-round.md` |
| OQ-9 | Remote Control resolved as the actually-universal notification channel, reusing Agentic Development's existing mechanism and `little-runner`'s own proven pattern. | 2026-09-15 | `presentations/17-answering-the-first-round.md` |
| OQ-10 | Per-pillar runtime dependencies confirmed deferred to implementation. | 2026-09-15 | `presentations/17-answering-the-first-round.md` |
| OQ-11 | Per-pillar build targets: `//:build`, `//:install`, `//:test`, `//:server` (the last one new to this ecosystem, specific to a pillar's long-running-service artifact type). | 2026-09-15 | `presentations/17-answering-the-first-round.md` |
| OQ-2 | Catalog reviewed directly by the user (not summarized) — confirmed good, no changes needed. | 2026-09-15 | `presentations/18-catalog-confirmed.md` |

## As a policy

"Keep an open-questions document, and record what Claude CLI surfaces in
it" doesn't survive intact as a single policy — the same "if we cannot
enforce a policy, we shall not define it" discipline this project already
applied to data-at-rest encryption and "no fabricated documentation"
splits this into two very different claims:

- **Enforceable, and a real catalog candidate**: `docs/open_questions.md`
  exists and has a minimally checkable structure (an Open section, a
  Resolved table). A test script can verify a file exists and roughly
  parses. See `docs/pillars/policy_catalog.md`'s entry,
  `open-questions-document-exists`.
- **Not enforceable, and not a policy**: "Claude CLI documented every
  question it surfaced." Nothing can verify, from repository state alone,
  that a *conversation* raised a question and it went unrecorded — the
  same reasoning that kept "documentation shall not contain fabricated
  content" out of the policy system. This stays what it already is on
  this page: a stated convention every Claude CLI session working on this
  project is expected to follow, enforced by discipline and by whoever
  reviews the session's work, not by an automated check.
