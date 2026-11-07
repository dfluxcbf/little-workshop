"""Dashboard routes: the asset folder and the review comments."""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from dashboard import client, job_catalog, markdown
from dashboard.app import ACTIVE_JOB_STATUSES, ASSET_EXPORT_JOB, _back, _try


def build_router(ctx) -> APIRouter:
    router = APIRouter()
    paths, _render = ctx.paths, ctx.render

    # ---- Assets: the project's visible asset folder (3D models, previews, textures) ----

    def _exporting_paths(project: str) -> list[str]:
        """The Blender files this project's queued or running export Jobs are about (read only for the file on show)."""
        jobs = _try(lambda: client.list_jobs(paths, types=[ASSET_EXPORT_JOB], project_id=project, status=ACTIVE_JOB_STATUSES), default=[])
        return [p for job in jobs if job.get("type") == ASSET_EXPORT_JOB and job.get("status") in ACTIVE_JOB_STATUSES
                for p in (job.get("params") or {}).get("paths", [])]

    @router.get("/assets/{project}", response_class=HTMLResponse)
    def assets_project(request: Request, project: str, path: str = ""):
        error, tree, details, doc_html = None, None, None, None
        try:
            tree = client.asset_tree(paths, project)
            if path:
                details = client.asset_details(paths, project, path)
                if details["kind"] == "doc":
                    doc_html = markdown.render_document(details["doc"] or "", project=project, path=path)
        except client.Refused as exc:
            error = exc.detail
        except client.PillarUnavailable as exc:
            error = str(exc)
        exporting = _exporting_paths(project) if details and details["kind"] == "blend" else []
        return _render(
            request, "assets.html", project=project, exporting=details is not None and details["path"] in exporting, tree=(tree or {}).get("tree", []), assets_dir=(tree or {}).get("assets_dir"),
            path=path, details=details, doc_html=doc_html, error=error, nav_active="docs", saved=request.query_params.get("saved"),
        )

    @router.get("/assets/{project}/file")
    def assets_file(project: str, path: str):
        try:
            body, media_type = client.asset_file(paths, project, path)
        except client.Refused as exc:
            return Response(exc.detail, status_code=exc.status)
        except client.PillarUnavailable:
            return Response("agent pillar unreachable", status_code=503)
        # Served from the dashboard's own origin: scripts in an SVG or Markdown asset must never run.
        return Response(body, media_type=media_type, headers={"Content-Security-Policy": "script-src 'none'", "Cache-Control": "no-cache"})

    @router.post("/assets/{project}/export")
    def assets_export(project: str, path: str = Form(...)):
        back = f"/assets/{project}?path={quote(path)}"
        if path in _exporting_paths(project):
            return _back(back, "That file is already being exported.")
        template, error = job_catalog.build_job(project, "export_asset", {"arg_blend": [path]})
        if template is None:
            return _back(back, error or "Could not start the export.")
        result = _try(lambda: client.create_job(paths, **template, depends_on=[], created_by="dashboard"))
        if result is None or result.get("status") not in ("running", "succeeded", "queued"):
            return _back(back, f"Could not start the export: {(result or {}).get('error') or 'orchestration unreachable'}")
        return _back(back, "Export started. This page updates when Blender is done.")

    @router.post("/assets/{project}/comment")
    def assets_comment(project: str, path: str = Form(...), body: str = Form("")):
        back = f"/assets/{project}?path={quote(path)}"
        try:
            client.put_asset_comment(paths, project, path, body.replace("\r\n", "\n"))
        except client.Refused as exc:
            return _back(back, f"Could not save the comment: {exc.detail}")
        except client.PillarUnavailable as exc:
            return _back(back, f"Could not save the comment: {exc}")
        return RedirectResponse(back + "&saved=1", status_code=303)

    @router.put("/docs/{project}/comments/{comment_id}")
    async def docs_comment_put(request: Request, project: str, comment_id: str):
        try:
            fields = await request.json()
        except ValueError:
            return JSONResponse({"detail": "expected JSON"}, status_code=400)
        try:
            comment = client.put_review_comment(paths, project, comment_id, fields if isinstance(fields, dict) else {})
        except client.Refused as exc:
            return JSONResponse({"detail": exc.detail}, status_code=exc.status)
        except client.PillarUnavailable as exc:
            return JSONResponse({"detail": str(exc)}, status_code=503)
        comment["html"] = markdown.render_comment(comment.get("body") or "", project=project, path=comment.get("path") or "")
        return comment

    @router.delete("/docs/{project}/comments/{comment_id}")
    def docs_comment_delete(project: str, comment_id: str):
        try:
            return client.delete_review_comment(paths, project, comment_id)
        except client.Refused as exc:
            return JSONResponse({"detail": exc.detail}, status_code=exc.status)
        except client.PillarUnavailable as exc:
            return JSONResponse({"detail": str(exc)}, status_code=503)

    return router
