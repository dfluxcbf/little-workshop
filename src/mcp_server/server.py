"""MCP (Model Context Protocol) server exposing little-workshop's pillars.

Implements the minimal JSON-RPC 2.0 surface MCP clients rely on --
``initialize``, ``tools/list``, ``tools/call`` -- on top of the Pillar
Interface calls in `mcp_server.tools`/`mcp_server.client`. Same hand-rolled,
dependency-free shape as `little_versions.mcp` (no `mcp` SDK dependency,
no risk of colliding with that package's own top-level `mcp` import name --
the reason this package is named `mcp_server`, not `mcp`, even though
Core's pillar registry still calls the pillar itself "mcp").

Run as a standalone stdio server with:

    python3 -m mcp_server.server

or, once little-workshop is installed, as the console entry point the
root MCP policy (docs/policies/mcp_policy.md) requires be registered at
user scope:

    lws mcp serve

Each line on stdin is one JSON-RPC 2.0 request; each response is written
as one JSON line on stdout.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from common.paths import Paths
from mcp_server.client import PillarUnavailable
from mcp_server.tools import TOOLS, TOOLS_BY_NAME

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "little-workshop"
_FALLBACK_SERVER_VERSION = "0.0.0"

JSONDict = dict[str, Any]

_NOTIFICATION_METHODS = frozenset(
    {"notifications/initialized", "notifications/cancelled", "notifications/progress"}
)


def _method_not_found(method: str) -> JSONDict:
    return {"code": -32601, "message": f"Method not found: {method!r}"}


def _invalid_params(message: str) -> JSONDict:
    return {"code": -32602, "message": message}


class _McpError(Exception):
    def __init__(self, error: JSONDict) -> None:
        super().__init__(error["message"])
        self.error = error


def _validate_arguments(spec, arguments: JSONDict) -> str | None:
    properties = spec.input_schema.get("properties", {})
    required = spec.input_schema.get("required", [])
    missing = [key for key in required if key not in arguments]
    if missing:
        return f"Missing required argument(s) for {spec.name!r}: {', '.join(missing)}"
    unknown = [key for key in arguments if key not in properties]
    if unknown:
        return f"Unknown argument(s) for {spec.name!r}: {', '.join(unknown)}"
    return None


def _tool_result(payload: Any, success: bool) -> JSONDict:
    return {
        "content": [{"type": "text", "text": json.dumps(payload, default=str)}],
        "isError": not success,
    }


def _tool_error(message: str) -> JSONDict:
    return _tool_result({"success": False, "message": message}, success=False)


class MCPServer:
    """JSON-RPC 2.0 / MCP server exposing little-workshop's pillars as tools."""

    def __init__(self, paths: Paths | None = None, *, version: str | None = None) -> None:
        self.paths = paths or Paths.from_env()
        self._version = version

    @property
    def server_version(self) -> str:
        if self._version:
            return self._version
        try:
            from importlib.metadata import PackageNotFoundError, version as pkg_version

            return pkg_version("little-workshop")
        except Exception:  # pylint: disable=broad-exception-caught
            return _FALLBACK_SERVER_VERSION

    def handle_request(self, request: JSONDict) -> JSONDict | None:
        is_notification = "id" not in request
        request_id = request.get("id")
        method = request.get("method")

        try:
            result = self._dispatch(method, request.get("params") or {})
        except _McpError as exc:
            return None if is_notification else _error_response(request_id, exc.error)

        return None if is_notification else _ok_response(request_id, result)

    def _dispatch(self, method: str | None, params: JSONDict) -> JSONDict:
        if method == "initialize":
            return self._handle_initialize()
        if method == "ping" or method in _NOTIFICATION_METHODS:
            return {}
        if method == "tools/list":
            return self._handle_tools_list()
        if method == "tools/call":
            return self._handle_tools_call(params)
        raise _McpError(_method_not_found(method or ""))

    def _handle_initialize(self) -> JSONDict:
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": self.server_version},
        }

    def _handle_tools_list(self) -> JSONDict:
        return {"tools": [tool.to_mcp() for tool in TOOLS]}

    def _handle_tools_call(self, params: JSONDict) -> JSONDict:
        name = params.get("name")
        arguments = params.get("arguments") or {}
        spec = TOOLS_BY_NAME.get(name)
        if spec is None:
            raise _McpError(_invalid_params(f"Unknown tool: {name!r}"))
        problem = _validate_arguments(spec, arguments)
        if problem is not None:
            raise _McpError(_invalid_params(problem))
        return self._invoke_tool(spec, arguments)

    def _invoke_tool(self, spec, arguments: JSONDict) -> JSONDict:
        try:
            payload = spec.handler(self.paths, arguments)
        except PillarUnavailable as exc:
            return _tool_error(str(exc))
        except (KeyError, ValueError) as exc:
            return _tool_error(f"invalid arguments: {exc}")

        success = payload.get("outcome", "succeeded") != "failed" if isinstance(payload, dict) and "outcome" in payload else True
        return _tool_result(payload, success=bool(success))


def _ok_response(request_id: Any, result: JSONDict) -> JSONDict:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error_response(request_id: Any, error: JSONDict) -> JSONDict:
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def _write(message: JSONDict) -> None:
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def serve(paths: Paths | None = None) -> int:
    """Run the MCP server over stdio: one JSON-RPC 2.0 request/response per line."""
    server = MCPServer(paths=paths)
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            _write(_error_response(None, {"code": -32700, "message": f"Parse error: {exc}"}))
            continue
        response = server.handle_request(request)
        if response is not None:
            _write(response)
    return 0


def main() -> int:
    return serve()


if __name__ == "__main__":
    sys.exit(main())
