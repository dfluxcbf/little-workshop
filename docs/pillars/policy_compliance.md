# Policy & Compliance — pillar specification

The first pillar specified end-to-end, per `presentations/05-status-and-roadmap.md`'s
proposed next step (that deck suggested Version Control & Release as the
narrowest starting point; Policy & Compliance was chosen instead, because
before this pillar can check anything, it needs its own rules for what a
rule *is*). See `docs/design.md`/`docs/architecture.md` for this pillar's
place in the whole system; this document is everything specific to it.

## Three tiers of rules, not one

Getting this pillar right meant separating three things this project had
been discussing as if they were one:

1. **Hard-coded invariants** — code, not data. "Automation never merges,
   deletes, or finishes on its own initiative," "the agent never touches
   branches when a Job specifies them." These live inside the Orchestration
   and Agentic Development pillars' own source and are never expressed as a
   policy at all — not even a framework one. They don't go through this
   pillar because they can't be safely made editable by *anyone*, including
   little-workshop's own maintainers, without reopening the exact class of
   accident they exist to prevent.
2. **Framework policies** — data, authored the same way as any policy (see
   below), but created by little-workshop's own maintainers rather than an
   end user, always scoped to all projects, and never overridable by a user
   policy at any level. "Every pillar exposes a health endpoint" is a
   framework policy, not a hard-coded invariant: it's a real rule with a
   real test script, just one only little-workshop's own maintainers can
   write or change.
3. **User policies** — data, authored by whoever is managing their own
   projects through little-workshop, scoped to one project, a project set,
   or all projects, and free to be anything that doesn't contradict a
   framework policy.

Only tiers 2 and 3 are "policies" in this pillar's own sense — both go
through the identical authoring and enforcement pipeline described below.
Tier 1 is out of scope for this pillar entirely, by design.

## Scope levels and precedence

A policy is scoped to exactly one of: a single project, a project set, or
all projects. Project sets themselves — a named, user-managed group of
projects (create, rename, add/remove member projects) — are owned by
Registry & Portfolio, not this pillar: grouping projects is a registry
concern, resolved once Registry & Portfolio was established as this
system's main responsible pillar for project identity and lifecycle (see
`docs/architecture.md`'s Registry & Portfolio entry). Policy & Compliance
reads project-set membership through Registry & Portfolio's Pillar
Interface when resolving which projects a project-set-scoped policy
actually covers — it never stores that membership itself.

Two policies can only conflict if they govern the *same concern* — so every
policy declares a short, stable **topic** (e.g. `server-manifest-shape`,
`branch-naming`). Precedence is resolved per topic, per project:

```
framework policy on this topic          (always wins if one exists)
  > user policy, this project specifically
  > user policy, a project set this project belongs to
  > user policy, all projects
```

Only user policies can be shadowed this way — a framework policy is never
overridden by anything, which is the entire point of "little-workshop's
own policies always have priority: this ensures they are not overwritten
by mistake." A higher-precedence policy shadows a lower one only *for the
projects it actually covers* — the lower one still governs every other
project untouched by something more specific.

**Worked example**: an all-projects user policy says every project must
declare a `VERSION` file (topic `version-file-presence`). A project-specific
user policy for `little-mapgen` (topic `version-file-presence`, since that
project deliberately has none — see its own `docs/architecture.md`) relaxes
that. `little-mapgen` is governed by its own project-specific policy on
that topic; every other project still answers to the all-projects one. If a
framework policy ever exists on `version-file-presence`, it would govern
every project including `little-mapgen`, and neither user policy could
touch it.

## Policy ownership: every topic belongs to one pillar

