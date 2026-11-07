"""Dashboard routes: Incidents."""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from dashboard import client
from dashboard.app import _back, _try, only_registered, registered_names


def build_router(ctx) -> APIRouter:
    router = APIRouter()
    paths, _render = ctx.paths, ctx.render

    # -- Incidents: an unhandled exception, awaiting the user's confirmation -----

    @router.get("/incidents", response_class=HTMLResponse)
    def incidents(request: Request, status: str = "open"):
        rows = only_registered(_try(lambda: client.list_incidents(paths, status=status or None), default=[]), registered_names(paths))
        return _render(request, "incidents.html", incidents=rows, status=status, nav_active="incidents", live_topics="incidents")

    @router.get("/incidents/{incident_id}", response_class=HTMLResponse)
    def incident_detail(request: Request, incident_id: str):
        incident = _try(lambda: client.get_incident(paths, incident_id), default=None)
        return _render(request, "incident_detail.html", incident=incident, nav_active="incidents", live_topics="incidents")

    @router.post("/incidents/{incident_id}/confirm")
    def incident_confirm(incident_id: str, review_mode: str = Form("auto_approve")):
        result = _try(lambda: client.create_job(
            paths, type="agent.confirm_incident", target_pillar="agent", project_id=None,
            params={"incident_id": incident_id, "review_mode": review_mode},
            queue_key=None, depends_on=[], created_by="dashboard",
        ))
        if result is None or result.get("status") not in ("running", "succeeded"):
            return _back(f"/incidents/{incident_id}", f"Could not confirm: {(result or {}).get('error', 'agent pillar unreachable')}")
        return _back("/tasks", "Confirmed — the fix is under way.")

    @router.post("/incidents/{incident_id}/dismiss")
    def incident_dismiss(incident_id: str):
        result = _try(lambda: client.create_job(
            paths, type="agent.dismiss_incident", target_pillar="agent", project_id=None,
            params={"incident_id": incident_id}, queue_key=None, depends_on=[], created_by="dashboard",
        ))
        if result is None or result.get("status") != "succeeded":
            return _back(f"/incidents/{incident_id}", f"Could not dismiss: {(result or {}).get('error', 'agent pillar unreachable')}")
        return _back("/incidents", "Dismissed.")

    return router
