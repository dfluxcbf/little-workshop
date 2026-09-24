← [The Full Milestone Sequence](20-the-full-milestone-sequence.md) · deck 21 of 22 · next: [Deterministic Automation →](22-deterministic-automation.md)

# What the First Pass Missed

Deck 20 laid out M2 through M14 and called it "the complete path to
implementing everything currently defined." It wasn't. Told directly that
the milestone list didn't cover the whole project, and it held up under
an actual re-audit against `design.md`/`architecture.md` rather than
against deck 20's own list — the thing worth naming here isn't the gap
itself, it's *why* checking a plan against the source is different from
checking a plan against itself. Resolved 2026-09-15.

## Three things, found by re-reading the source, not the plan

**The `lws` CLI never had a real home.** It was referenced constantly —
"the pillar's PI *is* the one surface every caller (CLI, MCP, other
pillars) goes through" — but no paragraph in `architecture.md` ever
formally specified it the way Core, or any pillar, gets specified. A
thing mentioned in passing thirteen times is still a thing without a
milestone if nobody ever gives it its own entry to check against.
`architecture.md` now has one, next to Core and the pillars table, and
`docs/milestones.md` moved it into M1 specifically — it's the only
interface that can exist before Dashboard or MCP Server do, and it's
what actually exercises Core and Orchestration during their own
construction, not an afterthought bolted on once they're done.

**The Pipeline engine was designed and then silently dropped from
scope.** `docs/design.md`'s own Core Concepts list `Pipeline`/
`PipelineRun`; `architecture.md`'s Data model lists Orchestration's
`pipelines`/`pipeline_runs` tables. M1's first draft covered Jobs in
detail and never mentioned Pipelines at all, despite them being the same
pillar's own table. Now explicit in M1's scope, with a note that it
needs no new execution-layer primitive beyond what dependency-linked
Jobs already provide — the design work was already done; only the
milestone coverage was missing.

**The whole point of the project had no milestone.** `docs/design.md`'s
own Purpose section: little-workshop "governs the full software
lifecycle... for every project in this ecosystem" and "replaces the
current arrangement of separate tools." Every milestone through M14
makes little-workshop internally complete and able to manage *itself* —
none of them touch the fact that `little-meals`, `little-mindcheck`, and
every other real project in this ecosystem still depend on `lreq`/`lvx`/
`lrun`/`little-pypi` directly, with nothing designed to actually move
them off. This is now three milestones: **M15** (compatibility shims —
the old CLIs become thin forwarders to `lws`, so nothing breaks mid-
transition), **M16** (the actual cutover, one project at a time, gated
on self-hosting — M14 — succeeding first), and **M17** (retiring the
five absorbed tools once nothing depends on them, not before).

---

## Why this happened

Deck 20 was produced by extending its *own* prior structure — walking
the ten pillars and asking "does each have a milestone," which correctly
found that all ten did. It never asked the different question: does the
*plan* actually deliver everything the *source documents* describe. A
plan can be internally consistent and still miss something the plan was
never checked against in the first place. The fix wasn't more pillars —
it was re-reading `design.md`'s Purpose section and `architecture.md`'s
own passing references literally, instead of trusting that deck 20's own
categories were exhaustive because they felt complete.

---

Back to [index](README.md) · or revisit [20 — The Full Milestone Sequence](20-the-full-milestone-sequence.md)
