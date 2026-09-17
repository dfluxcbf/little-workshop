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
- Each open item has seven parts: **where it comes from** (the design
  round or gap that surfaced it); **first designed** (when the
  mechanism the question is actually about was first designed —
  traced to the presentations deck or implementation commit that
  introduced it, not the date the question itself was raised, when the
  two differ); **design goal** (what that original mechanism was for,
  in the terms it was actually designed in); **pillars affected** (which
  pillar(s) own the mechanism in question and which others call into or
  depend on it, so the blast radius of any answer is visible up front);
  **what's actually being asked** (in plain terms, not just the original
  one-line summary); **why it matters** (what actually changes depending
  on the answer — nothing here is asked just to have an answer on file);
  and **possible interpretations** (a starting menu, not an exhaustive
  one — picking none of them and saying something else is completely
  fine).
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

**First designed.** 2026-09-16, commit `5d76ae1` ("Policy usability
refinement: enable/disable, waivers, per-policy gating") — `policy.
waiver_grant`/`waiver_revoke` and `policy.models.PolicyWaiver` did not
exist before this commit; there is no earlier design to trace this back
to.

**Design goal.** Framework tier's original all-or-nothing "never
overridden by anything" left no usable way to say "this one project
genuinely can't comply" — requested directly, right after M12/M13
shipped, once that gap was hit in practice. The waiver was designed as
an explicit, auditable, reversible per-project exception, deliberately
*not* a weakening of scope precedence: a framework policy still can
never be silently shadowed by a lower-scope policy; a waiver is a
distinct, separate act, granted per project, never a change to the
policy itself.

**Pillars affected.** Policy & Compliance owns the mechanism entirely —
the `policy_waivers` table, both Job types, and `policy/precedence.py`'s
skip-if-waived check. Orchestration is a secondary dependent: `GET /v1/
policies/gating-for-job` (called from `orchestration/policy_gate.py`'s
`resolve_gating_policy_ids`) already excludes a waived policy from what
it reports as gating a Job, so any answer here that adds a review/expiry
step changes what that endpoint can return, not just what Policy &
Compliance stores. No other pillar calls into the waiver endpoints
directly today.

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

### OQ-14 — `dispatch_job`'s fixed 20s timeout is too short for any Job type that genuinely calls the real Claude CLI

**Where this comes from.** Surfaced 2026-09-16 running M15 (self-hosting)
for real: authoring little-workshop's own first framework policy through
`policy.author` against the *real* Claude CLI (not the fast, deterministic
fake-`claude` stub every test suite uses) succeeded once, then failed
twice more with `"target pillar unreachable: timed out"` on a
resubmission of the identical request — never a Gate rejection, always a
timeout. `orchestration/dispatch.py`'s `dispatch_job` hardcodes
`timeout: float = 20.0`, and nothing in `POST /v1/jobs`'s request body
or `dispatch_ready_jobs`'s call site currently forwards a caller-supplied
value through to it — the `timeout` a caller like `cli.client.create_job`
passes only bounds *that caller's own* wait for Orchestration's HTTP
response, never Orchestration's own internal call to the target pillar.
Every existing test passes because the fake `claude` stub responds in
milliseconds; this is genuinely invisible until a real, slow LLM call
sits behind a gated Job type.

**First designed.** 2026-09-15, commit `70b2117` ("Implement M1 (Core +
Orchestration)") — the architectural decision to dispatch a Job to its
target pillar's Pillar Interface over a Unix socket dates to
`presentations/03-execution-model.md`, 2026-09-14; the specific
`timeout: float = 20.0` constant was fixed when `dispatch_job` was first
written the next day and has never been revisited since, including
through M12's own addition of `agent.query`-backed Job types.

**Design goal.** A bounded wait for a Pillar Interface call, so one
unreachable or hung pillar can never stall Orchestration's queue
indefinitely — every dispatch gets a real, finite answer (success,
failure, or "unreachable") rather than hanging forever. 20 seconds was
never a considered number for anything beyond M1's own reference-pillar
proof; nothing about it was chosen with a real, slow Job type in mind,
because none existed yet.

**Pillars affected.** Orchestration owns `dispatch_job` and is the only
pillar this timeout lives in. Every other pillar is an indirect
dependent purely by being a dispatch *target* — but the two that
actually reach it in practice today are Policy & Compliance
(`policy.author`/`policy.activate`, both real Claude-CLI-backed) and,
through them, Agentic Development (`agent.query`, the call that's
actually slow). Any Job type in any future pillar that shells out to
something slow inherits this same ceiling automatically, with no opt-out
today.

**What's actually being asked.** How should a Job type whose real
implementation can legitimately take longer than 20 seconds (today:
`policy.author`/`policy.activate`'s `agent.query` calls; potentially any
future Job type that shells out to something slow) get a longer
dispatch-side timeout, without just raising the global default and
silently changing every other Job type's own failure-detection latency?

**Why it matters.** Right now, the *only* two real end-to-end proofs of
the authoring pipeline against a real Claude CLI (`docs/requirements.md`'s
REQ-000000131/132, and this pass's own single successful M15 draft) each
happened to complete inside 20 seconds by chance. A framework-policy
authoring session that takes slightly longer — a slower API response, a
longer prompt, a busier agent sandbox — fails with a misleading "pillar
unreachable" rather than a real Gate rejection or a real success,
indistinguishable from Agentic Development's own unit actually being
down.

**Possible interpretations.**
- (a) A per-Job-type timeout table (mirroring `orchestration/
  policy_gate.py`'s `JOB_TYPE_CATEGORIES` pattern) that `dispatch_ready_jobs`
  consults before calling `dispatch_job`, so `policy.author`/`policy.
  activate` (and any future slow type) get a longer ceiling without
  touching the default for everything else.
- (b) Let the original `POST /v1/jobs` request body carry an optional
  `dispatch_timeout_seconds`, threaded through from Job creation to the
  eventual `dispatch_job` call — caller-controlled, no hardcoded table to
  keep in sync as new slow Job types appear.
- (c) Leave the fixed default alone and treat this as confirmation that
  `agent.query`-backed Job types need their own retry/resubmit story at
  the caller level (a failed dispatch due to timeout is not currently
  distinguished from a genuinely down pillar, so a caller can't tell
  which one happened and react differently).

**Your answer:**

---

### OQ-15 — A `project_registration`-gating policy can't be project-bound, only framework-bound

**Where this comes from.** Surfaced 2026-09-16 writing this pass's first
real end-to-end proof of the `project_registration` gate point
(`tests/integration/test_policy_end_to_end.py`): a project-bound policy
(`target_pillar` naming the pillar that owns the action, e.g.
`registry`, scope `all_projects`) gating `project.bootstrap` makes its
own `policy.check_project` dependency fail outright — not a `fail`
verdict, a genuine Job failure — because `policy/app.py`'s
`_mounts_for_policy` tries to resolve the *new* project's own checkout
path to bind-mount it into the sandbox, and that project doesn't exist
in Registry & Portfolio yet (registering it is the very Job being
gated). The proof test works around this by authoring a framework-bound
policy instead (`target_pillar == "policy"` itself, checking against
little-workshop's own `src`/`docs` — the one case `_mounts_for_policy`
already treats specially), which sidesteps the chicken-and-egg problem
but can't express "check something about the *incoming* project before
letting it register," only "check something about little-workshop
itself before letting any project register."

**First designed.** The mechanism this question actually collides with —
`_mounts_for_policy`'s project-bound-vs-framework-bound split — was
introduced 2026-09-16, commit `24396f4` ("M12: Policy & Compliance"),
following the sandbox design in `presentations/13-test-script-sandbox.md`
(2026-09-15). The `project_registration` gate point that exposes the
collision is much newer: commit `e4a7324` ("Add four gate-point
categories…"), later the same day.

**Design goal.** `_mounts_for_policy`'s two modes were designed for the
two cases M12 actually needed: a project-bound policy checks something
about one real, already-registered project (mount its checkout at
`/project`); a framework-bound policy checks something about
little-workshop itself (mount `src`/`docs` at `/framework/...`). Neither
mode was designed with "check a project that doesn't exist in the
registry yet" in mind, because no gate point needed that until
`project_registration` was added four months later in project time (same
day in wall-clock time).

**Pillars affected.** Policy & Compliance owns `_mounts_for_policy` and
the sandbox it feeds. Registry & Portfolio owns the two Job types this
gate point actually gates (`project.bootstrap`, `project.import`) and is
the pillar a project-bound policy would need to resolve the *incoming*
project's path against, if interpretation (b) below were built.
Orchestration is a pass-through here — it dispatches the gated Job and
holds it at `pending_confirmation` exactly as it does for every other
gate point, unaffected by which mode the check itself runs in.

**What's actually being asked.** Is a project-bound `project_registration`
gate a real, wanted capability (e.g. "the incoming project's directory
must already be a git repo," checkable against the path in
`project.import`'s own params without needing the sandbox's project-dir
mount at all), and if so, should `_mounts_for_policy`/the sandbox gain a
third mode for "check the pre-registration params/path directly," or is
framework-bound-only a fine permanent restriction for this one gate
point specifically (distinct from every other gate point, which all gate
an action against an *already-registered* project)?

**Why it matters.** Right now a maintainer authoring a
`project_registration`-gating policy with `target_pillar` set to the
project's owning pillar (the natural choice, matching every other gate
point's own convention) gets a confusing genuine Job failure rather than
a clean Gate-time rejection explaining why — nothing catches this at
authoring time, only at check time, against a real project attempting
to register.

**Possible interpretations.**
- (a) Leave it framework-bound-only, and have Gate 3 (or a new,
  earlier check) reject a project-bound `target_pillar` combined with
  `project_registration` in `gate_points` at authoring time, with a
  clear error, rather than letting it fail confusingly later.
- (b) Add a third `_mounts_for_policy` mode specifically for
  pre-registration checks: mount whatever `project.bootstrap`/`project.
  import`'s own params describe (e.g. `dest_dir`/`path`) directly,
  never a resolved-from-the-registry project directory.
- (c) Treat this as evidence `project_registration` needs its own
  distinct check mechanism entirely, separate from `policy.check_project`
  — future work, not a small fix.

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
