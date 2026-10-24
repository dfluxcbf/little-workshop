"""Asset review configuration of a registered project: which folder of the
checkout is visible to the Dashboard's Assets pages, and the project-scope MCP
server (`asset-review`, `lws asset-mcp serve`) that generates the model and
previews of its Blender files. The server is declared in the checkout's
`.mcp.json`; the project's own settings decide whether sessions enable it.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from common.assets import AssetError, asset_root
from registry import db

MCP_NAME = "asset-review"


class AssetsConfigError(Exception):
    pass


def mcp_entry(project: str) -> dict:
    return {"command": "lws", "args": ["asset-mcp", "serve", "--project-id", project]}


def _write_mcp(checkout: Path, project: str, enabled: bool) -> bool:
    path = checkout / ".mcp.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, ValueError) as exc:
        raise AssetsConfigError(f"cannot read {path}: {exc}") from exc
    servers = data.setdefault("mcpServers", {})
    if enabled and servers.get(MCP_NAME) != mcp_entry(project):
        servers[MCP_NAME] = mcp_entry(project)
    elif not enabled and MCP_NAME in servers:
        del servers[MCP_NAME]
    else:
        return False
    if not servers and not enabled:
        data.pop("mcpServers")
    if data:
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    elif path.is_file():
        path.unlink()
    return True


def configure_assets(conn: sqlite3.Connection, name: str, assets_dir: str | None, *, mcp: bool = True) -> dict:
    """Make `assets_dir` (relative to the checkout; None or "" turns the feature off) the project's visible folder."""
    project = db.get_project(conn, name)
    if project is None:
        raise AssetsConfigError(f"project {name!r} not found")
    checkout = Path(project.path)
    assets_dir = (assets_dir or "").strip().strip("/") or None
    if assets_dir is not None:
        try:
            asset_root(checkout, assets_dir)
        except AssetError as exc:
            raise AssetsConfigError(str(exc)) from exc
    db.update_project_assets_dir(conn, name, assets_dir)
    mcp_written = False
    if assets_dir and mcp:
        mcp_written = _write_mcp(checkout, name, enabled=True)
    elif not assets_dir:
        mcp_written = _write_mcp(checkout, name, enabled=False)
    return {"name": name, "assets_dir": assets_dir, "mcp_server": MCP_NAME if assets_dir and mcp else None, "mcp_json_changed": mcp_written}