Resolved 2026-09-15, clarifying what "no duplication of policy" actually
means — the earlier "no duplicate configuration" language
(`docs/architecture.md`'s Configuration & project data) was about project
*files*, not policies, and had never been given its own precise statement
for policies until now.

Every policy has a **`target_pillar`** — the one pillar whose behavior it
governs, the pillar that has to *follow* it, distinct from Policy &
Compliance, which *guards* every policy regardless of whose behavior it's
about. A topic (`server-manifest-shape`, `branch-naming`, ...) always
maps to exactly one `target_pillar`, consistently, everywhere it's used:

- Two policies can't declare the same topic with two different
  `target_pillar`s — that's not a scope-level conflict (Gate 3a already
  catches those), it's an ownership conflict, and it's just as
  mechanically checkable: does this topic already exist anywhere,
  targeting a different pillar? Reject if so.
- This is what "two or more pillars should not have the same policy"
  and "pillars should not have conflicting policies" actually cash out
  to: not that no two policies may ever resemble each other, but that a
  given concern has exactly one pillar accountable for it, never two
  pillars each holding their own version of what should be one rule.
  It's the same "one owner per capability" discipline already applied to
  Pillar Interfaces and project files, now applied to policy topics.

Gate 3's structural check (below) is extended to check this alongside the
existing scope-level+target+topic check — both are exact, mechanical, and
never guess.

## Definition-of-done is multi-pillar, and can conflict with a request

Resolved 2026-09-15. A milestone or task's definition-of-done isn't one
thing owned by one pillar — each pillar a task actually touches has its
own done-criteria (Requirements & Quality's is
`milestone-requirements-linked-and-passing`: requirements linked, tests
passing; Deployment & Servers' might be "the declared version is actually
running"; and so on), and a task's overall done-criteria is the
composition of whichever of those it touches.

A user may specify their own definition-of-done for a task, but it always
implicitly includes policy compliance — it's never a way to opt out of
one. If what the user asks for would violate a policy, the system does
neither of the two easy things: it doesn't silently execute it (violating
the policy) and it doesn't silently refuse it (leaving the user with no
path forward). It asks the user to **refine the request**, through the
same Claude-CLI-mediated, human-reviewed pattern this document already
uses for EARS refinement — explain the conflict, then help find an
alternative that satisfies what the user actually wants without violating
the policy.

**Worked example**: a user's stated definition-of-done is "the server
keeps restarting even if it keeps crashing." A policy says a broken
server shall be replaced with its latest known-working version, not left
running. These conflict. Refinement surfaces the conflict and proposes:
run the crashing version separately, on a different port, alongside the
policy-compliant server — the user may have their own reasons to keep
watching the crashing behavior, and this satisfies that without the
policy-compliant server ever being affected by it.

This reuses Agentic Development's Pillar Interface exactly the way EARS
refinement does — refine and hand back for review, never decide
unilaterally which one wins.

## Optionality is EARS' job, not a separate mechanism

"A policy is optional because a project doesn't need a server" is not a
flag on the policy record — it's what EARS' own **optional feature**
pattern already expresses: `WHERE <feature is present>, the <project> shall
<response>`. A policy about server manifests is written `WHERE a project
declares a server, the project shall...` — for a project with no server,
the policy's test script reports **not applicable**, not a pass and not a
failure. The recommended mechanical convention (implementation detail,
proposed not mandated): a pytest-based test script uses `pytest.skip()` for
the not-applicable case, distinct from an assertion failure — this
ecosystem already standardizes on pytest, and `skip` is exactly the
three-way outcome (pass/fail/not-applicable) this needs, with no new
convention invented.

## The EARS requirement

Every policy's natural-language form must be one of EARS' patterns
(ubiquitous, event-driven `WHEN`, state-driven `WHILE`, unwanted-behavior
`IF...THEN`, optional-feature `WHERE`, or a combination) — never free
prose. This is what makes a policy's own EARS text and its test script
inspectable against each other by a human, not just by whatever wrote the
script.

## Authoring a policy

Three gates stand between a natural-language submission and a generated
enforcement artifact, and a fourth stands between that artifact and a
saved, active policy. All four must pass — in order, each blocking the
next:

