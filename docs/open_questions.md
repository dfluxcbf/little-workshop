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
  below existed without being tracked anywhere central until this
  document was created: the practice this document exists to fix. See
  "As a policy" below for why only half of this convention can actually
  be an enforced policy.

## Open

### OQ-1 — Keep the enforceability-narrowed catalog entries?

**Where this comes from.** The authoring pipeline's Gate 2 ("if a policy
cannot be enforced, it is not defined") was applied retroactively to the
whole ~50-entry draft catalog after it was first written. Two entries
failed outright and were dropped entirely (data-at-rest encryption
posture, "documentation shall not contain fabricated content" — neither
is something a test script can verify). Four more survived, but only
after being narrowed to a smaller claim than the source material
originally intended:

| Topic | Original intent | What it was narrowed to |
|---|---|---|
| `doc-no-verbatim-duplication` | No duplicate *content* across docs (a semantic judgment) | No *identical text block* across docs (a diff can verify this) |
| `milestone-requirements-linked-and-passing` | Full definition-of-done: docs updated, requirements updated, implementation complete, tests passing | Just the two sub-clauses a script can check: requirements linked, tests passing |
| `no-secret-pattern-in-commit` | No credential ever stored or logged, anywhere (unbounded — can't prove an absence) | A staged file matching a known credential/token *pattern* is refused (a bounded scan) |
| `no-redundant-app-auth` | A project reachable only via the private network shall not "require" app-level login (what "require" means for arbitrary code is undefined) | A structural pair: a login route existing without a declared public-exposure flag is refused |

**What's actually being asked.** Each narrowed version is real and
enforceable, but genuinely weaker than what the original rule was trying
to guarantee — `no-secret-pattern-in-commit` will catch a token matching
a known shape, not every possible secret; `doc-no-verbatim-duplication`
won't catch two sections that say the same thing in different words. Is
"partial coverage, honestly scoped" still worth having as a policy, for
all four, some of them, or none of them?

**Why it matters.** These are all framework-tier (per the tier decision
already made), so whichever answer this gets, it applies to every project
little-workshop ever manages, unconditionally. Keeping a narrowed policy
gives real (if partial) automated protection — but also risks a false
sense of completeness, where "there's a policy for this" gets read as
"this is fully covered" when it isn't. Dropping it removes that risk but
also removes the only automated backstop the ecosystem would otherwise
have for that concern — it reverts to a pure human-review convention,
like `undefined-purpose-honesty` already is.

**Possible interpretations.**
- (a) Keep all four as narrowed — partial, honestly-scoped coverage beats
  none, and each is still a legitimate, distinct check on its own terms.
- (b) Drop all four — a policy that only partially delivers on its name
  is worse than no policy, because it invites false confidence.
- (c) Case by case — e.g. keep `no-secret-pattern-in-commit` (even a
  partial secret scan is high-value) but drop `no-redundant-app-auth`
  (lower stakes, narrower benefit).
- (d) Keep them, but make the narrowing visible where the policy is
  *used*, not just in this catalog — e.g. its EARS text or description
  explicitly says "this catches known patterns, not every case" so
  nobody downstream mistakes partial coverage for complete coverage.

**Your answer:**


---

### OQ-2 — Anything in the catalog flat-out wrong?

**Where this comes from.** The ~50-entry catalog was produced by mining
the 8 root policy documents and every sibling project's own design
decisions, then generalizing anything that named one of the four tools
little-workshop is absorbing. That's a mechanical, first-pass process —
never independently reviewed entry-by-entry against whether it's actually
right for where *this* project is heading, only whether it was
faithfully extracted from its source.

**What's actually being asked.** Not "which entries are weak" (that's
`OQ-1`, and a few others already got trimmed for enforceability) — whether
anything in the list is a genuine mistake: stale, contradictory to a
decision made elsewhere in this project, or a convention that made sense
for the *current* ecosystem tooling but won't make sense once
little-workshop is the real infrastructure.

**Why it matters.** Every catalog entry defaults to framework tier —
immutable, applied to every project unconditionally. A mistake caught now
costs nothing; the same mistake caught after real projects are already
complying with it costs a framework-policy edit (still possible, per the
resolved framework-policy-lifecycle round, but real work, not free).

