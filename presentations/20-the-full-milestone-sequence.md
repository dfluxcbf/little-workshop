← [Importing What Was Already Proven](19-importing-what-was-already-proven.md) · deck 20 of 21 · next: [What the First Pass Missed →](21-what-the-first-pass-missed.md)

# The Full Milestone Sequence

M1 (Core + Orchestration) was defined alone, for one prompt, to get
implementation started. This lays out the other thirteen — the full path
from M1 to everything `docs/design.md`/`architecture.md`/`pillars/*.md`
already define being real. Resolved 2026-09-15. Full detail:
[`../docs/milestones.md`](../docs/milestones.md).

## The sequence, and why this order

```
M1  Core + Orchestration           (the substrate)
M2  Registry & Portfolio — bootstrap    ─┐
M3  Version Control & Release           │  the first real pillars —
M4  Deployment & Servers                │  minimal cross-dependencies
M5  Self-redeploy (reflexive)          ─┘  M5 right after M4, not at the
                                            end: every pillar from here on
                                            benefits from it existing
M6  Agentic Development             ─┐
M7  MCP Server — minimal            │  M7 deliberately early: once it
                                     │  exists, further implementation
                                     ─┘  can use little-workshop's own
                                         tools instead of raw calls
M8  Registry & Portfolio — import   (needs M6)
M9  Requirements & Quality          (needs M4)
M10 Build & Packages                (needs M4)
M11 Policy & Compliance             (needs M6, M4, M9 — the most
                                      dependency-heavy pillar, which is
                                      why it's this late, not an
                                      oversight)
M12 Populate the policy catalog     (real authoring work, not a bulk
                                      import, one entry at a time)
M13 Dashboard                       (value scales with what exists to
                                      show; nothing above needs it)
M14 Self-hosting / dogfooding       (the self-application principle
                                      becomes literally true)
```

## Two splits worth calling out

**Registry & Portfolio splits across M2 and M8.** Bootstrap (a new
project, deterministic, no agent needed) ships in M2, early. Import (an
existing project, needs judgment, needs Agentic Development) waits for
M8 — blocking M2 on a pillar three slots later would have been worse than
splitting the one pillar's two capabilities across the timeline they
actually depend on.

**Self-redeploy gets its own milestone, not a line item in Deployment &
Servers.** `little-runner`'s own history — four or five distinct
milestones just for this one capability (the restart mechanism, worktree
deploys, build-before-stop, confirm-gating, cgroup-aware dispatch,
session independence) — is direct evidence this is harder than a normal
deploy, not a reason to guess it'll fit inside M4's scope.

## What's cross-cutting, not its own milestone

Several resolved open questions don't get a milestone because they're
not a *pillar* — they're implemented once, inside whichever milestone
first needs them, and reused:

- **Log retention** (`OQ-4`) — Core writes the first logs (M1), so the
  size-based rolling-trim convention is built there, once.
- **Cascading project removal** (`OQ-7`) — each pillar implements its
  own "purge this project" endpoint as part of *its own* milestone, not
  a separate cleanup pass at the end.
- **The Job-gating checklist** (`OQ-3`) — applied by whoever defines a
  new Job type, in whichever milestone that Job type is born in.

## One thing this planning pass didn't resolve, on purpose

`OQ-12` (does Agentic Development's review mode compose with
action-gating on the same Job) stays open, flagged now as something M11
specifically needs answered rather than left implicit until an
implementer hits it mid-build.

---

**This deck's own sequence turned out to be incomplete** — see
[21 — What the First Pass Missed](21-what-the-first-pass-missed.md) for
what it left out and why.

---

Next: [21 — What the First Pass Missed →](21-what-the-first-pass-missed.md) · or back to [index](README.md)
