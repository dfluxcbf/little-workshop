"""Dashboard — docs/architecture.md's third thin, stateless proxy: "a
human can see and act on everything above, without a terminal."
Generalizes `little-runner`'s own dashboard with a panel per pillar
(docs/milestones.md M14), consuming the decided visual identity
directly (`presentations/assets/identity-tokens.css`) rather than
re-deriving it.

Holds no domain data of its own — every panel is a live read through
`dashboard.client` against the owning pillar's Pillar Interface, and
every mutation is an ordinary Job submitted to Orchestration, exactly
the same discipline the CLI and MCP Server already follow. Bound to a
Unix domain socket like every other pillar (docs/architecture.md's PI
transport: "never a network port") — reachability for a human's browser
is a deployment-time concern (a reverse proxy in front of the socket,
the same `tailscale serve` model `little-runner` already uses), not
something this app solves itself.

**Gate-point values are duplicated as plain strings here** (never a
Python import of `policy.models.GatePoint`) — the same "no shared
import between two pillars' `src/` trees" rule `orchestration/
policy_gate.py` and `mcp_server/tools.py` already follow for the
identical reason.
"""

from __future__ import annotations

import importlib.resources

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from common.paths import Paths
from dashboard import client

GATE_POINT_VALUES = [
    "build_install", "server_launch", "deploy_redeploy", "self_redeploy", "server_stop",
    "release", "package_publish", "branch_finish", "project_removal", "project_registration",
]


def _templates() -> Jinja2Templates:
    templates_dir = importlib.resources.files("dashboard") / "templates"
    return Jinja2Templates(directory=str(templates_dir))


def build_app(paths: Paths) -> FastAPI:
    app = FastAPI(title="little-workshop Dashboard")
    templates = _templates()
    static_dir = importlib.resources.files("dashboard") / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/v1/health")
    def health() -> dict:
        return {"ok": True}

    def _render(request: Request, name: str, **context) -> HTMLResponse:
        return templates.TemplateResponse(request, name, context)

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        try:
            status = client.core_status(paths)
            pillars = status.get("pillars", {})
            error = None
        except client.PillarUnavailable as exc:
            pillars, error = {}, str(exc)
        return _render(request, "index.html", pillars=pillars, error=error)

    @app.get("/projects", response_class=HTMLResponse)
    def projects(request: Request):
        try:
            rows, error = client.list_projects(paths), None
        except client.PillarUnavailable as exc:
            rows, error = [], str(exc)
        return _render(request, "projects.html", projects=rows, error=error)

    @app.get("/jobs", response_class=HTMLResponse)
    def jobs(request: Request, status: str | None = None):
        try:
            rows, error = client.list_jobs(paths, status=status), None
        except client.PillarUnavailable as exc:
            rows, error = [], str(exc)
        return _render(request, "jobs.html", jobs=rows, status=status, error=error)

    @app.get("/policies", response_class=HTMLResponse)
    def policies(request: Request, message: str | None = None):
        try:
            rows = client.policy_list(paths)
            waivers = client.policy_list_waivers(paths)
            error = None
        except client.PillarUnavailable as exc:
            rows, waivers, error = [], [], str(exc)
        return _render(
            request, "policies.html",
            policies=rows, waivers=waivers, error=error, message=message,
            gate_point_values=GATE_POINT_VALUES,
        )

    @app.get("/policies/{policy_id}", response_class=HTMLResponse)
    def policy_detail(request: Request, policy_id: str):
        try:
            policy = client.policy_get(paths, policy_id)
            waivers = client.policy_list_waivers(paths, policy_id=policy_id)
            error = None
        except client.PillarUnavailable as exc:
            policy, waivers, error = None, [], str(exc)
        return _render(
            request, "policy_detail.html",
            policy=policy, waivers=waivers, error=error, gate_point_values=GATE_POINT_VALUES,
        )

    @app.post("/policies/{policy_id}/configure")
    def policy_configure(policy_id: str, enabled: str | None = Form(None), gate_points: list[str] = Form([])):
        client.create_job(
            paths, type="policy.configure", target_pillar="policy", project_id=None,
            params={"policy_id": policy_id, "enabled": enabled == "true", "gate_points": gate_points},
            queue_key=None, depends_on=[], created_by="dashboard",
        )
        return RedirectResponse(f"/policies/{policy_id}", status_code=303)

    @app.post("/policies/{policy_id}/waivers/grant")
    def waiver_grant(policy_id: str, project_id: str = Form(...), reason: str = Form(...)):
        client.create_job(
            paths, type="policy.waiver_grant", target_pillar="policy", project_id=project_id,
            params={"policy_id": policy_id, "reason": reason},
            queue_key=None, depends_on=[], created_by="dashboard",
        )
        return RedirectResponse(f"/policies/{policy_id}", status_code=303)

    @app.post("/policies/{policy_id}/waivers/{waiver_id}/revoke")
    def waiver_revoke(policy_id: str, waiver_id: str):
        client.create_job(
            paths, type="policy.waiver_revoke", target_pillar="policy", project_id=None,
            params={"waiver_id": waiver_id},
            queue_key=None, depends_on=[], created_by="dashboard",
        )
        return RedirectResponse(f"/policies/{policy_id}", status_code=303)

    return app