**Possible interpretations.** This isn't really multiple-choice, but two
places are worth looking first, by confidence level:
- **Lower-confidence entries** — mined from a *single* project rather
  than a repeated pattern: `no-redundant-app-auth` and `no-third-party-cdn`
  both come from `little-meals` alone. Compare against
  `tailscale-only-exposure`, independently established in three separate
  projects before it was promoted — that's the level of validation the
  single-source entries don't have.
- **Anything that assumes today's tooling** in a way the "name the
  concern, not the tool" pass might have missed despite the sweep already
  done for `lreq`/`lvx`/`lrun`/`little-pypi` specifically.

**Your answer:**


---

### OQ-3 — Which Job types declare which gating topics?

**Where this comes from.** Action-gating (the mechanism where a Job
doesn't run until the policy governing it passes) needs each Job *type*
to declare which topic, if any, gates it. The only two examples specified
so far are `server.launch` and `server.deploy`, gated by a server-related
topic — chosen as illustrative examples while designing the mechanism
itself, not as a considered pass over every pillar's Job types.

**What's actually being asked.** Beyond those two examples, no other Job
type across any pillar has been assigned a gating topic (or explicitly
decided to have none). Is this worth a first pass now, or is it purely
implementation-time work, decided pillar by pillar as each one actually
gets built?

**Why it matters.** This is a real safety-coverage decision, not
bookkeeping. A Job type that changes something hard to reverse (a
release, a deployment, a project deletion) but isn't marked as gated
means a relevant policy violation only ever gets caught *after the fact*,
by the periodic audit — a materially weaker guarantee than catching it
before the action happens. The reverse mistake — marking too much as
gated — adds a confirmation-window interruption to actions that didn't
need one.

**Possible interpretations.**
- (a) Leave it fully to implementation time, per pillar, as originally
  noted — no general guidance beyond the two existing examples.
- (b) State a general principle now, even without enumerating every Job
  type: something like "any Job type that performs a hard-to-reverse
  change (deploy, release, delete) defaults to gated; anything read-only
  or trivially reversible defaults to ungated" — giving future
  implementers a rule instead of a blank page.
- (c) Sketch a concrete starter list now for the pillars already specified
  in enough detail to guess at (Deployment & Servers, Registry &
  Portfolio's bootstrap/import), leaving the rest for later.

**Your answer:**


---

### OQ-4 — Log retention / rotation

**Where this comes from.** Identified by absence, not mined from a
source: `little-runner` already writes run logs to
`~/.local/state/little-runner/logs/`, and little-workshop's own design
plans the same pattern per pillar
(`~/.local/state/little-workshop/logs/<pillar>/`). No tool in this
ecosystem, old or new, states how long those logs are kept.

**What's actually being asked.** Should there be a retention/rotation
policy at all, and if so, age-based, size-based, or something else?

**Why it matters.** This is a real operational risk for a system meant to
run indefinitely, unattended, on one PC: unbounded log growth eventually
becomes disk pressure. It also affects debugging usefulness in the other
direction — too-aggressive rotation loses the history you'd want when
something actually goes wrong. It also touches Policy & Compliance's own
per-pillar `events` audit trail, which has the same unbounded-growth
shape and the same stake in "is this actually auditable in practice, not
just in principle."

**Possible interpretations.**
- (a) Simple age-based retention (delete logs older than N days) —
  matches common log-rotation tooling, easy to reason about.
- (b) Size-based (cap total log directory size per pillar, drop oldest on
  overflow) — bounds disk usage directly regardless of how active a
  pillar is.
- (c) Tiered: full detail recently, compressed/archived further back,
  deleted past some longer horizon — more history retained cheaply, more
  moving parts.
- (d) No automatic policy at all — treat this as the host's own general
  disk-management problem, out of scope for little-workshop given the
  personal, single-PC scale.

**Your answer:**


---

### OQ-5 — Crash-loop bounds

**Where this comes from.** Core's design already states it "tracks a
crash counter per pillar" and applies "backoff" before endlessly
relaunching a pillar that keeps failing (see `docs/design.md`'s "Core's
own recovery"). That description has always been conceptual — no actual
number or backoff shape was ever attached to it.

**What's actually being asked.** How many retries, on what schedule,
before Core treats a crash-looping pillar differently than a pillar that
crashed once and recovered?

**Why it matters.** This is a real resilience characteristic, not a
cosmetic detail. With no bound, a badly broken pillar could restart-loop
indefinitely — burning CPU, spamming logs (which also feeds back into
`OQ-4`). With too aggressive a bound, Core could give up on a pillar that
was only ever having a transient problem. This also raises a question
`OQ-9` already lives next to: once Core *does* give up on a pillar,
something needs to tell the user — another instance of the same
"informed immediately" gap.

**Possible interpretations.**
- (a) A small number of immediate retries, then exponential backoff
  capped at some maximum interval (e.g. every 5 minutes) — Core never
  fully gives up, just slows down.
- (b) A fixed number of retries, then stop entirely and mark the pillar
  **faulted** — a new state distinct from disabled or merely crashed,
  requiring explicit human action to try again.
- (c) A time-window count rather than a raw retry count ("more than 5
  crashes in 10 minutes" triggers backoff/stop) — so an occasional crash
  over days doesn't trip the same response as a rapid loop.

**Your answer:**


---

### OQ-6 — Health-probe timeout

**Where this comes from.** Core's reconnect-on-restart design (deck 8)
has it probe each pillar's own health endpoint to tell "running" (per
`systemctl`) apart from "actually healthy." No timeout for that probe was
ever specified.

**What's actually being asked.** How long does Core wait for a health
response before deciding a pillar is unhealthy and acting on it?

**Why it matters.** Too short, and Core risks false positives — treating
a pillar that's merely slow (host under load, a big request in flight) as
dead and relaunching it unnecessarily, which is disruptive if that pillar
was mid-operation. Too long, and Core is slow to notice a genuinely hung
pillar, undercutting the whole "fast, dumb crash recovery" design goal
from deck 8.

**Possible interpretations.**
- (a) A short, fixed timeout (1-2 seconds) — a health check should only
  ever mean "are you alive," never real work, so a short timeout is
  appropriate and fast failure is the right default.
- (b) A longer, more forgiving timeout (10-30 seconds) — trades slower
  detection for fewer false positives under host load.
- (c) A few quick retries at short intervals before declaring unhealthy,
  rather than one long wait — balances speed against reliability without
  picking a single number to get exactly right.

**Your answer:**


---

### OQ-7 — Project/data retention on removal

**Where this comes from.** The last design round established Registry &
Portfolio as the main responsible pillar for a project's lifecycle,
including removal — but only settled *who decides* to remove a project,
never what happens *downstream* across every other pillar that
accumulated data about it (Requirements & Quality's requirement/test
history, Orchestration's job history, Policy & Compliance's check
results, the project-files directory itself).

**What's actually being asked.** When a project is deregistered, is its
data across every pillar deleted immediately, archived, or left alone
until a separate, explicit purge?

**Why it matters.** This is a real data-loss/recoverability question — if
a project is removed by mistake, is its history gone for good? It's also
a storage-growth concern in the same family as `OQ-4`: removed projects'
data lingering forever in every pillar's database is exactly the kind of
thing nobody notices until it's a problem. There's also a coordination
question underneath the retention question: does removal *actively*
tell every other pillar to clean up (a broadcast, via their own Pillar
Interfaces), or does each pillar just naturally end up holding orphaned
rows for a `project_id` that no longer has a registry entry (cheaper to
build, messier over time)?

**Possible interpretations.**
- (a) Hard delete everywhere, immediately, cascading — Registry &
  Portfolio's removal triggers every other pillar to purge that
  project's data via their interfaces. Clean, but no undo.
- (b) Soft delete/archive — the project is marked inactive; other
  pillars' data stays and is excluded from active views; a real purge is
  a separate, later, explicit action.
- (c) Passive orphaning — Registry & Portfolio removes its own row;
  nothing tells other pillars anything; their data for that project
  simply becomes unreachable through normal means. Simplest to build,
  accumulates silently.
- (d) A configurable retention window (soft-delete for N days, then hard
  purge) — a middle ground closer to common SaaS deletion patterns.

**Your answer:**


---

### OQ-8 — Pillar Interface deprecation window

**Where this comes from.** The original pillar-architecture design (deck
2) specified that a Pillar Interface can serve multiple versions at once
(`/v1`, `/v2`) specifically so upgrading one pillar's interface never
forces every caller to move in lockstep. The *mechanism* for running two
versions was designed; the *policy* for how long the old one has to stay
alive was never addressed.

**What's actually being asked.** Is there a minimum support window for an
old PI version before it can be dropped, and if so, based on what — a
fixed duration, actual usage, or a case-by-case judgment call?

**Why it matters.** Too short a window risks breaking a caller that
hasn't upgraded yet — a real reliability concern given pillars are
independently deployable, so perfect coordination between an upgrade and
every caller isn't guaranteed. No bound at all risks the opposite: old,
possibly buggy interface versions kept alive indefinitely, with every
pillar accumulating permanent multi-version maintenance debt.

**Possible interpretations.**
- (a) A fixed minimum window (e.g. "at least N weeks after a new version
  ships") — predictable, similar to a public API deprecation policy.
- (b) Usage-based — keep an old version alive until nothing has called it
  for some period, which needs usage tracking per version but never
  breaks an actually-active caller.
- (c) No formal window — left to whoever owns the pillar to judge each
  time, consistent with how framework-policy changes themselves were
  already resolved to work (continuous development, not an automatic
  rule).
- (d) This might not need to be a *policy* at all, the same way `OQ-1`'s
  entries had to be narrowed or dropped — "how long is reasonable to keep
  an old version alive" may be a judgment call no test script can verify,
  in which case the honest answer is a stated convention, not an
  enforced rule.

**Your answer:**


---

### OQ-9 — "Informed immediately" isn't a universal channel

**Where this comes from.** This is the item that actually motivated
building this tracker: flagged as an open gap when action-gating was
designed, then genuinely dropped from tracking for several rounds until
this document's own creation caught it again.

**What's actually being asked.** When a gated action's policy check fails
and the 10-minute confirm-or-abort window starts, the user has to find
out *immediately* for that window to mean anything. The only two channels
designed are the Dashboard (a banner — requires Dashboard to be enabled
and someone to actually be looking) and a per-project GitHub issue
(requires that specific project to have the GitHub adapter attached). A
project that's headless (Dashboard disabled, a legitimate configuration
for an automation-only setup) *and* has no GitHub remote (also
legitimate — the `little-mapgen` precedent) has no channel at all today.

**Why it matters.** This isn't a cosmetic gap — the entire ten-minute
override mechanism central to how gated actions work is only meaningful
if the notification actually reaches someone in time. Without a real
channel, a gated action for such a project silently times out every time,
and the user never even learns a decision was needed.

**Possible interpretations.**
- (a) A genuinely universal third channel — a desktop/OS notification, a
  push service, an email, something that doesn't depend on Dashboard or
  GitHub being attached to the specific project.
- (b) Make gating itself conditional on having a channel — a project with
  neither Dashboard nor GitHub simply isn't allowed to have gated Job
  types at all, pushing the constraint onto configuration validity
  instead of solving universal notification.
- (c) Reuse whatever mechanism little-runner already has proven, rather
  than invent something new — it already knows how to reach the user for
  comparable situations.
- (d) Accept the gap as a known limitation for now: given the design is
  already fail-closed (a timeout defaults to "nothing happens," never to
  proceeding), a project without a channel just experiences more
  timeouts, which is inconvenient but not unsafe — and decide that's
  acceptable at this project's current scale rather than building a new
  mechanism to close it.

**Your answer:**


---

## Lower priority — implementation-time detail

Not design decisions so much as things to not silently forget once
building starts.

### OQ-10 — Per-pillar runtime dependencies

**Where this comes from.** `docs/architecture.md`'s Dependencies section
states an expected default (Python ≥3.11, stdlib-first, FastAPI/uvicorn
for every Pillar Interface) mirroring the absorbed tools' own existing
choices, without going pillar-by-pillar.

**What's actually being asked.** Whether any pillar needs something
beyond that default, decided now or left for whoever implements it.

**Why it matters.** Low stakes — this doesn't change the architecture,
only what gets installed once code exists.

**Possible interpretations.** Defer entirely to implementation, or note
now any pillar you already suspect will need something unusual.

**Your answer:**


---

### OQ-11 — Per-pillar Bazel build-target shape

**Where this comes from.** `docs/architecture.md`'s Build section expects
one Bazel package per pillar, matching the standard target names this
ecosystem already uses (`//:wheel`, `//:install`, `//:test`, …), without
specifying the exact shape per pillar.

**What's actually being asked.** Same category as `OQ-10` — whether this
needs deciding now or is naturally implementation-time work once each
pillar's actual artifact types are known.

**Why it matters.** Low stakes, same reasoning as `OQ-10`.

**Possible interpretations.** Defer to implementation, or flag now any
pillar you expect to need a non-standard target.

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
