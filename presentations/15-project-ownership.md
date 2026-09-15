← [Verdicts vs. Faults](14-verdicts-vs-faults.md) · deck 15 of 15 · back to [index](README.md)

# Who Owns a Project?

Answers two open questions at once, because they turned out to be the
same question: who owns project *sets* (deck 9's open item), and which
pillar is the main responsible for a project's resources. Resolved
2026-09-15.

## Registry & Portfolio owns identity, not everything

A project's data was always meant to stay distributed — that's the whole
point of ten pillars instead of one. What was missing was an answer to a
narrower question: which pillar decides a project *exists* at all.

Registry & Portfolio already had the goal for this — "one true answer to
what projects exist" — and already absorbed `bootstrap-little-project`.
That makes it the natural owner of a project's **lifecycle and identity**:
creation, import, removal, the canonical registry row. Not the owner of a
project's requirements, its branches, its deployment state, or anything
else any other pillar tracks about it — only whether the project is
registered at all.

---

## Two paths in, one outcome

| | New project | Existing project |
|---|---|---|
| Mechanism | `project.bootstrap` — deterministic pipeline | A Claude CLI session, via Registry & Portfolio's MCP entry point |
| Why | Scaffolding a blank project needs no judgment | Bringing an arbitrary existing repo into conformance does |
| Result | A `projects` row + a `~/.config/little-workshop/projects/<project>/` directory | Identical |

The row is what "this project exists" means. The directory is a
*consequence* of registration — this also resolves
[8 — Core's Recovery & Configuration](08-core-recovery-and-configuration.md)'s
leftover ambiguity about which of the two was authoritative.

---

## One mechanism, not two

"Claude CLI shall use the MCP entry to orchestrate the import" turned out
to describe something already true of this whole project, not a new
primitive to build: a human's interactive Claude Code session and Agentic
Development's own internally-dispatched Claude CLI session reach
little-workshop's MCP server identically. This session has been living
proof of the shape the whole time — `little-requirements`' and
`little-versions`' MCP tools have been this session's own entry point into
managing little-workshop itself. Import is the same mechanism, pointed at
a pre-existing repository instead of a blank one.

---

## Project sets move to Registry & Portfolio

Deck 9 stored project sets in Policy & Compliance, purely because
targeting policies was their only consumer at the time, and left moving
them "a reconciliation for later." With Registry & Portfolio now
established as the pillar responsible for project identity, later is now:
grouping projects is a registry concern regardless of who consumes the
grouping. Policy & Compliance reads project-set membership through
Registry & Portfolio's Pillar Interface when resolving a project-set-scoped
policy's targets — it no longer stores that membership itself.

---

Back to [index](README.md) · or revisit [2 — The Pillar Architecture](02-pillar-architecture.md)
