"""Dashboard routes: the HTTP gateway, the status and settings pages and the Pillars page."""

from __future__ import annotations

import asyncio
import threading
from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from common import api_tokens, redeploy_plan
from dashboard import client
from dashboard.app import (
    STATUS_LIVE_TOPICS, _back, _status_topics, _try, collect_attention, collect_status, pillar_name, starting_up,
)


def build_router(ctx) -> APIRouter:
    router = APIRouter()
    paths, static_dir, starting, _render = ctx.paths, ctx.static_dir, ctx.starting, ctx.render

    # -- HTTP gateway: the MCP tools for a project's own code, with its token (docs/pillars/dashboard.md) --------

    def _gateway(request: Request, method: str, path: str, body: dict | None = None) -> JSONResponse:
        authorization = request.headers.get("authorization", "")
        token = authorization[7:].strip() if authorization[:7].lower() == "bearer " else None
        project = api_tokens.project_for(paths, token)
        if project is None:
            return JSONResponse({"error": "unauthorized", "detail": "no valid token",
                                 "hint": "send Authorization: Bearer <token> from `lws api token-create --project-id <project>`"},
                                status_code=401, headers={"WWW-Authenticate": "Bearer"})
        try:
            status, answer = client.gateway_request(paths, method, path, project, json=body)
        except client.PillarUnavailable as exc:
            return JSONResponse({"error": "gateway_unavailable", "detail": str(exc),
                                 "hint": "enable the mcp pillar in the Core manifest (docs/pillars/mcp_server.md)"},
                                status_code=503)
        return JSONResponse(answer, status_code=status)

    @router.get("/api/v1/tools")
    def api_tools(request: Request) -> JSONResponse:
        return _gateway(request, "GET", "/v1/tools")

    @router.post("/api/v1/tools/{name}")
    async def api_tool_call(name: str, request: Request) -> JSONResponse:
        try:
            body = await request.json() if await request.body() else {}
        except ValueError:
            return JSONResponse({"error": "bad_json", "detail": "the body is not JSON"}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"error": "bad_json", "detail": "the body is a JSON object of the tool's arguments"},
                                status_code=400)
        return await asyncio.to_thread(_gateway, request, "POST", f"/v1/tools/{quote(name, safe='')}", body)

    @router.get("/sw.js")
    def service_worker() -> Response:
        # Served from the root so its scope covers the whole app.
        body = (static_dir / "sw.js").read_text(encoding="utf-8")
        return Response(body, media_type="application/javascript", headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"})

    @router.get("/offline", response_class=HTMLResponse)
    def offline(request: Request):
        return _render(request, "offline.html")

    @router.get("/attention.json")
    def attention_json() -> dict:
        items = collect_attention(paths)
        return {"count": len(items), "items": items}

    @router.get("/status.json")
    def status_json(project: str = "") -> dict:
        """What Claude Code shows of a project and of the workshop (REQ-000000411): the little-workshop status
        mod reads it for the phone, the status page for the Windows app."""
        return collect_status(paths, project or None)

    @router.get("/status/{project_id}", response_class=HTMLResponse)
    def status_page(request: Request, project_id: str):
        """The same topics in detail, every count shown, zeros too (REQ-000000412): opened beside the chat in the
        Windows app's Browser pane, and anywhere else a browser reaches the Dashboard."""
        status = collect_status(paths, project_id)
        project_topics, workshop_topics = _status_topics(status, project_id)
        return _render(request, "status.html", status=status, project_id=project_id, project_topics=project_topics,
                       workshop_topics=workshop_topics, live_topics=STATUS_LIVE_TOPICS)

    @router.get("/starting.json")
    def starting_json() -> dict:
        return {project: sorted(kinds) for project, kinds in starting_up(paths, starting).items()}

    @router.get("/settings", response_class=HTMLResponse)
    def settings(request: Request):
        items = collect_attention(paths)
        all_projects = _try(client.list_projects, paths, default=[])
        active_projects = sorted((p for p in all_projects if p.get("status", "active") == "active"), key=lambda p: p["name"])
        archived_projects = sorted((p for p in all_projects if p.get("status") == "removed"), key=lambda p: p["name"])
        return _render(request, "settings.html", items=items, attention_count=len(items), nav_active="settings", live_topics="attention",
                       active_projects=active_projects, archived_projects=archived_projects)

    # ---- Pillars: the system's own components ---------------------------

    @router.get("/pillars", response_class=HTMLResponse)
    def pillars(request: Request):
        try:
            entries, error = client.core_status(paths).get("pillars", {}), None
        except client.PillarUnavailable as exc:
            entries, error = {}, str(exc)
        deployed = _try(client.deployment_pillars, paths, default=None)
        commits = (deployed or {}).get("commits", [])
        secrets = _try(client.list_secrets, paths, default=[])
        return _render(
            request, "pillars.html", pillars=entries, error=error, nav_active="pillars",
            deployed=(deployed or {}).get("pillars", {}), repo_head=(deployed or {}).get("repo_head"),
            deployment_known=deployed is not None, commits=commits, secrets=secrets,
            versions={c["sha"]: c["version"] for c in commits if c.get("version")}, live_topics="pillars",
        )

    @router.post("/pillars/redeploy")
    def redeploy_pillars(pillar: list[str] = Form(default=[]), commit: str = Form("HEAD")):
        try:
            known = client.core_status(paths).get("pillars", {})
        except client.PillarUnavailable as exc:
            return _back("/pillars", f"Could not redeploy: {exc}")
        chosen = [p for p in dict.fromkeys(pillar) if p in known and known[p].get("status") != "disabled"]
        if not chosen:
            return _back("/pillars", "Select at least one pillar to redeploy.")
        commit = commit.strip() or "HEAD"
        batches = redeploy_plan.redeploy_batches(chosen)

        def create(template: dict) -> None:
            try:
                client.create_job(paths, timeout=320.0, **template)
            except client.PillarUnavailable:
                pass

        # The first batch runs inside its creation call, so the plan is submitted from a thread.
        threading.Thread(
            target=redeploy_plan.submit_redeploy_batches, args=(create, chosen, commit), kwargs={"created_by": "dashboard"}, daemon=True,
        ).start()
        # Each pillar by the name the Pillars page shows it under, so none hides behind its tag.
        steps = " → ".join(", ".join(pillar_name(p) for p in batch) for batch in batches)
        return _back("/pillars", f"Redeploy of {steps} ({commit}) started — each batch starts when the one before has succeeded; progress is on the Jobs page.")

    return router
