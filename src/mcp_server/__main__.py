"""MCP Server's real entrypoint: `python -m mcp_server`, or the `lws mcp
serve` console command `claude mcp add` registers per
docs/policies/mcp_policy.md. Unlike every other `python -m <pillar>`
entrypoint, this one is never launched by Core/systemd — it's a thin,
stateless proxy spawned on demand by whatever MCP client attaches it
(docs/architecture.md's Components table).

`python -m mcp_server gateway` is the other face: the HTTP gateway
(`mcp_server/gateway.py`), which Core does supervise when the manifest
enables the `mcp` pillar with that command."""

from __future__ import annotations

import sys

from mcp_server.server import main

if __name__ == "__main__":
    if sys.argv[1:] == ["gateway"]:
        from mcp_server.gateway import main as gateway_main

        gateway_main()
        sys.exit(0)
    sys.exit(main())
