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

The UI is `little-runner`'s own "mission control" design (its dark
stylesheet, top bar/bottom tab bar, PWA manifest and icons), reshaped
around little-workshop: the Dashboard and Control pages manage
*projects* (servers, Claude Code sessions, tasks); the system's own
pillars live apart on their own page.

**Gate-point values are duplicated as plain strings here** (never a
Python import of `policy.models.GatePoint`) — the same "no shared
import between two pillars' `src/` trees" rule `orchestration/
policy_gate.py` and `mcp_server/tools.py` already follow for the
identical reason.
"""

from __future__ import annotations

import importlib.resources
import os
import threading

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from common.paths import Paths
from dashboard import client

GATE_POINT_VALUES = [
    "build_install", "server_launch", "deploy_redeploy", "self_redeploy", "server_stop",
    "release", "package_publish", "branch_finish", "project_removal", "project_registration",
]

# Redeploying these restarts a link in the redeploy chain (Orchestration
# dispatches, Deployment executes), so they are submitted after the rest.
SELF_AFFECTING_PILLARS = {"orchestration", "deployment", "core"}


def _templates() -> Jinja2Templates:
    templates_dir = importlib.resources.files("dashboard") / "templates"
    return Jinja2Templates(directory=str(templates_dir))


def _run_status_bucket(runs: list[dict]) -> tuple[int, int]:
    awaiting = sum(1 for r in runs if r["status"] == "awaiting_finish")
    running = sum(1 for r in runs if r["status"] == "running")
    return awaiting, running


def _try(fn, *args, default=None):
    """A panel whose pillar is down renders as "unknown", never a 500."""
    try:
        return fn(*args)
    except client.PillarUnavailable:
        return default


def _fire_and_forget(paths: Paths, **job) -> None:
    """Deploys and CI checks outlast a browser request (a cold `bazel run
    //:install` takes a minute); the job is submitted from a thread and its
    progress read back from the Jobs page, instead of hanging the tap."""
    def submit() -> None:
        try:
            client.create_job(paths, timeout=320.0, **job)
        except client.PillarUnavailable:
            pass

    threading.Thread(target=submit, daemon=True).start()


def _back(to: str, message: str) -> RedirectResponse:
    from urllib.parse import quote

    return RedirectResponse(f"{to}{'&' if '?' in to else '?'}message={quote(message)}", status_code=303)


def build_app(paths: Paths) -> FastAPI:
    app = FastAPI(title="little-workshop Dashboard")
    templates = _templates()
    static_dir = importlib.resources.files("dashboard") / "static"
    templates.env.globals["static_v"] = int(os.path.getmtime(str(static_dir / "app.css")))
    # follow_symlink: Bazel runfiles trees are symlink forests.
    app.mount("/static", StaticFiles(directory=str(static_dir), follow_symlink=True), name="static")

    @app.get("/v1/health")
    def health() -> dict:
        return {"ok": True}

    def _render(request: Request, name: str, **context) -> HTMLResponse:
        context.setdefault("error", None)
        context.setdefault("message", request.query_params.get("message"))
        context.setdefault("nav_active", "")
        return templates.TemplateResponse(request, name, context)

    @app.get("/sw.js")
    def service_worker() -> Response:
        # Served from the root so its scope covers the whole app.
        body = (static_dir / "sw.js").read_text(encoding="utf-8")
        return Response(body, media_type="application/javascript", headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"})

    @app.get("/offline", response_class=HTMLResponse)
    def offline(request: Request):
        return _render(request, "offline.html")

    def _project_names() -> list[str]:
        return [p["name"] for p in client.list_projects(paths) if p.get("status", "active") == "active"]

    # ---- Dashboard: manage projects at a glance -------------------------

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        error = None
        try:
            names = _project_names()
        except client.PillarUnavailable as exc:
            names, error = [], str(exc)
        pillars = _try(lambda: client.core_status(paths).get("pillars", {}), default={})
        unhealthy = [n for n, e in pillars.items() if e["status"] not in ("healthy", "active", "disabled")]

        rows = []
        for name in names:
            server = _try(client.deployment_status_optional, paths, name)
            session = _try(client.agent_session_status, paths, name)
            runs = _try(client.agent_runs, paths, name, default=[])
            report = _try(client.requirements_report, paths, name)
            awaiting, running = _run_status_bucket(runs)
            reqs = None
            if report is not None:
                items = report.get("requirements", [])
                reqs = {"total": len(items), "done": sum(1 for r in items if r.get("done"))}
            rows.append({
                "name": name, "server": server, "session": session, "awaiting": awaiting, "running": running,
                "reqs": reqs, "ci": _try(client.agent_latest_ci_check, paths, name),
            })
        stats = {
            "projects": len(rows),
            "servers_total": sum(1 for r in rows if r["server"] is not None),
            "servers_up": sum(1 for r in rows if r["server"] and r["server"].get("listening")),
            "sessions_up": sum(1 for r in rows if r["session"] and r["session"].get("running")),
            "tasks_active": sum(r["running"] + r["awaiting"] for r in rows),
            "pillars_total": sum(1 for e in pillars.values() if e["status"] != "disabled"),
            "pillars_ok": sum(1 for e in pillars.values() if e["status"] in ("healthy", "active")),
        }
        return _render(request, "index.html", rows=rows, stats=stats, unhealthy=unhealthy, error=error, nav_active="dashboard")

    # ---- Control: per-project servers, sessions, work -------------------

    @app.get("/control", response_class=HTMLResponse)
    def control(request: Request, project: str | None = None):
        error = None
        try:
            names = _project_names()
        except client.PillarUnavailable as exc:
            names, error = [], str(exc)
        from dashboard.client import session_name_for

        panels = []
        for name in names:
            runs = _try(client.agent_runs, paths, name, default=[])
            awaiting, _ = _run_status_bucket(runs)
            panels.append({
                "name": name, "server": _try(client.deployment_status_optional, paths, name),
                "branches": _try(client.deployment_branches, paths, name, default=[]),
                "session": _try(client.agent_session_status, paths, name), "session_name": session_name_for(name),
                "runs": runs, "awaiting": awaiting,
            })
        return _render(request, "control.html", panels=panels, focus=project, error=error, nav_active="control")

    @app.post("/projects/{name}/deploy")
    def deploy(name: str, branch: str = Form("dev")):
        _fire_and_forget(
            paths, type="deployment.deploy", target_pillar="deployment", project_id=name, params={"branch": branch},
            queue_key=name, depends_on=[], created_by="dashboard",
        )
        return _back(f"/control?project={name}", f"Deploy of {name} ({branch}) started — progress is on the Jobs page.")

    @app.post("/projects/{name}/stop")
    def stop(name: str):
        result = _try(lambda: client.create_job(
            paths, type="deployment.stop", target_pillar="deployment", project_id=name, params={},
            queue_key=name, depends_on=[], created_by="dashboard",
        ))
        ok = result is not None and result.get("status") == "succeeded"
        return _back(f"/control?project={name}", f"{name} stopped." if ok else f"Could not stop {name}: {(result or {}).get('error', 'orchestration unreachable')}")

    @app.post("/projects/{name}/session/start")
    def session_start(name: str):
        result = _try(lambda: client.create_job(
            paths, type="agent.session_start", target_pillar="agent", project_id=name, params={},
            queue_key=None, depends_on=[], created_by="dashboard",
        ))
        if result is not None and result.get("status") == "succeeded":
            return _back(f"/control?project={name}", f"Session started — open the Claude app and attach to {result['result']['session_name']}.")
        return _back(f"/control?project={name}", f"Could not start a session: {(result or {}).get('error', 'orchestration unreachable')}")

    @app.post("/projects/{name}/session/stop")
    def session_stop(name: str):
        _try(lambda: client.create_job(
            paths, type="agent.session_stop", target_pillar="agent", project_id=name, params={},
            queue_key=None, depends_on=[], created_by="dashboard",
        ))
        return _back(f"/control?project={name}", "Session stopped.")

    @app.get("/projects")
    def projects_redirect():
        return RedirectResponse("/control", status_code=307)

    # ---- Tasks: Claude on a real branch, under review -------------------

    @app.get("/tasks", response_class=HTMLResponse)
    def tasks(request: Request):
        error = None
        runs: list[dict] = []
        try:
            for name in _project_names():
                runs += _try(client.agent_runs, paths, name, default=[])
        except client.PillarUnavailable as exc:
            error = str(exc)
        runs.sort(key=lambda r: r["created_at"], reverse=True)
        return _render(request, "tasks.html", runs=runs, error=error, nav_active="tasks")

    @app.get("/tasks/new", response_class=HTMLResponse)
    def task_new(request: Request, project: str | None = None):
        error = None
        try:
            names = _project_names()
        except client.PillarUnavailable as exc:
            names, error = [], str(exc)
        return _render(request, "task_new.html", projects=names, selected=project, error=error, nav_active="new-task")

    @app.post("/tasks/new")
    def task_create(
        project: str = Form(...), title: str = Form(...), description: str = Form(""),
        intent: str = Form("task"), review_mode: str = Form("manual"),
    ):
        result = _try(lambda: client.create_job(
            paths, type="agent.run_task", target_pillar="agent", project_id=project,
            params={"title": title, "description": description, "intent": intent, "review_mode": review_mode},
            queue_key=project, depends_on=[], created_by="dashboard",
        ))
        if result is None or result.get("status") not in ("running", "succeeded"):
            return _back("/tasks/new", f"Could not start the task: {(result or {}).get('error', 'orchestration unreachable')}")
        return _back("/tasks", f"Task started on {project}.")

    @app.get("/tasks/{project}/{run_id}", response_class=HTMLResponse)
    def task_detail(request: Request, project: str, run_id: str):
        run = _try(client.agent_run, paths, project, run_id)
        if run is None:
            return _render(request, "tasks.html", runs=[], error=f"no such task {run_id!r} on {project}", nav_active="tasks")
        return _render(request, "task_detail.html", run=run, nav_active="tasks")

    @app.post("/tasks/{project}/{run_id}/{action}")
    def task_settle(project: str, run_id: str, action: str):
        if action not in ("finish", "skip"):
            return _back("/tasks", "Unknown action.")
        result = _try(lambda: client.create_job(
            paths, type=f"agent.{action}_task", target_pillar="agent", project_id=project, params={"run_id": run_id},
            queue_key=project, depends_on=[], created_by="dashboard",
        ))
        ok = result is not None and result.get("status") == "succeeded"
        return _back(f"/tasks/{project}/{run_id}", f"Task {action}ed." if ok else f"Could not {action}: {(result or {}).get('error', 'orchestration unreachable')}")

    # ---- Pillars: the system's own components ---------------------------

    @app.get("/pillars", response_class=HTMLResponse)
    def pillars(request: Request):
        try:
            entries, error = client.core_status(paths).get("pillars", {}), None
        except client.PillarUnavailable as exc:
            entries, error = {}, str(exc)
        deployed = _try(client.deployment_pillars, paths, default=None)
        commits = (deployed or {}).get("commits", [])
        return _render(
            request, "pillars.html", pillars=entries, error=error, nav_active="pillars",
            deployed=(deployed or {}).get("pillars", {}), repo_head=(deployed or {}).get("repo_head"),
            deployment_known=deployed is not None, commits=commits,
            versions={c["sha"]: c["version"] for c in commits if c.get("version")},
        )

    @app.post("/pillars/redeploy")
    def redeploy_pillars(pillar: list[str] = Form(default=[]), commit: str = Form("HEAD")):
        try:
            known = client.core_status(paths).get("pillars", {})
        except client.PillarUnavailable as exc:
            return _back("/pillars", f"Could not redeploy: {exc}")
        chosen = [p for p in dict.fromkeys(pillar) if p in known and known[p].get("status") != "disabled"]
        if not chosen:
            return _back("/pillars", "Select at least one pillar to redeploy.")
        commit = commit.strip() or "HEAD"
        # Pillars whose restart interrupts the redeploy machinery itself go last.
        chosen.sort(key=lambda p: (p in SELF_AFFECTING_PILLARS, p))

        def submit_all() -> None:
            for name in chosen:
                try:
                    client.create_job(
                        paths, timeout=320.0, type="deployment.redeploy_pillar", target_pillar="deployment",
                        project_id=name, params={"pillar": name, "commit": commit}, queue_key=name,
                        depends_on=[], created_by="dashboard",
                    )
                except client.PillarUnavailable:
                    pass

        threading.Thread(target=submit_all, daemon=True).start()
        return _back("/pillars", f"Redeploy of {', '.join(chosen)} ({commit}) started — progress is on the Jobs page.")

    @app.get("/jobs", response_class=HTMLResponse)
    def jobs(request: Request, status: str | None = None):
        try:
            rows, error = client.list_jobs(paths, status=status), None
        except client.PillarUnavailable as exc:
            rows, error = [], str(exc)
        return _render(request, "jobs.html", jobs=rows, status=status, error=error, nav_active="jobs")

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
            gate_point_values=GATE_POINT_VALUES, nav_active="policies",
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
            policy=policy, waivers=waivers, error=error, gate_point_values=GATE_POINT_VALUES, nav_active="policies",
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
