"""Dashboard routes: System > Data management."""

from __future__ import annotations

import json
import threading
from datetime import date, datetime, timezone

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, Response

from dashboard import client, deletions
from dashboard.app import DATA_PURGE_JOBS, ORCHESTRATION_PURGE, _back, _try


def build_router(ctx) -> APIRouter:
    router = APIRouter()
    paths, background, _render = ctx.paths, ctx.background, ctx.render

    # -- System > Data management: delete a project's local data; keep, back up and restore its requirements ----

    @router.get("/data", response_class=HTMLResponse)
    def data_management(request: Request):
        projects = _try(lambda: client.list_projects(paths), default=None)
        stored = _try(lambda: client.requirement_projects(paths), default=None)
        names = {p["name"] for p in projects or []}
        for row in stored or []:
            row["registered"] = row["project_id"] in names
        now = datetime.now(timezone.utc)
        owing = [{**d, "stuck": deletions.stuck(d, now)} for d in deletions.load(paths).values()]
        return _render(request, "data.html", projects=projects, stored=stored, registered=sorted(names), owing=owing,
                       retry_minutes=deletions.RETRY_SECONDS // 60, nav_active="data")

    def _run_job(job_type: str, pillar: str, project_id: str, **params) -> tuple[bool, str, str | None]:
        """(ok, why not, the Job's id): one Job on a pillar, waited for."""
        result = _try(lambda: client.create_job(paths, type=job_type, target_pillar=pillar, project_id=project_id, params=params,
                                                queue_key=project_id, depends_on=[], created_by="dashboard"))
        if result is None:
            return False, f"{pillar} is unreachable", None
        job_id = result.get("id")
        if result.get("status") == "pending_confirmation":
            return False, "held by a policy: confirm it under Jobs, then delete again", job_id
        if result.get("status") != "succeeded":
            return False, result.get("error") or f"the job {result.get('status')}", job_id
        return True, "", job_id

    def _run_purge_step(project: str, step: dict) -> tuple[bool, str, str | None]:
        """One purge step of a deletion: a pillar's `purge_project` Job, or Orchestration's own purge, which first
        stops the project's running Jobs."""
        if step["type"] != ORCHESTRATION_PURGE:
            return _run_job(step["type"], step["pillar"], project)
        try:
            for job_id in client.running_job_ids(paths, project):
                client.stop_job(paths, job_id, force=True)
            client.purge_orchestration(paths, project)
        except client.PillarUnavailable as exc:
            return False, str(exc), None
        return True, "", None

    def _retry_deletions(stop: threading.Event) -> None:
        while not stop.wait(deletions.RETRY_SECONDS):
            try:
                deletions.retry_all(paths, _run_purge_step, datetime.now(timezone.utc))
            except Exception:  # noqa: BLE001 - a failed round must not end the loop; the next one retries
                pass

    background.append(_retry_deletions)

    @router.post("/data/projects/{name}/delete")
    def data_delete_project(name: str, confirm: str = Form("")):
        if confirm.strip() != name:
            return _back("/data", f"Type the project's name ({name}) to delete it.")
        try:
            server = client.deployment_status_optional(paths, name)
        except client.PillarUnavailable:
            return _back("/data", f"{name} was not deleted: Deployment & Servers is unreachable, so it cannot be told whether its server is running.")
        if server and (server.get("listening") or server.get("unit") == "active"):
            ok, why, _ = _run_job("deployment.stop", "deployment", name)
            if not ok:
                return _back("/data", f"{name} was not deleted: its server could not be stopped ({why}).")
        ok, why, _ = _run_job("project.remove", "registry", name, name=name)
        if not ok:
            return _back("/data", f"{name} was not deleted: {why}.")
        now = datetime.now(timezone.utc)
        steps = [{"type": job_type, "pillar": pillar} for job_type, pillar in DATA_PURGE_JOBS] + [{"type": ORCHESTRATION_PURGE, "pillar": "orchestration"}]
        owing, errors = [], []
        for step in steps:
            ok, why, job_id = _run_purge_step(name, step)
            if not ok:
                owing.append(step)
                errors.append({"at": now.isoformat(), "pillar": step["pillar"], "type": step["type"], "error": why, "job_id": job_id})
        deletions.begin(paths, name, owing, errors, now)
        done = f"Deleted {name}: its registration and local data. Its checkout and its requirements were kept."
        if owing:
            done += (f" {', '.join(s['pillar'] for s in owing)} could not be reached: trying again every "
                     f"{deletions.RETRY_SECONDS // 60} minutes; it needs your attention if that has not worked within 2 hours.")
        return _back("/data", done)

    @router.post("/data/deletions/{project}/retry")
    def data_retry_deletion(project: str, mode: str = Form("again")):
        if project not in deletions.load(paths):
            return _back("/data", f"Nothing is owing for {project}.")
        left = deletions.retry(paths, project, _run_purge_step, datetime.now(timezone.utc), restart_clock=mode != "once")
        if left is None:
            return _back("/data", f"The deletion of {project} is finished.")
        return _back("/data", f"The deletion of {project} still owes {', '.join(s['pillar'] for s in left['pending'])}.")

    @router.get("/data/requirements/{project}/export")
    def data_export_requirements(project: str):
        try:
            backup = client.requirements_export(paths, project)
        except client.PillarUnavailable as exc:
            return _back("/data", f"Could not export: {exc}.")
        filename = f"requirements-{project}-{date.today().isoformat()}.json"
        return Response(json.dumps(backup, indent=2), media_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="{filename}"'})

    @router.post("/data/requirements/{project}/delete")
    def data_delete_requirements(project: str, confirm: str = Form("")):
        if confirm.strip() != project:
            return _back("/data", f"Type the project id ({project}) to delete its requirements.")
        ok, why, _ = _run_job("requirements.delete_project_data", "requirements", project)
        return _back("/data", f"Deleted the requirements of {project}." if ok else f"The requirements of {project} were not deleted: {why}.")

    @router.post("/data/requirements/import")
    async def data_import_requirements(request: Request):
        form = await request.form()
        upload = form.get("backup")
        if upload is None or not hasattr(upload, "read"):
            return _back("/data", "Choose a backup file to import.")
        try:
            data = json.loads((await upload.read()).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return _back("/data", "That file is not a requirements backup (not valid JSON).")
        target = str(form.get("target") or "original")
        original = str((data or {}).get("project_id") or "") if isinstance(data, dict) else ""
        project = {"original": original, "custom": str(form.get("custom_id") or "").strip(),
                   "existing": str(form.get("existing") or "").strip()}.get(target, "")
        if not project:
            return _back("/data", "Choose the project id to import under.")
        try:
            result = client.requirements_import(paths, project, data)
        except client.PillarUnavailable as exc:
            return _back("/data", f"Could not import: {exc}.")
        if result.get("outcome") != "succeeded":
            return _back("/data", f"Not imported: {result.get('error')}.")
        done = result["result"]
        renumbered = done["renumbered"]
        return _back("/data", f"Imported {done['imported']['requirements']} requirements into {project}."
                              + (f" {len(renumbered)} keys were renumbered ({', '.join(f'{r['old']} -> {r['new']}' for r in renumbered[:8])}"
                                 f"{', ...' if len(renumbered) > 8 else ''})." if renumbered else ""))

    return router
