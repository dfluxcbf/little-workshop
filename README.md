# little-workshop

The unified, self-hosted system governing the full software lifecycle —
requirements through deployment and monitoring — for every project in this
ecosystem, sized for one PC and up to two developers. Absorbs
`little-requirements`, `little-versions`, `little-runner`, and
`bootstrap-little-project` as internal modules of one daemon, rather than
replacing them with separate third-party peers.

Design-only right now: see `docs/design.md` for the purpose, goals, and core
concepts, and `docs/architecture.md` for the pillar breakdown, data model,
and feature mapping from the tools it absorbs. No implementation exists yet
— see `docs/milestones.md`.

For the narrative version of the same decisions — cross-linked
presentations — start at `presentations/README.md`.

**Independent by design.** Unlike every other submodule in this ecosystem,
little-workshop is exempt from the super-repo's root policies — it is not
wired into `little-requirements`/`lreq` or `little-versions`/`lvx`. See
`docs/governance_policy.md` for why and `docs/README.md`'s "Exemption:
little-workshop" for where the super-repo itself sanctions it.

**What's still unresolved**: `docs/open_questions.md` is the single
tracker for every open design question and gap — check there before
assuming something is decided.
