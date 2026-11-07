"""Dashboard routes: the Dashboard page and the project controls."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from common.workspace import WORKSPACE_ID, workspace_dir
from dashboard import client, prefs
from dashboard.app import PROJECT_NAME_RE, _back, _fire_and_forget, _run_status_bucket, _try, starting_up


def build_router(ctx) -> APIRouter:
    router = APIRouter()
    paths, starting, _render = ctx.paths, ctx.starting, ctx.render

    def _project_names() -> list[str]:
        return [p["name"] for p in client.list_projects(paths) if p.get("status", "active") == "active"]

    ctx.project_names = _project_names

    # ---- Dashboard: manage projects at a glance -------------------------

    def _arrange_page(request: Request, names: list[str]):
        return _render(request, "arrange.html", names=names, page="dashboard", back="/", nav_active="dashboard")

    @router.post("/order/{page}/{name}/{direction}")
    def reorder(page: str, name: str, direction: str):
        try:
            prefs.move(paths, page, _project_names(), name, direction)
        except client.PillarUnavailable as exc:
            return _back("/", f"Could not reorder: {exc}")
        except ValueError as exc:
            return _back("/", f"Could not reorder: {exc}")
        return RedirectResponse("/?arrange=1", status_code=303)

    @router.get("/", response_class=HTMLResponse)
    def index(request: Request, arrange: str | None = None, expand: str | None = None):
        error = None
        from dashboard.client import session_name_for

        try:
            names = prefs.ordered(paths, "dashboard", _project_names())
        except client.PillarUnavailable as exc:
            names, error = [], str(exc)
        if arrange:
            return _arrange_page(request, names)
        pillars = _try(lambda: client.core_status(paths).get("pillars", {}), default={})
        unhealthy = [n for n, e in pillars.items() if e["status"] not in ("healthy", "active", "disabled")]

        under_way = starting_up(paths, starting)

        def _project_row(name: str) -> dict:
            # None means "no manifest" (a 404); an unreachable Deployment is a different answer.
            server_unreachable = False
            try:
                server = client.deployment_status_optional(paths, name)
            except client.PillarUnavailable:
                server, server_unreachable = None, True
            session = _try(client.agent_session_status, paths, name)
            runs = _try(client.agent_runs, paths, name, default=[])
            report = _try(client.requirements_report, paths, name)
            awaiting, running = _run_status_bucket(runs)
            reqs = None
            if report is not None:
                items = report.get("requirements", [])
                published = [r for r in items if r.get("status") == "published"]
                reqs = {
                    "total": len(items), "done": sum(1 for r in items if r.get("done")),
                    # a draft is awaiting review, not failing; only a published requirement that is not
                    # passing is a problem worth colouring
                    "drafts": len(items) - len(published),
                    "failing": sum(1 for r in published if not r.get("done")),
                }
            return {
                "name": name, "server": server, "server_unreachable": server_unreachable, "session": session, "awaiting": awaiting, "running": running,
                "reqs": reqs, "ci": _try(client.agent_latest_ci_check, paths, name),
                "branches": _try(client.deployment_branches, paths, name, default=[]),
                "session_name": session_name_for(name), "runs": runs,
                "launching": "launch" in under_way.get(name, ()), "session_starting": "session" in under_way.get(name, ()),
            }

        # Each project's own row is 6 independent, read-only pillar calls —
        # none depend on another project's result, so fetching them
        # concurrently rather than one project fully at a time is a pure
        # latency win with no staleness risk (still the same live data,
        # same request). `ThreadPoolExecutor.map` keeps `rows` in the same
        # order as `names` (2026-09-23 finding: this loop, run serially,
        # was most of what remained of the home page's load time after
        # REQ-000000246 fixed its own dominant N+1-query cost).
        if names:
            with ThreadPoolExecutor(max_workers=len(names)) as pool:
                rows = list(pool.map(_project_row, names))
        else:
            rows = []
        stats = {
            "projects": len(rows),
            "servers_total": sum(1 for r in rows if r["server"] is not None),
            "servers_up": sum(1 for r in rows if r["server"] and r["server"].get("listening")),
            "sessions_up": sum(1 for r in rows if r["session"] and r["session"].get("running")),
            "tasks_active": sum(r["running"] + r["awaiting"] for r in rows),
            "pillars_total": sum(1 for e in pillars.values() if e["status"] != "disabled"),
            "pillars_ok": sum(1 for e in pillars.values() if e["status"] in ("healthy", "active")),
        }
        workspace = {
            "id": WORKSPACE_ID, "dir": str(workspace_dir()), "session": _try(client.agent_session_status, paths, WORKSPACE_ID),
            "session_name": session_name_for(WORKSPACE_ID), "session_starting": "session" in under_way.get(WORKSPACE_ID, ()),
        }
        return _render(
            request, "index.html", rows=rows, stats=stats, unhealthy=unhealthy, workspace=workspace,
            expand=expand, error=error, nav_active="dashboard", live_topics="dashboard",
        )

    # ---- Project controls: server, session, work (on the Dashboard) -------

    @router.post("/projects/{name}/deploy")
    def deploy(name: str, branch: str = Form("dev")):
        if "launch" in starting_up(paths, starting).get(name, ()) or not starting.claim("launch", name):
            return _back(f"/?expand={name}", f"{name} is already launching — progress is on the Jobs page.")
        _fire_and_forget(
            paths, on_done=lambda: starting.release("launch", name), type="deployment.deploy", target_pillar="deployment", project_id=name, params={"branch": branch},
            queue_key=name, depends_on=[], created_by="dashboard",
        )
        return _back(f"/?expand={name}", f"Deploy of {name} ({branch}) started — progress is on the Jobs page.")

    @router.post("/projects/{name}/stop")
    def stop(name: str):
        result = _try(lambda: client.create_job(
            paths, type="deployment.stop", target_pillar="deployment", project_id=name, params={},
            queue_key=name, depends_on=[], created_by="dashboard",
        ))
        ok = result is not None and result.get("status") == "succeeded"
        return _back(f"/?expand={name}", f"{name} stopped." if ok else f"Could not stop {name}: {(result or {}).get('error', 'orchestration unreachable')}")

    @router.post("/projects/{name}/session/start")
    def session_start(name: str):
        if "session" in starting_up(paths, starting).get(name, ()) or not starting.claim("session", name):
            return _back(f"/?expand={name}", f"A session for {name} is already starting.")
        try:
            result = _try(lambda: client.create_job(
                paths, type="agent.session_start", target_pillar="agent", project_id=name, params={},
                queue_key=None, depends_on=[], created_by="dashboard",
            ))
        finally:
            starting.release("session", name)
        if result is not None and result.get("status") == "succeeded":
            return _back(f"/?expand={name}", f"Session started — open the Claude app and attach to {result['result']['session_name']}.")
        return _back(f"/?expand={name}", f"Could not start a session: {(result or {}).get('error', 'orchestration unreachable')}")

    @router.post("/projects/{name}/session/stop")
    def session_stop(name: str):
        _try(lambda: client.create_job(
            paths, type="agent.session_stop", target_pillar="agent", project_id=name, params={},
            queue_key=None, depends_on=[], created_by="dashboard",
        ))
        return _back(f"/?expand={name}", "Session stopped.")

    @router.post("/projects/{name}/archive")
    def project_archive(name: str):
        """Drops `name` out of the active project list into Settings' Archived tab, keeping its
        registry rows, files directory and git checkout untouched — for a project whose code already
        lives on a remote, so nothing is lost by taking it out of the active list."""
        result = _try(lambda: client.create_job(
            paths, type="project.archive", target_pillar="registry", project_id=name, params={"name": name},
            queue_key=name, depends_on=[], created_by="dashboard",
        ))
        ok = result is not None and result.get("status") == "succeeded"
        return _back("/settings", f"{name} archived." if ok else f"Could not archive {name}: {(result or {}).get('error', 'registry unreachable')}")

    @router.post("/projects/{name}/governance")
    def project_governance(name: str, governance: str = Form(...)):
        """Light or full governance (REQ-000000495): which policies apply and whether requirement work is asked."""
        result = _try(lambda: client.create_job(
            paths, type="project.set_governance", target_pillar="registry", project_id=name, params={"governance": governance},
            queue_key=name, depends_on=[], created_by="dashboard",
        ))
        ok = result is not None and result.get("status") == "succeeded"
        return _back("/settings", f"{name} now has {governance} governance." if ok else
                     f"Could not change {name}'s governance: {(result or {}).get('error', 'registry unreachable')}")

    @router.post("/projects/{name}/restore")
    def project_restore(name: str):
        result = _try(lambda: client.create_job(
            paths, type="project.restore", target_pillar="registry", project_id=name, params={"name": name},
            queue_key=name, depends_on=[], created_by="dashboard",
        ))
        ok = result is not None and result.get("status") == "succeeded"
        return _back("/settings", f"{name} restored." if ok else f"Could not restore {name}: {(result or {}).get('error', 'registry unreachable')}")

    @router.post("/projects/{name}/delete")
    def project_delete(name: str):
        """Hard delete (`project.remove`): purges `name`'s registry rows and files directory, no undo.
        The git checkout itself is left alone. Settings only offers this where there is no remote to
        fall back on — a remote-backed project is archived instead."""
        result = _try(lambda: client.create_job(
            paths, type="project.remove", target_pillar="registry", project_id=name, params={"name": name},
            queue_key=name, depends_on=[], created_by="dashboard",
        ))
        ok = result is not None and result.get("status") == "succeeded"
        return _back("/settings", f"{name} deleted." if ok else f"Could not delete {name}: {(result or {}).get('error', 'registry unreachable')}")

    @router.get("/projects/new", response_class=HTMLResponse)
    def project_new(request: Request):
        return _render(request, "project_new.html", dest=str(workspace_dir()), nav_active="new-project")

    @router.post("/projects/new")
    def project_create(
        name: str = Form(...), description: str = Form(""), create_github_repo: str | None = Form(None),
        public: str | None = Form(None), interview: str | None = Form(None),
    ):
        name = name.strip()
        if name == WORKSPACE_ID:
            return _back("/projects/new", f"{WORKSPACE_ID!r} is reserved for the workspace session; choose another name.")
        if not PROJECT_NAME_RE.match(name):
            return _back("/projects/new", "The name must be lowercase letters, digits and hyphens, starting with a letter or digit.")
        params = {"name": name, "dest_dir": str(workspace_dir()), "description": description.strip(),
                  "create_github_repo": bool(create_github_repo), "public": bool(public), "interview": bool(interview)}
        try:
            result = client.create_job(
                paths, timeout=300.0, type="project.bootstrap", target_pillar="registry", project_id=name,
                params=params, queue_key=None, depends_on=[], created_by="dashboard",
            )
        except client.PillarUnavailable as exc:
            return _back("/projects/new", f"Could not create {name}: {exc}")
        if result.get("status") != "succeeded":
            return _back("/projects/new", f"Could not create {name}: {result.get('error') or result.get('status')}")
        created = result.get("result") or {}
        message = f"Project {name} created at {created.get('path')} and registered."
        if created.get("interview_job_id"):
            message += " The interview has started: answer its questions in the Claude app."
        elif created.get("interview_error"):
            message += f" The interview did not start: {created['interview_error']}"
        return _back("/", message)

    @router.get("/projects")
    def projects_redirect():
        return RedirectResponse("/", status_code=307)

    return router
