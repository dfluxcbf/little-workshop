← [The Pillar Architecture](02-pillar-architecture.md) · deck 3 of 5 · next: [What Each Old Tool Becomes →](04-feature-mapping.md)

# Jobs, Pipelines, Policy & Recovery

How work actually moves between pillars once they're separate processes —
and, directly following from that separation, the two distinct ways this
system recovers when something goes wrong.

## The Job: one shape for every kind of work

Every operation any absorbed tool performs today —
`requirement.create`, `version.feature_finish`, `test.run_suite`,
`build.run`, `package.publish`, `server.deploy`, `agent.run_task`,
`policy.check`, `project.bootstrap` — is a **Job**, owned by the
Orchestration pillar, dispatched to whichever pillar's interface actually
performs it.

A long-running Job (an agentic-dev session someone is watching live)
doesn't block Orchestration: the target pillar accepts it and reports
completion back later — the same "queue pumps itself" shape
`little-runner`'s repository-slot logic already uses, now crossing a
process boundary instead of staying in-process.

Full diagram: [`../docs/design.md` → Cross-pillar job dispatch](../docs/design.md#cross-pillar-job-dispatch-the-shape-every-pillars-work-takes).

---

## Pipelines: a DAG, not a hand-run checklist

- A named, versioned DAG of Job templates.
- A node's target pillar *and* `project_id` can differ from other nodes in
  the same pipeline — this is what makes a pipeline span several pillars,
  or several submodules, or both.
- Replaces the hand-run, hand-sequenced "ecosystem milestone" pattern in
  today's `docs/milestones.md`.

---

## Policy & Compliance: rules, checked by asking, not by reading

- Seeded from the 8 root policy documents this ecosystem already lives by.
  The markdown stays as the human-readable *rationale*; the rule becomes
  the actual check.
- Evaluated at defined checkpoints: pre-Job, pre-merge/pre-release (a
  pipeline gate), or on a schedule.
- Critically: Policy & Compliance gathers the facts it needs by calling
  the relevant pillar's own interface — **never** by opening that pillar's
  database directly. Compliance-checking follows the same separation rule
  as everything else.
- A few safety rules stay hard-coded, not policy-editable: automation
  never merges/deletes/finishes on its own initiative; the agent never
  touches branches when a Job specifies them. These are invariants this
  ecosystem's production use already depends on.

---

## Two recovery mechanisms — deliberately not one

Requirement #7 ("relaunch only the crashed part") and #8 ("update without
interrupting others") sound similar but need different answers:

| | Crash recovery | Planned upgrade |
|---|---|---|
| Owner | **Core** | **Deployment & Servers** pillar |
| Speed | Immediate | Build-before-stop, takes as long as the build does |
| Rebuild? | No — relaunch the last known-good build | Yes — build in a fresh worktree first |
| Confirm gate? | No — a crash needs speed, not a vote | Yes, for Core and Deployment & Servers themselves by default |
| Reversible? | N/A | Yes — rollback to the last confirmed commit on timeout |

Conflating these into one mechanism would make crash recovery too slow
(rebuilding every time something crashes) or planned upgrades too risky
(no chance to catch a bad deploy before it settles). Keeping them separate
lets each be exactly as careful as it needs to be — no more.

---

## Crash recovery, concretely

```
systemd detects pillar-X exited  →  Restart=on-failure (free, OS-level)
   →  Core increments a crash counter, confirms health once probed
   →  every OTHER pillar: unaffected
   →  a Job mid-flight to pillar-X retries once it's healthy again
```

No rebuild. No confirm gate. Fast, because a crash is an emergency, not a
decision.

---

## Planned upgrade, concretely

```
git worktree add --detach <new commit>     -- pillar X's OLD process
   install(cwd=worktree)                       keeps serving everyone else
   confirm_timeout_seconds armed (default for Core / Deployment & Servers)
   stop old unit → launch new unit from the worktree
   confirmed in time? → settle.  window elapses? → rollback to last-confirmed
```

Inherited directly from `little-runner`'s already-proven mechanism — not
reinvented. Redeploying Core or Deployment & Servers itself reuses the
same cgroup-aware dispatch trick `little-runner` already solved for its own
self-restart.

Full diagrams: [`../docs/design.md` → Data flow](../docs/design.md#data-flow).

---

Next: [4 — What Each Old Tool Becomes →](04-feature-mapping.md)
