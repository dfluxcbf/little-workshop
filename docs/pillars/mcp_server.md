# MCP Server

## Description

MCP Server is one of little-workshop's three thin, stateless proxies
(alongside `lws` and, once built, Dashboard) — it holds no domain data of
its own, translating a caller's MCP `tools/call` request into a Pillar
Interface call against whichever pillar actually owns the answer. It's a
hand-rolled, dependency-free JSON-RPC 2.0/MCP stdio server
(`initialize`, `tools/list`, `tools/call`) — the same proven shape
`little_versions.mcp` already uses in this ecosystem, chosen over the
`mcp` PyPI SDK so this pillar carries no new runtime dependency and no
risk of colliding with that package's own top-level `mcp` import name
(the source package is named `mcp_server`; Core's pillar registry still
calls the pillar itself `"mcp"`).

## Purpose & goal

"An agent (Claude, or another tool) can see and act on everything above,
without a terminal" (`docs/architecture.md`). Placed deliberately early in
the milestone sequence (M7, right after Agentic Development) so that
further implementation work on little-workshop itself could start using
its own tools reflexively instead of raw CLI/API calls — the same
bootstrapping value this ecosystem already gets from `little-requirements`'
and `little-versions`' own MCP surfaces.

## Responsibilities

- **The stdio JSON-RPC loop** (`mcp_server/server.py`) — one line of
  JSON-RPC 2.0 in on stdin, one line out on stdout per response;
  `initialize` reports protocol version and server info, `tools/list`
  returns the declared catalog, `tools/call` validates arguments
  (required-present, no-unknown-fields) before dispatch.
- **The tool catalog** (`mcp_server/tools.py`) — every mutating Job type
  and read endpoint built so far for Registry & Portfolio, Version
  Control & Release, Deployment & Servers, and Agentic Development,
  namespaced `workshop_<pillar>_<operation>`. Each `ToolSpec` is a
  declarative schema plus a small handler translating MCP arguments into
  exactly one Pillar Interface call — no logic of its own beyond that
  translation. Tool coverage grows automatically as later milestones
  ship; this pillar's own code doesn't need revisiting for that.
- **Registration at user scope** — `bazel run //:install` runs
  `claude mcp remove little-workshop` (no forced scope, so a stale entry
  can't shadow the new one) then `claude mcp add little-workshop -s user
  -- lws mcp serve`, per the root MCP policy: a project's install script
  is the only place that should call `claude mcp add`/`remove`, and user
  scope (never local/project) is required since this server proxies the
  same daemon regardless of which project's session attaches it.

## Job checklists (2026-10-03)

`workshop_job_status` returns the Job's `steps`; `workshop_job_answer` relays the user's choice for
a step in state `attention` (always a prompt, since it stands in for a human decision). A version
job that can ask is waited for up to 120 seconds, until it ends or a step asks, and comes back with
its checklist so the question can be relayed.


## Interfacing

- **Calls**: Registry & Portfolio, Version Control & Release, Deployment
  & Servers, and Agentic Development — a mutation becomes an
  Orchestration `POST /v1/jobs` (the identical `create_job` convention
  `lws` itself uses), a read becomes a direct `GET` on the owning
  pillar's socket. Every socket — including Orchestration's own, for
  dispatching a Job — is resolved through Core's registry
  (`common.pillar_socket.resolve_pillar_socket`), never hardcoded.
  `mcp_server.client` is a deliberate duplicate of `cli.client`'s
  HTTP-over-Unix-socket glue, not a shared import, per the project-wide
  rule that no two pillars' `src/` trees share a Python import path —
  extended here to little-workshop's two thin proxies as well.
- **Called by**: whatever MCP client attaches it — Claude Code (human-
  interactive or one of its own agent sessions), or any other MCP-capable
  tool. Never Core/systemd — unlike every pillar, it's spawned on demand
  over stdio (`lws mcp serve`), not supervised as a long-running unit;
  Core's manifest has always defaulted `"mcp": enabled=False` with no
  launch `command`, exactly the "not Core-supervised" state this pillar
  needs, since M1.
- **Discovered through**: Core, for every pillar it proxies into — but
  it is never itself discovered through Core by another pillar, since
  nothing calls *into* MCP Server except its own MCP client.
