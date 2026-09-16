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

### OQ-13 — Should granting a policy waiver itself be gated, reviewed, or time-boxed?

**Where this comes from.** Surfaced while building the 2026-09-16 policy
usability refinement (enable/disable, per-project waivers, per-policy
gate points) — not a pre-existing gap, a new one caught in the act of
implementing, per this document's own "Claude CLI documents what it
surfaces" convention.

**What's actually being asked.** `policy.waiver_grant` is an ordinary,
ungated Job today: any caller who can reach Policy & Compliance's own
Pillar Interface can grant a project a permanent exception to any
policy — including a framework one — by supplying nothing more than a
`reason` string. There's no approval step, no expiry, and no periodic
re-justification. That's a real, if narrow, hole in "framework policy
is never overridden by anything" — technically true (the policy itself
is untouched, and every *other* project still obeys it) but a waiver
still means a specific project stops being checked against it,
indefinitely, on one person's say-so.

**Why it matters.** If waivers are meant to be rare, deliberate
exceptions, an ungated, permanent, unreviewed grant path is easy to
reach for as a workaround rather than a last resort — the opposite of
what the framework tier exists to guarantee. If they're meant to be
routine "this project is just different" configuration, then the
current shape (a reason, an audit trail via `created_at`, nothing more)
is already right-sized, and adding process would just be friction.

**Possible interpretations.**
- (a) Leave it exactly as built: a reason and a timestamp are the whole
  audit trail; any caller with PI access can grant/revoke freely,
  same trust model as everything else in this single-user household
  system (`docs/architecture.md`'s "no multi-tenant auth" decision).
- (b) A waiver against a *framework* policy specifically routes through
  its own gate (e.g. requires the same kind of explicit confirmation an
  action-gated Job's own `pending_confirmation` window uses) — user
  waivers stay ungated, since a user policy is already fully within
  that user's own authority to edit or remove outright.
- (c) Waivers expire by default (a `expires_at`, optional) and surface
  on `policy.check_all`/a periodic audit as "this waiver is stale,
  reconfirm or let the policy re-apply" — never silently permanent.

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
| OQ-12 | Interpretation (a): gating always resolves first, regardless of review mode. Confirmed by the actual M12 implementation's own shape — `orchestration/policy_gate.py`'s gate check lives entirely inside Orchestration's Job-creation/dispatch path, with zero awareness of Agentic Development's `review_mode` at all; a `deployment.deploy` Job created as the *result* of an auto-approved agentic task goes through the identical gate-check-then-`pending_confirmation` flow as one created by hand. Auto-approve governs only the agent's own report/test-result check inside Agentic Development's own flow (M6) and never reaches into, or is consulted by, Orchestration's policy gate — a policy is never optional, for either review mode. | 2026-09-16 | `docs/pillars/policy_compliance.md`'s "Action-gating checks," implemented as `docs/milestones.md` M12 |

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
