"""`lws` commands of the MCP servers (the workshop's and the per-project asset-review one)."""

from __future__ import annotations

import argparse

from common.paths import Paths


def cmd_asset_mcp_serve(args: argparse.Namespace) -> int:
    from asset_mcp.server import serve

    return serve(Paths.from_env(), args.project_id)


def cmd_mcp_serve(args: argparse.Namespace) -> int:
    """The command `claude mcp add` registers at user scope (docs/policies/
    mcp_policy.md): a stdio MCP server proxying every pillar's Pillar
    Interface, per docs/milestones.md's M7."""
    from mcp_server.server import serve

    return serve(Paths.from_env())


def register(subparsers) -> None:
    mcp = subparsers.add_parser("mcp", help="MCP Server commands")
    mcp_sub = mcp.add_subparsers(dest="mcp_command", required=True)

    mcp_serve = mcp_sub.add_parser("serve", help="Run the MCP server over stdio (what `claude mcp add` registers)")
    mcp_serve.set_defaults(func=cmd_mcp_serve)

    asset_mcp = subparsers.add_parser("asset-mcp", help="The per-project asset-review MCP server (3D model and previews of Blender files)")
    asset_mcp_sub = asset_mcp.add_subparsers(dest="asset_mcp_command", required=True)
    asset_mcp_serve = asset_mcp_sub.add_parser("serve", help="Run the asset-review MCP server over stdio for one project")
    asset_mcp_serve.add_argument("--project-id", required=True)
    asset_mcp_serve.set_defaults(func=cmd_asset_mcp_serve)