```
user writes a policy in natural language
   (Dashboard: "your projects" tab, or the separate "little-workshop
   itself" tab -- see below -- for a framework policy)
   │
   ▼
GATE 1 -- EARS-compliant?
   │  no → Policy & Compliance calls Agentic Development's Pillar
   │        Interface: "rewrite this into EARS form" -- narrowly scoped,
   │        no file writes, no branch, no side effect of any kind.
   │        Claude CLI returns ONLY the refined text -- never applies it,
   │        never decides unilaterally, the same non-negotiable rule this
   │        project already applies to branching, applied here to policy
   │        text instead. User reviews -- approves, edits, or asks again.
   ▼  yes / approved
GATE 2 -- mechanically enforceable? Can an automated test script verify
   this with no human judgment call inside the check itself?
   │  no  → REJECTED. Not saved anywhere, in any form. Told to the user
   │        as: this belongs in prose documentation, not the policy
   │        system -- "if it can't be enforced, it isn't defined here."
   ▼  yes
GATE 3 -- non-conflicting? Three checks, two mechanical and exact, one
   best-effort:
   │  a) structural: does a policy already exist at this exact scope
   │     level + scope target + topic? Mechanically checked, no
   │     ambiguity possible -- two policies can never govern the same
   │     project on the same topic at the same level.
   │  a2) ownership: does this topic already exist anywhere, targeting a
   │     different target_pillar? Also mechanical and exact -- a topic
   │     belongs to one pillar, always (see "Policy ownership" above).
   │  b) semantic: Policy & Compliance calls Agentic Development's
   │     Pillar Interface -- Claude CLI checks the proposed EARS text
   │     against every existing policy's EARS text for contradiction.
   │     Best-effort, not provable in general (nothing can prove two
   │     natural-language requirements do or don't contradict with
   │     certainty) -- anything flagged is surfaced to the user to
   │     resolve, never silently allowed and never silently merged.
   │  either check fails → REJECTED, not saved, user told which existing
   │     policy it conflicts with
   ▼  passes both
Policy & Compliance calls Agentic Development's Pillar Interface again:
   "write the one test script that enforces this EARS requirement" --
   Claude CLI decides the script's own logic and which specific files it
   needs to read (within whatever pool Gate 2's enforceability check and
   the sandbox's project-bound/framework-bound split already constrain it
   to) -- a proposal, not yet anything saved or executable against real
   state
   │
   ▼
GATE 4 -- full user approval, every time, no exception: the user reviews
   BOTH the EARS text AND the generated test script (which is also how
   the file-read allowlist actually gets reviewed -- it's read directly
   off the script the human is already looking at, not a separate
   declaration to approve twice) before anything is saved
   │  not approved → discarded or sent back for another authoring pass;
   │                 nothing is saved
   ▼  approved
policy record saved and active: EARS text + a 1:1 reference to its one
   test script + topic + scope level + scope target (project / project
   set / all) + created_by (user | framework)
```

No policy — created or edited, user-tier or framework-tier — ever
activates without this. Editing a policy re-runs the entire flow, Gate 4
included: an edit is "replace this policy's EARS text and script," never
a shortcut around review.

Two separate Claude CLI calls across Gates 1-3, two different scopes of
trust, and both stay strictly proposals until Gate 4: the EARS-refinement
call (Gate 1) is text-in, text-out; the conflict check (Gate 3b) is
read-only judgment, never a write; the script-writing call produces a
draft artifact that has no effect on anything until a human has looked at
it. Claude CLI's role throughout is *drafting* — every artifact it
produces, at every step, is something a human reviews before it becomes
real, never something that activates on Claude CLI's own authority.

Every Claude CLI call in this flow goes through Agentic Development's
Pillar Interface — this pillar never reimplements agentic execution
locally, the same "a new capability maps to an existing pillar's
interface, never duplicated" rule `docs/architecture.md` already states.

### Policy & Compliance is out of user reach

