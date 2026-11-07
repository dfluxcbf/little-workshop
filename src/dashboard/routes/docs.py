"""Dashboard routes: documentation review."""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, Response

from dashboard import client, docs_tree, markdown
from dashboard.app import ACTIVE_JOB_STATUSES, DOC_UPDATE_JOB, _back, _try


def build_router(ctx) -> APIRouter:
    router = APIRouter()
    paths, templates, _render = ctx.paths, ctx.templates, ctx.render

    # ---- Docs: documentation review ------------------------------------

    def _doc_update_entries(project: str) -> list[dict]:
        """This project's update-doc jobs that have not started or are running:
        Orchestration Jobs still queued/running, and scheduled ones."""
        entries = []
        active = _try(lambda: client.list_jobs(paths, types=[DOC_UPDATE_JOB], project_id=project, status=ACTIVE_JOB_STATUSES, summary=True),
                      default=[])
        for job in active:
            if job.get("type") == DOC_UPDATE_JOB and job.get("project_id") == project and job.get("status") in ACTIVE_JOB_STATUSES:
                params = job.get("params") or {}
                entries.append({"kind": "job", "id": job["id"], "branch": params.get("branch"), "status": job["status"],
                                "remote_control": bool(params.get("remote_control")), "created_at": job.get("created_at")})
        for task in _try(lambda: client.scheduled_tasks(paths), default=[]):
            template = task.get("job_template") or {}
            if template.get("type") == DOC_UPDATE_JOB and template.get("project_id") == project:
                params = template.get("params") or {}
                entries.append({"kind": "scheduled", "id": task["id"], "branch": params.get("branch"), "status": "scheduled",
                                "remote_control": bool(params.get("remote_control")), "schedule": task.get("schedule") or {},
                                "at": ((task.get("schedule") or {}).get("at") or "")[:16]})
        return entries

    @router.get("/docs", response_class=HTMLResponse)
    def docs_index(request: Request):
        try:
            names, error = ctx.project_names(), None
        except client.PillarUnavailable as exc:
            names, error = [], str(exc)
        return _render(request, "docs.html", projects=names, error=error, nav_active="docs")

    @router.get("/docs/{project}", response_class=HTMLResponse)
    def docs_project(request: Request, project: str, dir: str = ""):
        error = None
        try:
            overview = client.docs_overview(paths, project)
        except (client.PillarUnavailable, client.Refused) as exc:
            overview, error = {"branch": "", "branches": [], "files": [], "dirty": False}, str(exc)
        entries = _doc_update_entries(project)
        branch = overview["branch"]
        open_comments = sum(f["open"] for f in overview["files"])
        recent = [u for u in _try(client.doc_updates, paths, project, default=[]) if u["status"] not in ("starting", "running")][:5]
        view = docs_tree.folder_view(overview["files"], dir)
        return _render(
            request, "docs_project.html", project=project, overview=overview, entries=entries, view=view, directory=view["directory"],
            blocked=[e for e in entries if e["branch"] == branch], open_comments=open_comments, recent=recent,
            error=error, nav_active="docs",
        )

    @router.post("/docs/{project}/checkout")
    def docs_checkout(project: str, branch: str = Form(...), back: str = Form("")):
        back = back if back.startswith(f"/docs/{project}") else f"/docs/{project}"
        result = _try(lambda: client.create_job(
            paths, type="agent.docs_checkout", target_pillar="agent", project_id=project,
            params={"branch": branch}, queue_key=project, depends_on=[], created_by="dashboard",
        ))
        if result is None or result.get("status") != "succeeded":
            return _back(back, f"Could not check out {branch}: {(result or {}).get('error') or 'orchestration unreachable'}")
        return _back(back, f"Checked out {branch}.")

    @router.post("/docs/{project}/update")
    def docs_update(project: str, remote_control: str | None = Form(None), when: str = Form("immediate"), at: str = Form("")):
        back = f"/docs/{project}"
        try:
            branch = client.docs_overview(paths, project)["branch"]
        except (client.PillarUnavailable, client.Refused) as exc:
            return _back(back, f"Could not read the project: {exc}")
        if not branch:
            return _back(back, "The checkout is not on a branch — check one out first.")
        if any(e["branch"] == branch for e in _doc_update_entries(project)):
            return _back(back, f"An update-doc job for {branch} is already queued or running — cancel or delete it first.")
        params = {"branch": branch, "remote_control": remote_control == "on"}
        template = {"type": DOC_UPDATE_JOB, "target_pillar": "agent", "project_id": project, "params": params,
                    "queue_key": f"{project}:docs:{branch}", "depends_on": []}
        if when == "immediate":
            result = _try(lambda: client.create_job(paths, **template, created_by="dashboard"))
            if result is None or result.get("status") not in ("running", "succeeded", "queued"):
                return _back(back, f"Could not start the update: {(result or {}).get('error') or 'orchestration unreachable'}")
            return _back(back, f"Update-doc job started for {branch}.")
        schedule, refusal = ctx.parse_schedule("once", at, "", [], back)
        if refusal is not None:
            return refusal
        try:
            client.create_scheduled_task(paths, name=f"Update docs: {project} ({branch})", job_template=template, schedule=schedule)
        except client.PillarUnavailable as exc:
            return _back(back, f"Could not schedule the update: {exc}")
        return _back(back, f"Update-doc job for {branch} scheduled.")

    @router.post("/docs/{project}/update/scheduled/{task_id}/edit")
    def docs_update_edit(project: str, task_id: str, remote_control: str | None = Form(None), at: str = Form("")):
        back = f"/docs/{project}"
        task = _try(client.get_scheduled_task, paths, task_id)
        template = (task or {}).get("job_template") or {}
        if template.get("type") != DOC_UPDATE_JOB or template.get("project_id") != project:
            return _back(back, "That scheduled update no longer exists.")
        schedule, refusal = ctx.parse_schedule("once", at, "", [], back)
        if refusal is not None:
            return refusal
        template = {**template, "params": {**(template.get("params") or {}), "remote_control": remote_control == "on"}}
        try:
            client.edit_scheduled_task(paths, task_id, job_template=template, schedule=schedule)
        except client.PillarUnavailable as exc:
            return _back(back, f"Could not change it: {exc}")
        return _back(back, "Scheduled update changed.")

    @router.post("/docs/{project}/update/scheduled/{task_id}/remove")
    def docs_update_remove(project: str, task_id: str):
        try:
            client.remove_scheduled_task(paths, task_id)
        except client.PillarUnavailable as exc:
            return _back(f"/docs/{project}", f"Could not delete it: {exc}")
        return _back(f"/docs/{project}", "Scheduled update deleted.")

    @router.post("/docs/{project}/update/jobs/{job_id}/stop")
    def docs_update_stop(project: str, job_id: str):
        try:
            result = client.stop_job(paths, job_id)
        except client.PillarUnavailable as exc:
            return _back(f"/docs/{project}", f"Could not stop it: {exc}")
        if not result.get("stopped"):
            return _back(f"/docs/{project}", f"Could not stop it: {result.get('reason')}")
        return _back(f"/docs/{project}", "Update-doc job stopped.")

    @router.get("/docs/{project}/view", response_class=HTMLResponse)
    def docs_view(request: Request, project: str, path: str):
        try:
            doc = client.doc_file(paths, project, path)
            overview = client.docs_overview(paths, project)
            comments = client.review_comments(paths, project, branch=doc["branch"], path=path)
        except client.Refused as exc:
            return _render(request, "docs.html", projects=[], error=exc.detail, nav_active="docs")
        except client.PillarUnavailable as exc:
            return _render(request, "docs.html", projects=[], error=str(exc), nav_active="docs")
        for comment in comments:
            comment["html"] = markdown.render_comment(comment.get("body") or "", project=project, path=path)
        files = [f["path"] for f in overview["files"]]
        return templates.TemplateResponse(request, "docs_view.html", {
            "project": project, "path": path, "branch": doc["branch"], "branches": overview["branches"],
            "html": markdown.render_document(doc["source"], project=project, path=path),
            "comments": comments, "files": files,
            "active_job": next((e for e in _doc_update_entries(project) if e["branch"] == doc["branch"] and e["status"] == "running"), None),
        })

    @router.get("/docs/{project}/image")
    def docs_image(project: str, path: str):
        try:
            body, media_type = client.doc_image(paths, project, path)
        except client.Refused as exc:
            return Response(exc.detail, status_code=exc.status)
        except client.PillarUnavailable:
            return Response("agent pillar unreachable", status_code=503)
        return Response(body, media_type=media_type, headers={"Content-Security-Policy": "script-src 'none'"})

    return router
