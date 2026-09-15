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
all projects. Project sets are this pillar's own concept — a named,
user-managed group of projects (create, rename, add/remove member
projects), stored here because targeting policies is the only thing they're
for today. (If Registry & Portfolio ever needs generic project grouping for
an unrelated reason, that's a reconciliation for later — not decided now.)

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

Three gates stand between a natural-language submission and a saved
policy. All three must pass — in order, each blocking the next — before
Claude CLI is ever asked to write the enforcement artifact:

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
GATE 3 -- non-conflicting? Two checks, one mechanical and exact, one
   best-effort:
   │  a) structural: does a policy already exist at this exact scope
   │     level + scope target + topic? Mechanically checked, no
   │     ambiguity possible -- two policies can never govern the same
   │     project on the same topic at the same level.
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
   "write the one test script that enforces this EARS requirement"
   │
   ▼
policy record saved: EARS text + a 1:1 reference to its one test script +
   topic + scope level + scope target (project / project set / all) +
   created_by (user | framework)
```

Two separate Claude CLI calls across this flow, two different scopes of
trust: the EARS-refinement call (Gate 1) is text-in, text-out, reviewed by
a human before anything is built from it; the conflict check (Gate 3b) is
read-only judgment, never a write; the final call produces the actual
enforcement artifact, only after both gates ahead of it have passed.
Whether the generated test script itself should also go through explicit
human review before a policy goes active (mirroring Agentic Development's
normal PR-review flow) isn't settled by anything said so far — still
flagged as open.

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
version doesn't count against itself). Whether little-workshop's own
maintainers can edit or retire a framework policy the same way isn't
decided here — left open, alongside the rest of framework-policy
lifecycle.

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

## Informing the user immediately

A failing check is recorded the moment its Job completes, not batched. Two
channels, reusing patterns already proven in this ecosystem rather than
inventing new ones:

- **The Dashboard**, when running, surfaces it immediately — the same
  "warn" pill treatment already designed for the Home/Portfolio and
  Pillars screens.
- **A per-project GitHub issue**, when that project has the GitHub adapter
  attached — opened or updated exactly the way little-runner's existing
  branch audit already does (one persistent, diffed issue per repo, never
  auto-assigned into the agentic pipeline).

Neither channel is universal on its own (Dashboard can be disabled;
GitHub isn't attached to every project), which is an open gap worth
revisiting once this pillar is actually built, not a claim that "immediate"
is fully solved by the two channels above.

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

```
Orchestration is about to dispatch a Job of a gated type
   │
   ▼
ask Policy & Compliance: does the winning policy for this Job's topic(s),
   on this project, pass? (the identical check mechanism as an audit --
   same 1:1 test script, same Deployment & Servers worktree -- just
   triggered synchronously here instead of on a schedule)
   │  pass or not-applicable → dispatch proceeds, nothing else happens
   ▼  fail
Job → pending_confirmation (a new Job status, alongside
   queued/running/succeeded/failed/cancelled)
   │
   ▼
user is warned immediately -- which policy failed, on which project, and
   that the Job is waiting (Dashboard banner; the same treatment already
   designed for an existing confirm-gated deploy's banner)
   │
   ├─ confirmed within the window (10 minutes -- matching little-runner's
   │  own existing confirm_timeout_seconds default exactly, reused rather
   │  than picked fresh) → Job dispatches, the action happens exactly as
   │                        if the check had passed
   └─ window elapses, no response → Job = failed, reason = "policy check
      unconfirmed" -- the action NEVER happens. A fresh launch: the
      server stays not running. A replace: Deployment & Servers'
      build-before-stop sequence never starts in the first place, so the
      old version was never touched and there is nothing to roll back
      either. The timeout's default is always the option that changes
      nothing.
```

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

- Policy test scripts are expected to run fast enough to check inline,
  synchronously, before a gated action starts — there's no defined
  maximum runtime for the check itself yet, separate from the ten-minute
  human-response window.
- Which Job types declare which gating topics isn't enumerated here —
  that mapping is each pillar's own implementation work as its Job types
  get defined.

## Data model (this pillar's own database)

Supersedes the placeholder `policy_rules`, `policy_check_results` pair in
`docs/architecture.md`'s Data model section with the actual shape:

- `policies` — id, topic, ears_text, test_script_ref (1:1), scope_level
  (`project` | `project_set` | `all_projects`), scope_target (a project id,
  a project-set id, or null for all-projects), created_by (`user` |
  `framework`), status, created_at, amended_at.
- `project_sets` — id, name, member project ids.
- `policy_check_results` — policy id, project id, outcome (`pass` | `fail`
  | `not_applicable`), checked_at, job id.

`docs/architecture.md`'s Data model section should be read with this
document as the authoritative detail for this pillar specifically, per
this ecosystem's own documentation policy ("don't duplicate content across
files; link to the authoritative one instead").
