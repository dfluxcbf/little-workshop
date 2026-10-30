"""The per-project `asset-review` MCP server: `lws asset-mcp serve --project-id <id>`.

Declared in a project's `.mcp.json` by Registry & Portfolio's `project.configure_assets`; it serves
that one project only, and only inside the project's visible asset folder. Same hand-rolled
JSON-RPC 2.0 stdio shape as `mcp_server.server`. Tools:

- `asset_list`: the Blender files of the asset folder and whether each has a current bundle;
- `asset_export`: build a Blender file's bundle (3D model for the Dashboard, ten preview renders,
  UV layout, textures) by running Blender headless — see `common.assets` for the layout;
- `asset_status`: what one file's bundle holds.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from common import assets as conv
from common.paths import Paths
from common.registry_client import ProjectResolutionError, resolve_project
from asset_mcp import runner

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "asset-review"

_PATH = {"type": "string", "description": "Path of the .blend file relative to the asset folder, with forward slashes (see asset_list)."}
TOOLS = [
    {"name": "asset_list", "description": "List the Blender files in the project's visible asset folder, with the state of each one's "
     "exported bundle: none (never exported), stale (the .blend changed since) or ready.",
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "asset_export", "description": "Generate a Blender file's 3D model (glTF binary the Dashboard loads), its ten preview images "
     "(front, right, back, left, top, bottom, two orthographic corner views from above and two from below, textures shown), its UV layout "
     "and its texture images, into the <name>.view folder beside it. Runs Blender headless: takes seconds to a minute.",
     "inputSchema": {"type": "object", "properties": {
         "path": _PATH,
         "collection": {"type": "string", "description": "Export only this collection. Default: every visible mesh outside collections named Reference or Scratch."},
         "resolution": {"type": "integer", "description": "Preview image size in pixels (square). Default 768."},
     }, "required": ["path"], "additionalProperties": False}},
    {"name": "asset_status", "description": "What a Blender file's bundle holds: state, files and any errors of the last export.",
     "inputSchema": {"type": "object", "properties": {"path": _PATH}, "required": ["path"], "additionalProperties": False}},
]


class AssetServer:
    def __init__(self, paths: Paths, project_id: str) -> None:
        self.paths = paths
        self.project_id = project_id

    def root(self) -> Path:
        try:
            row = resolve_project(self.paths, self.project_id)
        except ProjectResolutionError as exc:
            raise conv.AssetError(str(exc), 503) from exc
        return conv.asset_root(Path(row["path"]), row.get("assets_dir"))

    def blend(self, root: Path, rel: str) -> Path:
        target = conv.resolve_inside(root, rel)
        if target.suffix.lower() != conv.BLEND_SUFFIX or not target.is_file():
            raise conv.AssetError(f"not a .blend file in the asset folder: {rel!r}", 404)
        return target

    def asset_list(self, _args: dict) -> Any:
        root = self.root()
        files = sorted(p for p in root.rglob("*" + conv.BLEND_SUFFIX)
                       if p.is_file() and not any(part.startswith(".") or part.endswith(conv.BUNDLE_SUFFIX) for part in p.relative_to(root).parts))
        return {"assets_dir": root.name, "blend_files": [{"path": conv.rel_path(root, p), "bundle": conv.bundle_state(p)} for p in files]}

    def asset_status(self, args: dict) -> Any:
        root = self.root()
        blend = self.blend(root, args["path"])
        folder = conv.bundle_dir(blend)
        manifest = conv.read_manifest(blend) or {}
        return {"path": args["path"], "bundle": conv.bundle_state(blend), "folder": conv.rel_path(root, folder),
                "files": sorted(conv.rel_path(folder, p) for p in folder.rglob("*") if p.is_file()) if folder.is_dir() else [],
                "generated_at": manifest.get("generated_at"), "errors": manifest.get("errors", [])}

    def asset_export(self, args: dict) -> Any:
        root = self.root()
        blend = self.blend(root, args["path"])
        result = runner.export_bundle(blend, collection=args.get("collection"), resolution=int(args.get("resolution") or 768))
        return {"path": args["path"], "ok": result["ok"], "errors": result["errors"], "objects": result["objects"],
                "bundle": conv.rel_path(root, conv.bundle_dir(blend)), "state": conv.bundle_state(blend)}

    def handle(self, request: dict) -> dict | None:
        method, request_id = request.get("method"), request.get("id")
        if "id" not in request:
            return None
        try:
            if method == "initialize":
                result: dict = {"protocolVersion": PROTOCOL_VERSION, "capabilities": {"tools": {}},
                                "serverInfo": {"name": SERVER_NAME, "version": "1"}}
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": TOOLS}
            elif method == "tools/call":
                result = self.call(request.get("params") or {})
            else:
                return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": f"Method not found: {method!r}"}}
        except Exception as exc:  # pylint: disable=broad-exception-caught
            return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32603, "message": str(exc)}}
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    def call(self, params: dict) -> dict:
        name, args = params.get("name"), params.get("arguments") or {}
        spec = next((t for t in TOOLS if t["name"] == name), None)
        if spec is None:
            raise ValueError(f"Unknown tool: {name!r}")
        schema = spec["inputSchema"]
        missing = [k for k in schema.get("required", []) if k not in args]
        unknown = [k for k in args if k not in schema["properties"]]
        if missing or unknown:
            raise ValueError(f"{name}: missing {missing or 'nothing'}, unknown {unknown or 'nothing'}")
        try:
            payload, ok = getattr(self, name)(args), True
        except (conv.AssetError, runner.BlenderError) as exc:
            payload, ok = {"success": False, "message": str(exc)}, False
        if ok and isinstance(payload, dict) and payload.get("ok") is False:
            ok = False
        return {"content": [{"type": "text", "text": json.dumps(payload, default=str)}], "isError": not ok}


def serve(paths: Paths, project_id: str) -> int:
    server = AssetServer(paths, project_id)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except ValueError:
            continue
        response = server.handle(request)
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()
    return 0
