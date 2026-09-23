# Pillar specifications

One document per pillar — description, purpose & goal, responsibilities,
and interfacing (which other pillars it calls, which call it, and why).
See `docs/architecture.md` for the Components table these are grounded
in, and `docs/milestones.md` for what's actually been built so far.

Core and `lws` are deliberately not here: `docs/architecture.md` is
explicit that Core is not a pillar (the bootstrap/supervision layer every
pillar runs under), and `lws` is one of three thin, stateless proxies
(alongside Dashboard and the MCP Server) rather than a pillar in its own
right.

| Pillar | Status |
|---|---|
| [Orchestration](orchestration.md) | Built (M1, M9) |
| [Policy & Compliance](policy_compliance.md) (+ [catalog](policy_catalog.md)) | Built (M12); catalog population in progress (M13, 1/59 entries) |
| [Registry & Portfolio](registry_portfolio.md) | Built (M2, M8) |
| [Requirements & Quality](requirements_quality.md) | Built (M10) |
| [Version Control & Release](version_control_release.md) | Built (M3) |
| [Build & Packages](build_packages.md) | Built (M11) |
| [Deployment & Servers](deployment_servers.md) | Built (M4, M5) |
| [Agentic Development](agentic_development.md) | Built (M6) — fully specified |
| [Dashboard](dashboard.md) | Not yet built (M14) |
| [MCP Server](mcp_server.md) | Built (M7) |
