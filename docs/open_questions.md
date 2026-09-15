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
- Each open item below has a **Your answer** field — write directly
  under it, in this file. Partial answers, a decision plus a "but," or a
  question back are all fine; this is a worksheet, not a form with a
  single right shape.
- Once an item has an answer, tell Claude to process this file: it moves
  the item to **Resolved** (with a date and a pointer to the deck/doc
  where the full reasoning gets written up), updates every doc the answer
  actually affects, and clears the field so the next round starts clean.
- **Convention, not a mechanically-enforced policy**: any Claude CLI
  session that surfaces a new open question or design gap while working
  on this project adds it here — as its own `OQ-NNN` entry, dated,
  before the session concludes — rather than leaving it as a one-off
  remark buried in a document or a conversation. This is *why* `OQ-9`
  below existed without being tracked anywhere central until this
  document was created: the practice this document exists to fix. See
  "As a policy" below for why only half of this convention can actually
  be an enforced policy.

## Open

### OQ-1 — Keep the enforceability-narrowed catalog entries?

*Raised 2026-09-15 · source: `docs/pillars/policy_catalog.md`, "Open questions for you"*

Four catalog entries were narrowed to their mechanically enforceable core:
`doc-no-verbatim-duplication`, `milestone-requirements-linked-and-passing`,
`no-secret-pattern-in-commit`, `no-redundant-app-auth`. Each is a weaker
claim than the source material's original intent, by construction. Worth
confirming the narrower version is still worth having as a policy — or
should some also be dropped entirely, the way the data-at-rest-encryption
and fabricated-content entries were?

**Your answer:**


---

### OQ-2 — Anything in the catalog flat-out wrong?

*Raised 2026-09-15 · source: `docs/pillars/policy_catalog.md`, "Open questions for you"*

Not "unranked" or "needs narrowing" — actually wrong for where this
project is heading. Easiest to catch now, before any catalog entry goes
through real EARS refinement.

**Your answer:**


---

### OQ-3 — Which Job types declare which gating topics?

*Raised 2026-09-15 · source: `docs/pillars/policy_compliance.md`, "Left open"*

Action-gating (deck 10) established that a Job *type* can declare a
gating topic, using `server.launch`/`server.deploy` as the only examples
so far. No actual mapping of Job types to topics exists yet for any other
pillar. Worth sketching now for the pillars already specified, or purely
an implementation-time decision once each pillar's Job types are defined?

**Your answer:**


---

### OQ-4 — Log retention / rotation

*Raised 2026-09-15 · source: `docs/pillars/policy_catalog.md`, "Gaps"*

No tool in this ecosystem — including `little-runner`'s existing
`~/.local/state/little-runner/logs/` and little-workshop's own planned
per-pillar `~/.local/state/little-workshop/logs/<pillar>/` — has a stated
retention or rotation policy.

**Your answer:**


---

### OQ-5 — Crash-loop bounds

*Raised 2026-09-15 · source: `docs/pillars/policy_catalog.md`, "Gaps"*

Core's design mentions a crash counter and backoff conceptually
(`docs/architecture.md`'s Core description). No maximum retry count or
backoff shape is actually defined.

**Your answer:**


---

### OQ-6 — Health-probe timeout

*Raised 2026-09-15 · source: `docs/pillars/policy_catalog.md`, "Gaps"*

Nothing states how long Core waits for a pillar's health response before
treating it as unhealthy and acting (relaunching, marking it down).

**Your answer:**


---

### OQ-7 — Project/data retention on removal

*Raised 2026-09-15 · source: `docs/pillars/policy_catalog.md`, "Gaps"*

No stated policy for what happens to a project's requirements, jobs, and
project files when it's deregistered from Registry & Portfolio. Deleted
immediately? Archived? Kept until explicitly purged?

**Your answer:**


---

### OQ-8 — Pillar Interface deprecation window

*Raised 2026-09-15 · source: `docs/pillars/policy_catalog.md`, "Gaps"*

Versioned PI paths (`/v1`, `/v2`) are designed so a pillar can serve two
versions at once during a migration. Nothing states how long an old
version must keep working before it can be dropped.

**Your answer:**


---

### OQ-9 — "Informed immediately" isn't a universal channel

*Raised 2026-09-15 · source: `docs/pillars/policy_compliance.md`, "Informing the user immediately"*

A failing policy check currently reaches the user through two channels:
the Dashboard (can be disabled) and a per-project GitHub issue (only
exists for projects with the GitHub adapter attached). Neither is
universal on its own, and nothing covers a project that has neither. This
is the item that fell out of tracking once already — see deck 16.

**Your answer:**


---

## Lower priority — implementation-time detail

Not design decisions so much as things to not silently forget once
building starts.

### OQ-10 — Per-pillar runtime dependencies

*Raised 2026-09-15 · source: `docs/architecture.md`, Dependencies*

Exact runtime dependency list per pillar isn't finalized — expected to
mirror the absorbed tools' existing choices, not decided in detail.

**Your answer:**


---

### OQ-11 — Per-pillar Bazel build-target shape

*Raised 2026-09-15 · source: `docs/architecture.md`, Build*

One Bazel package per pillar is expected; the exact target shape beyond
`//:all`/`//:test` isn't decided.

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