A user policy's scope target and gating topic shall never resolve to the
Policy & Compliance pillar itself — checked at Gate 3 alongside the
conflict check, rejected outright if violated. The referee cannot be
altered by the rules anyone using it can write. Framework policies are
exempt from this restriction and are in fact expected to eventually govern
Policy & Compliance's own conformance — see "the little-workshop itself
tab" below and `docs/pillars/policy_catalog.md`'s tier K, which already
includes candidates like `policy-one-to-one-test-script` checking this
pillar against its own rule.

### User-policy lifecycle

A user may create, edit, or remove a user-level policy at any time — no
restriction on timing. Editing or removing is not a distinct, lighter-
weight path: an edit is "replace this policy's EARS text," so it goes
through all three gates again, exactly as a new policy would, including
being re-checked for conflict against every *other* policy (its own prior
version doesn't count against itself).

### Framework-policy lifecycle

"Never overridden by a user policy" and "never revised" are different
claims — only the first is true. A framework policy is a hard rule while
it's in force (nothing overrides it, no exception, no timeout, no
confirmation dialog gets around it), but little-workshop is itself still
under active development, and a framework policy can turn out to be wrong,
incomplete, or superseded by better project design the same way any other
part of this system can. Little-workshop's own maintainers can edit or
remove a framework policy — through the same "little-workshop itself" tab,
the same three gates as authoring one fresh.

What's different from a user edit is what the change is *part of*: a
framework-policy edit is treated as ordinary, continuous development of
little-workshop itself — the same discipline `docs/governance_policy.md`'s
evolution principle already states for this project's rules in general
("deliberate... documented in the file itself... dated"), not a casual
runtime toggle. In practice this means a framework-policy change belongs
in the same place any other design decision does — a milestone, a
documented reason, a date — rather than being indistinguishable in the
audit trail from a user tweaking their own project's policy on a whim.
The three-gate pipeline still enforces the mechanics (EARS-compliant,
enforceable, non-conflicting); this is the additional expectation on top,
specific to changing a rule everything else in the ecosystem is required
to obey unconditionally.

## Enforcement

One policy, one test script — a strict 1:1 relationship; no script is
shared between policies, no policy has more than one script. Checking a
policy against a project is a Job (`policy.check_project`, `{policy_id,
project_id}`); checking it everywhere it applies is
`policy.check_all(policy_id)`, which fans out into one
`policy.check_project` per applicable project — Orchestration's ordinary
Job/Pipeline mechanism, nothing new needed at the execution layer.

Running a test script needs an isolated checkout of the target project,
the same requirement Requirements & Quality's own test execution already
has. Policy & Compliance does not reimplement worktree provisioning a
second time — both pillars call Deployment & Servers' Pillar Interface for
an isolated worktree, exactly the "one owner per capability" discipline
this project already applies everywhere else.

Three outcomes per `(policy, project)` check, not two: **pass**, **fail**,
or **not applicable** (the EARS `WHERE` case above).

## Test-script execution sandbox

A generated test script is unattended, AI-written code, re-run repeatedly
against real project state. "Isolated worktree" (above) solves a
*concurrency* problem — it doesn't step on a human's checkout — not a
*safety* problem: a worktree is still just a directory a normal OS process
can do anything to. This section is that boundary, stated concretely
rather than assumed.

