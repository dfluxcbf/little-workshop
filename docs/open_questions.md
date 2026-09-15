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
- An item is **Open** until it's genuinely settled, then moves to
  **Resolved** with a date and a pointer to where the full reasoning
  lives — usually a `presentations/*.md` deck, since that's where this
  project already narrates a decision once it's made. This document
  stays the index; it doesn't duplicate the narrative.
- **Convention, not a mechanically-enforced policy**: any Claude CLI
  session that surfaces a new open question or design gap while working
  on this project adds it here — as its own `OQ-NNN` entry, dated,
  before the session concludes — rather than leaving it as a one-off
  remark buried in a document or a conversation. This is *why* `OQ-9`
  below existed without being tracked anywhere central until this
  document was created: the practice this document exists to fix.
  See "As a policy" below for why only half of this convention can
  actually be an enforced policy.

## Open

| ID | Title | Raised | Source |
|---|---|---|---|
| OQ-1 | Are the four enforceability-narrowed catalog entries (`doc-no-verbatim-duplication`, `milestone-requirements-linked-and-passing`, `no-secret-pattern-in-commit`, `no-redundant-app-auth`) still worth keeping at their weaker scope, or should some be dropped like the encryption/fabricated-content entries were? | 2026-09-15 | `docs/pillars/policy_catalog.md`, "Open questions for you" |
| OQ-2 | Anything in the ~50-entry catalog that's flat-out wrong for where this project is heading, not just unranked? | 2026-09-15 | `docs/pillars/policy_catalog.md`, "Open questions for you" |
| OQ-3 | Which Job types declare which gating topics — no mapping exists yet beyond the `server.launch`/`server.deploy` examples. | 2026-09-15 | `docs/pillars/policy_compliance.md`, "Left open" |
| OQ-4 | Log retention/rotation — no tool in this ecosystem, including little-workshop's own planned per-pillar logs, has a stated policy. | 2026-09-15 | `docs/pillars/policy_catalog.md`, "Gaps" |
| OQ-5 | Crash-loop bounds — Core's crash counter and backoff are conceptual only; no maximum retry count or backoff shape is defined. | 2026-09-15 | `docs/pillars/policy_catalog.md`, "Gaps" |
| OQ-6 | Health-probe timeout — nothing states how long Core waits for a pillar's health response before treating it as unhealthy. | 2026-09-15 | `docs/pillars/policy_catalog.md`, "Gaps" |
| OQ-7 | Project/data retention on removal — no policy for what happens to a project's requirements, jobs, and project files when it's deregistered. | 2026-09-15 | `docs/pillars/policy_catalog.md`, "Gaps" |
| OQ-8 | Pillar Interface deprecation window — versioned paths (`/v1`, `/v2`) are designed; nothing states how long an old version must keep working before it can be dropped. | 2026-09-15 | `docs/pillars/policy_catalog.md`, "Gaps" |
| OQ-9 | "Informed immediately" notification channel isn't universal — Dashboard can be disabled, GitHub isn't attached to every project, and nothing covers the gap between them. | 2026-09-15 | `docs/pillars/policy_compliance.md`, "Informing the user immediately" |

Lower priority — implementation-time detail rather than a design
decision, expected to resolve naturally once building starts, tracked
here only so nothing gets silently forgotten:

| ID | Title | Source |
|---|---|---|
| OQ-10 | Exact runtime dependency list per pillar isn't finalized. | `docs/architecture.md`, Dependencies |
| OQ-11 | Exact Bazel build-target shape per pillar isn't decided. | `docs/architecture.md`, Build |

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

## As a policy

"Keep an open-questions document, and record what Claude CLI surfaces in
it" doesn't survive intact as a single policy — the same "if we cannot
enforce a policy, we shall not define it" discipline this project already
applied to data-at-rest encryption and "no fabricated documentation"
splits this into two very different claims:

- **Enforceable, and a real catalog candidate**: `docs/open_questions.md`
  exists and has a minimally checkable structure (an Open table, a
  Resolved table). A test script can verify a file exists and roughly
  parses. See `docs/pillars/policy_catalog.md`'s new entry,
  `open-questions-document-exists`.
- **Not enforceable, and not a policy**: "Claude CLI documented every
  question it surfaced." Nothing can verify, from repository state alone,
  that a *conversation* raised a question and it went unrecorded — the
  same reasoning that kept "documentation shall not contain fabricated
  content" out of the policy system. This stays what it already is on
  this page: a stated convention every Claude CLI session working on this
  project is expected to follow, enforced by discipline and by whoever
  reviews the session's work, not by an automated check.
