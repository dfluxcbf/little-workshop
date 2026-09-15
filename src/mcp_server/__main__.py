"""MCP Server's real entrypoint: `python -m mcp_server`, or the `lws mcp
serve` console command `claude mcp add` registers per
docs/policies/mcp_policy.md. Unlike every other `python -m <pillar>`
entrypoint, this one is never launched by Core/systemd — it's a thin,
stateless proxy spawned on demand by whatever MCP client attaches it
(docs/architecture.md's Components table)."""

from __future__ import annotations

import sys

from mcp_server.server import main

if __name__ == "__main__":
    sys.exit(main())
