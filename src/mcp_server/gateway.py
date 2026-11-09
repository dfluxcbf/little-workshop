"""The MCP pillar's HTTP gateway: the tool catalog over HTTP for a project's own code (docs/pillars/mcp_server.md,
"HTTP gateway"). A Windows-hosted project cannot open the pillars' Unix sockets or speak MCP over stdio, but it
reaches the Dashboard on 127.0.0.1:8790; the Dashboard checks the caller's token (`common/api_tokens.py`) and
forwards `/api/v1/tools/...` here with the project the token belongs to in `X-Little-Workshop-Project`.

The gateway acts as that project, as a Claude session in it would without a prompt: the reads, the routine work
and the policy-gated actions of `common/tool_access.py`, always on its own project. The tools that stand in for a
human decision or administer little-workshop itself are refused, since nobody is there to ask.

`python -m mcp_server gateway` serves it on the pillar's socket, supervised by Core like any pillar.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from common import serving
from common.paths import Paths
from common.tool_access import (
    MCP_ASK, MCP_GATED, MCP_NAMED_PROJECT, MCP_PLATFORM, MCP_READ_ONLY, MCP_ROUTINE, MCP_SESSION,
)
from mcp_server.client import PillarUnavailable
from mcp_server.tools import TOOLS, TOOLS_BY_NAME, ToolSpec

PROJECT_HEADER = "X-Little-Workshop-Project"
ALLOWED = frozenset(MCP_READ_ONLY + MCP_ROUTINE + MCP_GATED)


def _error(status: int, error: str, detail: str, hint: str | None = None) -> JSONResponse:
    return JSONResponse({"error": error, "detail": detail, **({"hint": hint} if hint else {})}, status_code=status)


def refusal(spec: ToolSpec, arguments: dict, project: str) -> str | None:
    """Why `project` may not make this call over the gateway, or None. Mirrors the scope guard's MCP rule."""
    name = spec.name
    if name in MCP_ASK or name in MCP_SESSION:
        return f"{name} stands in for a human decision: the user makes it on the Dashboard or in a Claude session"
    if name in MCP_PLATFORM:
        return f"{name} administers little-workshop itself, which no project's token may do"
    if name not in ALLOWED:
        return f"{name} is not classified for the gateway"
    other = arguments.get("project_id")
    if other is not None and other != project:
        return f"{name} names project {other!r}; this token belongs to {project!r}"
    if name in MCP_NAMED_PROJECT and arguments.get("name") != project:
        return f"{name} names project {arguments.get('name')!r}; this token belongs to {project!r}"
    for entry in arguments.get("projects") or ():
        named = entry.get("project_id") if isinstance(entry, dict) else None
        if named is not None and named != project:
            return f"{name} names project {named!r}; this token belongs to {project!r}"
    return None


def with_project(spec: ToolSpec, arguments: dict, project: str) -> dict:
    """The arguments with the token's project filled in where the tool takes one and the caller left it out (so a
    tool whose `project_id` is optional never acts on every project from here)."""
    properties = spec.input_schema.get("properties", {})
    filled = dict(arguments)
    if "project_id" in properties and "project_id" not in filled:
        filled["project_id"] = project
    if spec.name in MCP_NAMED_PROJECT and "name" not in filled:
        filled["name"] = project
    return filled


def _argument_problem(spec: ToolSpec, arguments: dict) -> str | None:
    properties = spec.input_schema.get("properties", {})
    missing = [key for key in spec.input_schema.get("required", []) if key not in arguments]
    if missing:
        return f"missing argument(s): {', '.join(missing)}"
    unknown = [key for key in arguments if key not in properties]
    if unknown:
        return f"unknown argument(s): {', '.join(unknown)}"
    return None


def build_app(paths: Paths) -> FastAPI:
    app = FastAPI(title="little-workshop MCP gateway", docs_url=None, redoc_url=None)

    @app.get("/v1/health")
    def health() -> dict:
        return {"ok": True, "tools": len(ALLOWED)}

    @app.get("/v1/tools")
    def tools(request: Request) -> Any:
        if not request.headers.get(PROJECT_HEADER):
            return _error(400, "no_project", f"{PROJECT_HEADER} is missing")
        return {"tools": [t.to_mcp() for t in TOOLS if t.name in ALLOWED]}

    @app.post("/v1/tools/{name}")
    async def call(name: str, request: Request) -> Any:
        project = request.headers.get(PROJECT_HEADER)
        if not project:
            return _error(400, "no_project", f"{PROJECT_HEADER} is missing")
        spec = TOOLS_BY_NAME.get(name)
        if spec is None:
            return _error(404, "unknown_tool", f"no tool {name!r}", "GET /api/v1/tools lists the tools")
        try:
            body = await request.json() if await request.body() else {}
        except ValueError:
            return _error(400, "bad_json", "the body is not JSON")
        if not isinstance(body, dict):
            return _error(400, "bad_json", "the body is a JSON object of the tool's arguments")
        arguments = with_project(spec, body, project)
        reason = refusal(spec, arguments, project)
        if reason:
            return _error(403, "refused", reason)
        problem = _argument_problem(spec, arguments)
        if problem:
            return _error(422, "bad_arguments", problem, f"see the input schema of {name} in GET /api/v1/tools")
        try:
            payload = await serving_call(spec, paths, arguments)
        except PillarUnavailable as exc:
            return _error(503, "pillar_unavailable", str(exc))
        except (KeyError, ValueError) as exc:
            return _error(422, "bad_arguments", str(exc))
        failed = isinstance(payload, dict) and payload.get("outcome") == "failed"
        return {"tool": name, "project_id": project, "is_error": failed, "result": payload}

    return app


async def serving_call(spec: ToolSpec, paths: Paths, arguments: dict) -> Any:
    """Run a tool's handler off the event loop: handlers block on pillar sockets, some for minutes (job_wait)."""
    return await run_in_threadpool(spec.handler, paths, arguments)


def main() -> None:
    paths = Paths.from_env()
    paths.ensure_layout()
    serving.serve(build_app(paths), paths.socket_path("mcp"))
