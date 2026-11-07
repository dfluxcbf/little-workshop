# Pillar specifications

One document per pillar: description, purpose and goal, responsibilities, and interfacing (which other pillars
it calls, which call it, and why). `docs/architecture.md` has the Components table they are grounded in.

Core and `lws` are not here: Core is the bootstrap and supervision layer every pillar runs under, and `lws` is
one of three thin, stateless proxies (with the Dashboard and the MCP Server).

- [Orchestration](orchestration.md)
- [Policy & Compliance](policy_compliance.md) and its [catalog](policy_catalog.md)
- [Registry & Portfolio](registry_portfolio.md)
- [Requirements & Quality](requirements_quality.md)
- [Version Control & Release](version_control_release.md)
- [Build & Packages](build_packages.md)
- [Deployment & Servers](deployment_servers.md)
- [Agentic Development](agentic_development.md)
- [Dashboard](dashboard.md)
- [MCP Server](mcp_server.md)
- [Usage & Efficiency](usage.md)
