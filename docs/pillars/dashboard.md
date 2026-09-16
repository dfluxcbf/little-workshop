# Dashboard

**Not yet built.** Scheduled as `docs/milestones.md`'s M13, deliberately
placed late — its value scales with how much there already is to show,
and none of the pillars above need it to function. This document
describes the design as currently specified — see `docs/architecture.md`
and `docs/milestones.md` for the authoritative source; nothing here has
shipped code behind it yet.

## Description

Dashboard is one of little-workshop's three thin, stateless proxies
(alongside `lws` and the MCP Server) — it holds no domain data of its own,
translating an HTTP request from a human's browser into a Pillar
Interface call against whichever pillar actually owns the answer, the
identical discipline the MCP Server already follows for an agent caller.
Absorbs `little-runner`'s `web/`, generalized with a panel for every
pillar that exists by the time it's built. Visual identity is already
decided and ready to consume directly:
`presentations/assets/identity-tokens.css` (Ledger's layout, Mission
Control's exact color tokens).

## Purpose & goal

"A human can see and act on everything above, without a terminal"
(`docs/architecture.md`) — the human-facing counterpart to what the MCP
Server already gives an agent caller.

## Responsibilities (as designed)

- **One panel per pillar that exists** — since it's built last among the
  required-by-default set, its panel coverage is meant to start complete
  rather than grow incrementally the way the MCP Server's tool catalog
  did.
- **Read views and action triggers** — the same split every Pillar
  Interface already makes between plain `GET`s and `POST /v1/jobs/<type>`
  mutations, surfaced as pages and buttons instead of CLI subcommands or
  MCP tool calls.
- **Explicit review/finish actions** — Agentic Development's own design
  already names "a dashboard button" as one of the equally-valid
  explicit-finish triggers for a manual-review run, alongside an MCP call
  or a comment-equivalent; this is where that button actually lives.

## Interfacing (as designed)

- **Calls**: every pillar's Pillar Interface, the same way the MCP
  Server does — a mutation becomes an Orchestration `POST /v1/jobs`, a
  read becomes a direct `GET` on the owning pillar's socket, every socket
  resolved through Core's registry, never hardcoded.
- **Called by**: a human's browser, directly — no client-side daemon or
  CLI involved, the way both `lws` and an MCP client are still separate
  processes talking to this system.
- **Discovered through**: Core, for every pillar it proxies into. Whether
  Dashboard itself needs to be discoverable *by* another pillar (it's
  optional — "a headless/automation-only setup can disable it") hasn't
  come up as a real requirement, since nothing else in the system has a
  reason to call into it.