**Network — Pillar Interfaces only, read-only calls only.** A test script
has no general network access at all — no external HTTP, no DNS, nothing.
The only thing it can reach is other pillars' Pillar Interfaces, through a
mediating library the same way every other cross-pillar call works, and
only their **safe** endpoints: get information, or request something
computed, but never anything that mutates running state (no triggering a
deploy, no writing through another pillar's interface, no side effect on
anything currently running). The practical mechanism: every PI endpoint is
classified safe (read/compute) or mutating (the usual HTTP-verb convention
— `GET` is safe, anything that changes state isn't) at the point it's
defined, and the sandbox's network namespace has no route to anything
except the Unix-socket directory PI calls use, with only safe-classified
requests actually forwarded.

**Filesystem — read-only, allowlisted, scoped to project-bound or
framework-bound.** Every policy is one or the other, not both, determined
by what it actually checks:

- **Project-bound** (a policy scoped to a project, a project set, or all
  projects — i.e. every user policy, and any framework policy that checks
  project state): read access to that project's repository checkout and
  the relevant files under `~/.config/little-workshop/projects/<project>/`.
  Nothing outside those two locations, and never another project's.
- **Framework-bound** (a framework policy checking little-workshop's own
  architecture — the tier-K style candidates in `policy_catalog.md`):
  read access to a strictly enumerated set of little-workshop's own files,
  not an open license to read anything in its state directory.

Both are read-only. The natural implementation is little-workshop's
existing optional Docker sandbox backend (already designed for
Requirements & Quality's test execution) — for policy checks specifically
it stops being optional: a container's own mount namespace is what turns
"an allowlist" into something actually enforced, bind-mounting in exactly
the paths a policy's scope grants and nothing else.

**Write — verdict and Policy & Compliance's own files, nothing in the
project.** A check never writes into the project it's checking, at all —
read-only there is absolute. The only writes anywhere are: the verdict
itself, using the same pattern Agentic Development already uses for a run
result (a structured file at a harness-provided path outside every
bind-mounted read path, so it can never be confused with — or overwrite —
project content), and any project file already tagged with this pillar's
own `.policy` owner tag, per `docs/architecture.md`'s existing
Configuration & project data convention. A policy script's write access is
exactly Policy & Compliance's own write access, never broader.

**Resource bounds — an async Job, not a blocking call, thirty minutes.**
A gated Job does not block Orchestration waiting on a synchronous check:
Orchestration creates a `policy.check_project` Job and the gated Job
`depends_on` it — the same dependency mechanism every other Job already
uses, nothing new at the execution layer. The check Job gets a 30-minute
maximum runtime, forcibly enforced (the sandboxed script is terminated,
not just abandoned) — a framework design decision to guarantee nothing
gets stuck, not a policy-enforced constraint and not something
Policy & Compliance's rule system produced an answer to. EARS-scoped
policies are small by construction; this ceiling is against a hang or a
bug, never an expected duration. A script that actually completes and
returns `fail` is what starts the separate ten-minute human-confirmation
window in "Action-gating checks" below — a timeout never does, because
there's no determined violation there for a human to knowingly bypass;
see that section for the full distinction.

## Informing the user immediately

A failing check is recorded the moment its Job completes, not batched.
Three channels, reusing patterns already proven in this ecosystem rather
than inventing new ones:

- **The Dashboard**, when running, surfaces it immediately — the same
  "warn" pill treatment already designed for the Home/Portfolio and
  Pillars screens.
- **A per-project GitHub issue**, when that project has the GitHub adapter
  attached — opened or updated exactly the way little-runner's existing
  branch audit already does (one persistent, diffed issue per repo, never
  auto-assigned into the agentic pipeline).
- **Remote Control**, resolved 2026-09-15 as the actually-universal
  channel: the same mechanism Agentic Development already uses to expose
  a live Claude Code session to the user's own Claude app, and the same
  one `little-runner`'s design already proved for exactly this shape of
  problem — its `--brief` mode "adds the user-messaging tool so the agent
  can ask a question mid-run instead of guessing." Policy & Compliance
  asks Agentic Development's Pillar Interface to open a named,
  Remote-Control-visible session whose only job is delivering the warning
  and awaiting a response — a new *use* of an existing capability, not a
  new mechanism, and unlike the other two channels it doesn't depend on
  Dashboard being enabled or GitHub being attached to the specific
  project, because it's tied to the user's own Claude account rather than
  per-project configuration.

Dashboard and GitHub issue remain useful — the Dashboard for anyone
already looking at it, the GitHub issue as a persistent record on the
project itself — but Remote Control is what actually closes the gap
`OQ-9` raised: the one channel guaranteed to exist regardless of a
project's own configuration.

**A fourth mode worth noting, not yet decided into the design**: this
ecosystem already has a proven pattern for *ambient*, low-stakes
awareness that's lighter than any of the three channels above — a
`PostToolUse` hook (`.claude/hooks/branch_audit_check.py`) that re-runs
the branch audit opportunistically after a git command, rate-limited
(at most once every 5 minutes) and purely informational (a
`systemMessage`, never blocking, never gating anything). Nothing above
plays that role for Policy & Compliance — every existing channel is tied
to either a genuine failure (Dashboard/GitHub/Remote Control) or a
scheduled audit. Whether something like it belongs here too, for
surfacing a passive compliance signal without the weight of a full
notification, is left as a future consideration, not a decision made by
importing the pattern's existence.

## The "little-workshop itself" tab

A framework policy is authored through the identical pipeline as a user
policy — same EARS check, same two Claude CLI calls, same 1:1 script — the
only difference is who's allowed to start that flow (little-workshop's own
maintainers, not an end user) and what gets set automatically once it's
saved (`created_by: framework`, scope forced to all-projects, and marked
never-overridable). The Dashboard's policy-creation screen needs a visibly
separate mode for this rather than a checkbox buried in the normal flow —
a design requirement for whenever that screen is actually built, not
designed in detail here.

This is also where `docs/governance_policy.md`'s three prose principles
(independence, evolution, self-application) eventually become real,
checkable policies instead of prose only this project's maintainers read —
and per the self-application principle itself, little-workshop is expected
to register itself as a project in its own Registry & Portfolio once that
pillar exists, so framework policies apply to little-workshop exactly as
they apply to everything else it manages, not as a special case.

## Action-gating checks

Distinct from the on-demand/scheduled audit above (`policy.check_all`,
checking everywhere a policy applies, at leisure): some Jobs must not run
at all until the policy governing their action passes. A Job *type*
declares which topic(s), if any, gate it — most declare none, and dispatch
with no policy involvement whatsoever, sparse and opt-in rather than a
blanket check on everything. `server.launch` and `server.deploy`, for
example, are gated by whichever policy currently wins (the usual
precedence resolution) on a server-related topic, for the target project.

### Deciding whether a Job type is gated

Resolved 2026-09-15. Rather than a single rule, a checklist to apply when
a pillar defines a new Job type — use whichever categories are relevant;
none of these are individually decisive, and silence should never
default to ungated:

**Reversibility** — gate if undoing the effect needs another Job of
equal or greater consequence, a restore from backup, replaying history,
or manual reconstruction. Leans ungated if the effect expires or resets
on its own in a short, bounded window, or only ever touches a draft/
staging/scratch area with no promotion step taken yet.

**External visibility** — gate if the effect becomes visible to users,
customers, or the public before a human could plausibly review it, or if
it changes what other services/Jobs treat as ground truth. Leans ungated
if the effect stays internal to the same actor who triggered it, or is
purely observational (a report, a log entry, a computed value) with no
side effects.

**Cost and resource consumption** — gate if the Job spends real money
above a trivial threshold, or provisions/de-provisions billed
infrastructure. Leans ungated if it consumes only negligible, capped
internal compute/storage.

**Blast radius / scope** — gate if the effect can cascade into other
Jobs/pipelines, or operates on a bulk/wildcard selection rather than one
named target. Leans ungated if it's scoped to the actor's own resources,
or to a single isolated object nothing else references.

**Data sensitivity** — gate if the Job deletes or overwrites data with no
independent backup, or exposes/exports/transmits sensitive or regulated
data (PII, credentials, financial). Leans ungated if it only reads data
already fully visible to the requesting actor, or reads without
persisting or forwarding it anywhere.

**Access and permissions** — gate if the Job revokes access in a way that
could lock out a legitimate user or break a running system.

**Release / deployment lifecycle** — gate if the Job causes new code,
config, or content to start serving live traffic, or promotes an
artifact from non-production to production. Leans ungated if it only
builds, compiles, or tests without deploying or publishing (default open
for rollback to a previously known-good state, unless the rollback itself
has side effects).

**Policy interaction** — gate if a relevant policy exists that could
plausibly forbid the action if violated, or if the Job's inputs are
attacker- or user-controllable in a way that could steer it outside
intended bounds regardless of how mild the nominal action seems. If no
policy topic currently governs anything the Job type touches, flag it for
review rather than silently deciding either way. Leans ungated if the
Job's behavior is fully deterministic and enumerable at review time, with
no runtime branching into riskier paths.

**Frequency and batching** — leans ungated if the Job runs at high
frequency as part of normal automated workflows and per-invocation risk
is genuinely low; leans gated if it's rare, deliberate, and typically
invoked by a human directly. If gating would break an automated pipeline
that must run unattended, scope the policy so the legitimate automated
case passes without a human in the loop — never remove gating just for
convenience.

**Actor and context** — gate if the Job can be triggered by a lower-trust
or external actor, not just trusted internal operators — and gate
regardless of who can trigger it if effect severity is high, even for a
small, pre-vetted set of privileged operators. Gate if the Job's effect
depends on ambient context (time, environment, current system state)
that could make an otherwise-safe action unsafe.

**Detectability after the fact** — leans ungated if a periodic audit
would catch a bad outcome in time to matter, and the effect is
self-documenting and easy to diff against expected state. Gate if a
periodic audit would only catch the outcome after damage has already
propagated or become irreversible, or if the effect is silent, hard to
detect, or easily masked by later activity.

**Category defaults** — as a first pass by name: a destructive verb
(delete, purge, revoke, terminate, deprovision) or a promotion/publish
verb (deploy, release, publish, launch, merge-to-prod) defaults gated; a
read/inspect verb (get, list, describe, preview, diff) defaults ungated.
Any Job type that doesn't clearly match an existing pattern is itself a
signal — silence should never resolve to ungated.

```
Orchestration is about to dispatch a Job of a gated type
   │
   ▼
Orchestration creates a policy.check_project Job for the winning policy
   on this Job's topic(s) and this project, and sets the gated Job's
   depends_on to include it -- an ordinary Job dependency, not a
   synchronous blocking call; the check runs in its sandbox (above),
   up to its 30-minute ceiling
   │
   ▼
policy.check_project resolves one of two fundamentally different ways:

   VERDICT REACHED (the script ran to completion): pass | fail |
   not_applicable
      │  pass or not_applicable → dependency satisfied, gated Job
      │                           dispatches, nothing else happens
      ▼  fail
   Job → pending_confirmation (a new Job status, alongside
      queued/running/succeeded/failed/cancelled)
      │
      ▼
   user is warned immediately -- which policy failed, on which project,
      and that the Job is waiting (Dashboard banner; the same treatment
      already designed for an existing confirm-gated deploy's banner) --
      this is the card `docs/pillars/policy_catalog.md`'s Q1 called it:
      "if the user actually intended to break policy, they have 10
      minutes to say so"
      │
      ├─ confirmed within the window (10 minutes -- matching
      │  little-runner's own existing confirm_timeout_seconds default
      │  exactly, reused rather than picked fresh) → Job dispatches, the
      │  action happens exactly as if the check had passed
      └─ window elapses, no response → Job = failed, reason = "policy
         check unconfirmed" -- read as: this was probably broken by
         mistake, not on purpose. The action NEVER happens. A fresh
         launch: the server stays not running. A replace: Deployment &
         Servers' build-before-stop sequence never starts in the first
         place, so the old version was never touched and there is
         nothing to roll back either.

   NO VERDICT (the 30-minute ceiling was hit): not a policy outcome at
   all -- a framework-level fault, the same category as a stuck process
   Core would want to know about, not something Policy & Compliance's
   rule system produced an answer to
      │
      ▼
   the sandboxed script is forcibly terminated (the ceiling is an active
      guarantee against a stuck script, not a passive wait-and-give-up)
      │
      ▼
   Job = failed immediately, reason = "policy check did not complete" --
      NO pending_confirmation, NO ten-minute window. There is nothing for
      a human to knowingly bypass, because nothing was determined to be
      a violation -- only that the check itself is broken. The action
      never happens, and this is surfaced to the user as something to
      go fix (most likely a bug in the generated script), with messaging
      that reads distinctly from "a policy failed."
```

Two clocks with different jobs, never both on the same path. The
30-minute ceiling is a framework design decision, not something Policy &
Compliance's rule system enforces — the same category of self-protection
as Core refusing to let a crashed pillar spiral, not a policy verdict, and
EARS-scoped scripts are expected to need nowhere near it. The ten-minute
window is the deliberate-bypass card, and it only ever starts once a
script has actually run to completion and returned `fail` — never after a
timeout, because there's no known violation there to knowingly bypass.

This refines and supersedes `docs/design.md`'s original job-dispatch
diagram, which only said a failing pre-job policy check "fails (required
rule) → Job = failed" — written before this mechanism existed. The
outcome now depends on whether anyone answers in time, not on the check
result alone.

### Two confirm-gates, not one mechanism reused twice

Both share a skeleton — mark pending, arm a timeout, wait, do the safe
thing on timeout — but they answer different questions at different
points in a Job's life, so they stay two named things with two owners:

| | Deploy confirmation (existing) | Policy confirmation (this) |
|---|---|---|
| Owner | Deployment & Servers | Orchestration |
| Arms | *After* the new version is already running | *Before* the action has started at all |
| Timeout default | Roll back to the last confirmed commit | Never start — there's nothing to roll back |
| Scope | Server/pillar redeploys only | Any Job type that declares a gating topic |

A gated `server.deploy` Job can pass through both in sequence: first
policy confirmation (proceed despite the failure, or never start), then —
only once the new version is genuinely running — Deployment & Servers'
own deploy confirmation (keep it, or roll back). They don't conflict;
each is a separate question about a separate moment in the same Job.

### Left open

Tracked as `OQ-3` in [`../open_questions.md`](../open_questions.md): which
Job types declare which gating topics isn't enumerated here — that
mapping is each pillar's own implementation work as its Job types get
defined. (The two items this section used to list alongside it — check
execution as an async Job with a 30-minute ceiling, and Gate 4 making a
separate allowlist-declaration step unnecessary — are resolved; see
"Test-script execution sandbox" and "Authoring a policy" above.)

## Data model (this pillar's own database)

Supersedes the placeholder `policy_rules`, `policy_check_results` pair in
`docs/architecture.md`'s Data model section with the actual shape:

- `policies` — id, topic, **target_pillar** (the one pillar this policy's
  subject governs — see "Policy ownership" above), ears_text,
  test_script_ref (1:1), scope_level (`project` | `project_set` |
  `all_projects`), scope_target (a project id, a project-set id, or null
  for all-projects), created_by (`user` | `framework`), status,
  created_at, amended_at.
- `policy_check_results` — policy id, project id, outcome (`pass` | `fail`
  | `not_applicable`), checked_at, job id.

`project_sets` is **not** this pillar's table — it lives in Registry &
Portfolio (`docs/architecture.md`'s Data model), reached through that
pillar's Pillar Interface whenever a project-set-scoped policy's targets
need resolving.

`docs/architecture.md`'s Data model section should be read with this
document as the authoritative detail for this pillar specifically, per
this ecosystem's own documentation policy ("don't duplicate content across
files; link to the authoritative one instead").
