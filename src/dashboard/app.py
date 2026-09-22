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
import json
import os
import re
import threading
from datetime import datetime, timezone

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from common.paths import Paths
from common.workspace import WORKSPACE_ID, workspace_dir
from dashboard import client, prefs

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


def requirement_verdict(row: dict) -> str:
    """One word for a requirement's state: passing (done), failing, no tests, or draft."""
    if row.get("done"):
        return "passing"
    if row.get("status") != "published":
        return "draft"
    return "failing" if row.get("tested") else "no tests"


def build_report(paths: Paths, project: str) -> dict:
    """Every requirement (description, verdict) and every test (name, verdict — the latest run) of one project."""
    rows = {r["requirement_id"]: r for r in client.requirements_report(paths, project).get("requirements", [])}
    requirements = []
    for req in sorted(client.requirements_list(paths, project), key=lambda r: r["key"]):
        row = rows.get(req["id"], {})
        requirements.append({
            "key": req["key"], "title": req.get("title", ""), "description": req.get("description", ""),
            "verdict": requirement_verdict({**row, "status": row.get("status", req.get("status"))}),
        })
    suites = {s["id"]: s["name"] for s in client.requirements_suites(paths, project)}
    latest: dict[tuple[str, str], dict] = {}
    for result in client.requirements_test_results(paths, project):
        key = (result["suite_id"], result["test_name"])
        if key not in latest or result["executed_at"] >= latest[key]["executed_at"]:
            latest[key] = result
    tests = [
        {"suite": suites.get(sid, sid), "name": name, "verdict": r["verdict"], "detail": r.get("detail", "")}
        for (sid, name), r in latest.items()
    ]
    tests.sort(key=lambda t: (t["suite"], t["name"]))
    return {"requirements": requirements, "tests": tests}


WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
PROJECT_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")

PILLAR_NAMES = {
    "orchestration": "Orchestration", "policy": "Policy & Compliance", "registry": "Registry & Portfolio",
    "requirements": "Requirements & Quality", "version": "Version Control & Release", "build": "Build & Packages",
    "deployment": "Deployment & Servers", "agent": "Agentic Development", "dashboard": "Dashboard", "mcp": "MCP Server",
}


def pillar_name(identifier: str) -> str:
    """The pillar's full name, capitalised as in docs/architecture.md; an unknown identifier is shown as given."""
    return PILLAR_NAMES.get(identifier, identifier)


def collect_attention(paths: Paths) -> list[dict]:
    """What needs a human right now: an enabled policy whose latest check on a
    project failed, and every Job waiting for a confirmation. Both are read
    live from their owning pillar; a pillar that is down contributes nothing."""
    items: list[dict] = []
    policies = {p["id"]: p for p in _try(client.policy_list, paths, default=[])}
    latest: dict[tuple[str, str], dict] = {}
    for result in _try(client.policy_check_results, paths, default=[]):  # oldest first
        latest[(result["policy_id"], result["project_id"])] = result
    for (policy_id, project), result in latest.items():
        policy = policies.get(policy_id)
        if result["outcome"] == "fail" and policy and policy.get("enabled", True):
            items.append({
                "kind": "policy", "project": project, "title": f"{policy['topic']} is failing",
                "detail": (result.get("detail") or "").strip()[:240], "href": f"/policies/{policy_id}",
                "since": result.get("checked_at"),
            })
    for job in _try(lambda: client.list_jobs(paths, status="pending_confirmation"), default=[]):
        items.append({
            "kind": "confirmation", "project": job.get("project_id") or "", "title": f"{job['type']} awaits confirmation",
            "detail": "", "href": "/jobs?status=pending_confirmation", "since": job.get("created_at"),
        })
    return items


JOB_STATUS_ORDER = ["running", "pending_confirmation", "queued", "failed", "succeeded", "cancelled"]
JOB_STATUS_LABELS = {
    "running": "Running", "pending_confirmation": "Needs confirmation", "queued": "Queued",
    "failed": "Failed", "succeeded": "Succeeded", "cancelled": "Cancelled",
}
JOB_ACTIVE_STATUSES = {"running", "queued", "pending_confirmation"}
JOBS_PAGE_SIZE = 50
# (hours, label) — how old a finished job must be before "Delete old jobs" may remove it.
JOB_CLEANUP_AGES = [(12, "12 hours"), (48, "2 days"), (168, "1 week"), (672, "4 weeks"), (1344, "8 weeks"), (4032, "24 weeks")]


def _parse_time(value: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(value) if value else None
    except ValueError:
        return None


def relative_time(value: str | None, *, now: datetime | None = None) -> str:
    """'just now', '5m ago', '3h ago', '2d ago' — for a job's timestamp."""
    moment = _parse_time(value)
    if moment is None:
        return ""
    seconds = int(((now or datetime.now(timezone.utc)) - moment).total_seconds())
    if seconds < 45:
        return "just now"
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= size:
            return f"{seconds // size}{unit} ago"
    return "just now"


def job_duration(created: str | None, updated: str | None) -> str:
    """How long a finished job took: '0.4s', '12s', '3m 05s', '1h 02m'."""
    start, end = _parse_time(created), _parse_time(updated)
    if start is None or end is None:
        return ""
    seconds = max((end - start).total_seconds(), 0.0)
    if seconds < 10:
        return f"{seconds:.1f}s"
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds // 60)}m {int(seconds % 60):02d}s"
    return f"{int(seconds // 3600)}h {int(seconds % 3600 // 60):02d}m"


def pretty_json(value) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False, default=str)


def build_app(paths: Paths) -> FastAPI:
    app = FastAPI(title="little-workshop Dashboard")
    templates = _templates()
    static_dir = importlib.resources.files("dashboard") / "static"
    templates.env.filters["reltime"] = relative_time
    templates.env.filters["pretty"] = pretty_json
    templates.env.globals["pillar_name"] = pillar_name
    templates.env.globals["job_duration"] = job_duration
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
        context.setdefault("attention_count", len(collect_attention(paths)))
        return templates.TemplateResponse(request, name, context)

    @app.get("/sw.js")
    def service_worker() -> Response:
        # Served from the root so its scope covers the whole app.
        body = (static_dir / "sw.js").read_text(encoding="utf-8")
        return Response(body, media_type="application/javascript", headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"})

    @app.get("/offline", response_class=HTMLResponse)
    def offline(request: Request):
        return _render(request, "offline.html")

    @app.get("/attention.json")
    def attention_json() -> dict:
        items = collect_attention(paths)
        return {"count": len(items), "items": items}

    @app.get("/attention", response_class=HTMLResponse)
    def attention(request: Request):
        items = collect_attention(paths)
        return _render(request, "attention.html", items=items, attention_count=len(items), nav_active="attention")

    def _project_names() -> list[str]:
        return [p["name"] for p in client.list_projects(paths) if p.get("status", "active") == "active"]

    # ---- Dashboard: manage projects at a glance -------------------------

    def _arrange_page(request: Request, names: list[str]):
        return _render(request, "arrange.html", names=names, page="dashboard", back="/", nav_active="dashboard")

    @app.post("/order/{page}/{name}/{direction}")
    def reorder(page: str, name: str, direction: str):
        try:
            prefs.move(paths, page, _project_names(), name, direction)
        except client.PillarUnavailable as exc:
            return _back("/", f"Could not reorder: {exc}")
        except ValueError as exc:
            return _back("/", f"Could not reorder: {exc}")
        return RedirectResponse("/?arrange=1", status_code=303)

    @app.get("/", response_class=HTMLResponse)
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
                published = [r for r in items if r.get("status") == "published"]
                reqs = {
                    "total": len(items), "done": sum(1 for r in items if r.get("done")),
                    # a draft is awaiting review, not failing; only a published requirement that is not
                    # passing is a problem worth colouring
                    "drafts": len(items) - len(published),
                    "failing": sum(1 for r in published if not r.get("done")),
                }
            rows.append({
                "name": name, "server": server, "session": session, "awaiting": awaiting, "running": running,
                "reqs": reqs, "ci": _try(client.agent_latest_ci_check, paths, name),
                "branches": _try(client.deployment_branches, paths, name, default=[]),
                "session_name": session_name_for(name), "runs": runs,
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
        workspace = {
            "id": WORKSPACE_ID, "dir": str(workspace_dir()), "session": _try(client.agent_session_status, paths, WORKSPACE_ID),
            "session_name": session_name_for(WORKSPACE_ID),
        }
        return _render(
            request, "index.html", rows=rows, stats=stats, unhealthy=unhealthy, workspace=workspace,
            expand=expand, error=error, nav_active="dashboard",
        )

    # ---- Report: requirements and tests with their verdicts ---------------

    @app.get("/report", response_class=HTMLResponse)
    def report(request: Request, project: str | None = None, q: str = "", verdict: str = ""):
        error = None
        try:
            names = prefs.ordered(paths, "dashboard", _project_names())
        except client.PillarUnavailable as exc:
            names, error = [], str(exc)
        chosen = project if project in names else (names[0] if names else None)
        data = {"requirements": [], "tests": []}
        if chosen:
            try:
                data = build_report(paths, chosen)
            except client.PillarUnavailable as exc:
                error = str(exc)
        needle = q.strip().lower()
        reqs = [r for r in data["requirements"] if (not verdict or r["verdict"] == verdict)
                and (not needle or needle in f"{r['key']} {r['title']} {r['description']}".lower())]
        tests = [t for t in data["tests"] if (not verdict or t["verdict"] == verdict)
                 and (not needle or needle in f"{t['suite']} {t['name']}".lower())]
        req_counts: dict[str, int] = {}
        for r in data["requirements"]:
            req_counts[r["verdict"]] = req_counts.get(r["verdict"], 0) + 1
        test_counts: dict[str, int] = {}
        for t in data["tests"]:
            test_counts[t["verdict"]] = test_counts.get(t["verdict"], 0) + 1
        suites: dict[str, list[dict]] = {}
        for t in tests:
            suites.setdefault(t["suite"], []).append(t)
        return _render(
            request, "report.html", projects=names, project=chosen, q=q, verdict=verdict, requirements=reqs,
            suites=suites, req_counts=req_counts, test_counts=test_counts, req_total=len(data["requirements"]),
            test_total=len(data["tests"]), error=error, nav_active="report",
        )

    # ---- Project controls: server, session, work (on the Dashboard) -------

    @app.post("/projects/{name}/deploy")
    def deploy(name: str, branch: str = Form("dev")):
        _fire_and_forget(
            paths, type="deployment.deploy", target_pillar="deployment", project_id=name, params={"branch": branch},
            queue_key=name, depends_on=[], created_by="dashboard",
        )
        return _back(f"/?expand={name}", f"Deploy of {name} ({branch}) started — progress is on the Jobs page.")

    @app.post("/projects/{name}/stop")
    def stop(name: str):
        result = _try(lambda: client.create_job(
            paths, type="deployment.stop", target_pillar="deployment", project_id=name, params={},
            queue_key=name, depends_on=[], created_by="dashboard",
        ))
        ok = result is not None and result.get("status") == "succeeded"
        return _back(f"/?expand={name}", f"{name} stopped." if ok else f"Could not stop {name}: {(result or {}).get('error', 'orchestration unreachable')}")

    @app.post("/projects/{name}/session/start")
    def session_start(name: str):
        result = _try(lambda: client.create_job(
            paths, type="agent.session_start", target_pillar="agent", project_id=name, params={},
            queue_key=None, depends_on=[], created_by="dashboard",
        ))
        if result is not None and result.get("status") == "succeeded":
            return _back(f"/?expand={name}", f"Session started — open the Claude app and attach to {result['result']['session_name']}.")
        return _back(f"/?expand={name}", f"Could not start a session: {(result or {}).get('error', 'orchestration unreachable')}")

    @app.post("/projects/{name}/session/stop")
    def session_stop(name: str):
        _try(lambda: client.create_job(
            paths, type="agent.session_stop", target_pillar="agent", project_id=name, params={},
            queue_key=None, depends_on=[], created_by="dashboard",
        ))
        return _back(f"/?expand={name}", "Session stopped.")

    @app.get("/projects/new", response_class=HTMLResponse)
    def project_new(request: Request):
        return _render(request, "project_new.html", dest=str(workspace_dir()), nav_active="new-project")

    @app.post("/projects/new")
    def project_create(
        name: str = Form(...), description: str = Form(""), create_github_repo: str | None = Form(None),
        public: str | None = Form(None),
    ):
        name = name.strip()
        if name == WORKSPACE_ID:
            return _back("/projects/new", f"{WORKSPACE_ID!r} is reserved for the workspace session; choose another name.")
        if not PROJECT_NAME_RE.match(name):
            return _back("/projects/new", "The name must be lowercase letters, digits and hyphens, starting with a letter or digit.")
        params = {"name": name, "dest_dir": str(workspace_dir()), "description": description.strip(),
                  "create_github_repo": bool(create_github_repo), "public": bool(public)}
        try:
            result = client.create_job(
                paths, timeout=300.0, type="project.bootstrap", target_pillar="registry", project_id=name,
                params=params, queue_key=None, depends_on=[], created_by="dashboard",
            )
        except client.PillarUnavailable as exc:
            return _back("/projects/new", f"Could not create {name}: {exc}")
        if result.get("status") != "succeeded":
            return _back("/projects/new", f"Could not create {name}: {result.get('error') or result.get('status')}")
        return _back("/", f"Project {name} created at {result['result']['path']} and registered.")

    @app.get("/projects")
    def projects_redirect():
        return RedirectResponse("/", status_code=307)

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

    # ---- Queue a task behind another; schedule a task for later ----------------

    def _waiting_on_candidates() -> list[dict]:
        """Agent tasks that have not finished yet — what a queued task can wait behind."""
        jobs = _try(lambda: client.list_jobs(paths), default=[])
        return [
            {"id": j["id"], "label": f"{j.get('project_id') or '?'} — {(j.get('params') or {}).get('title') or j['id'][:8]} ({j['status'].replace('_', ' ')})"}
            for j in sorted(jobs, key=lambda j: j.get("created_at") or "", reverse=True)
            if j.get("type") == "agent.run_task" and j.get("status") in JOB_ACTIVE_STATUSES
        ]

    @app.get("/tasks/queue", response_class=HTMLResponse)
    def task_queue(request: Request, project: str | None = None):
        error = None
        try:
            names = _project_names()
        except client.PillarUnavailable as exc:
            names, error = [], str(exc)
        return _render(request, "task_queue.html", projects=names, selected=project, predecessors=_waiting_on_candidates(), error=error, nav_active="queue-task")

    @app.post("/tasks/queue")
    def task_queue_submit(
        project: str = Form(...), title: str = Form(...), description: str = Form(""), intent: str = Form("task"),
        review_mode: str = Form("manual"), after: str = Form(""),
    ):
        if not after or after not in {c["id"] for c in _waiting_on_candidates()}:
            return _back("/tasks/queue", "Choose the task this one should wait for.")
        result = _try(lambda: client.create_job(
            paths, type="agent.run_task", target_pillar="agent", project_id=project,
            params={"title": title, "description": description, "intent": intent, "review_mode": review_mode},
            queue_key=project, depends_on=[after], created_by="dashboard",
        ))
        if result is None or result.get("status") not in ("queued", "running", "succeeded"):
            return _back("/tasks/queue", f"Could not queue the task: {(result or {}).get('error', 'orchestration unreachable')}")
        return _back("/tasks", f"Task queued on {project}; it starts once the task it waits for has succeeded.")

    @app.get("/tasks/schedule", response_class=HTMLResponse)
    def task_schedule(request: Request, project: str | None = None):
        error = None
        try:
            names = _project_names()
        except client.PillarUnavailable as exc:
            names, error = [], str(exc)
        scheduled = [t for t in _try(lambda: client.scheduled_tasks(paths), default=[]) if (t.get("job_template") or {}).get("type") == "agent.run_task"]
        return _render(
            request, "task_schedule.html", projects=names, selected=project, scheduled=scheduled, weekdays=WEEKDAYS,
            error=error, nav_active="schedule-task",
        )

    @app.post("/tasks/schedule")
    def task_schedule_submit(
        project: str = Form(...), title: str = Form(...), description: str = Form(""), intent: str = Form("task"),
        review_mode: str = Form("manual"), kind: str = Form("once"), at: str = Form(""), time_of_day: str = Form(""),
        day: list[str] = Form(default=[]),
    ):
        if kind == "once":
            try:
                when = datetime.fromisoformat(at)
            except ValueError:
                return _back("/tasks/schedule", "Choose the date and time to start the task.")
            if when <= datetime.now():
                return _back("/tasks/schedule", "That time has already passed — choose a time in the future.")
            schedule = {"kind": "once", "at": when.isoformat()}
        elif kind == "recurring":
            days = sorted({int(d) for d in day if d.isdigit() and 0 <= int(d) <= 6})
            if not days or not re.match(r"^([01]\d|2[0-3]):[0-5]\d$", time_of_day):
                return _back("/tasks/schedule", "Choose at least one weekday and a time of day.")
            schedule = {"kind": "recurring", "days_of_week": days, "time_of_day": time_of_day}
        else:
            return _back("/tasks/schedule", "Choose whether the task runs once or every week.")
        template = {
            "type": "agent.run_task", "target_pillar": "agent", "project_id": project,
            "params": {"title": title, "description": description, "intent": intent, "review_mode": review_mode},
            "queue_key": project, "depends_on": [],
        }
        try:
            client.create_scheduled_task(paths, name=f"{project}: {title}", job_template=template, schedule=schedule)
        except client.PillarUnavailable as exc:
            return _back("/tasks/schedule", f"Could not schedule the task: {exc}")
        return _back("/tasks/schedule", f"Task scheduled on {project}.")

    @app.post("/tasks/schedule/{task_id}/remove")
    def task_schedule_remove(task_id: str):
        try:
            client.remove_scheduled_task(paths, task_id)
        except client.PillarUnavailable as exc:
            return _back("/tasks/schedule", f"Could not remove it: {exc}")
        return _back("/tasks/schedule", "Scheduled task removed.")

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
    def jobs(request: Request, status: str | None = None, q: str | None = None, limit: int = JOBS_PAGE_SIZE):
        try:
            everything, error = client.list_jobs(paths), None
        except client.PillarUnavailable as exc:
            everything, error = [], str(exc)
        counts = {name: 0 for name in JOB_STATUS_ORDER}
        for job in everything:
            counts[job["status"]] = counts.get(job["status"], 0) + 1
        matching = [j for j in everything if not status or j["status"] == status]
        needle = (q or "").strip().lower()
        if needle:
            matching = [
                j for j in matching
                if needle in " ".join(str(j.get(k) or "") for k in ("type", "project_id", "target_pillar", "created_by", "id")).lower()
            ]
        matching.sort(key=lambda j: j.get("created_at") or "", reverse=True)
        limit = max(JOBS_PAGE_SIZE, min(limit, 1000))
        prunable = _try(lambda: client.job_cleanup_preview(paths, [hours for hours, _ in JOB_CLEANUP_AGES]), default={}) if everything else {}
        cleanup_options = [{"hours": hours, "label": label, "count": prunable.get(str(hours), 0)} for hours, label in JOB_CLEANUP_AGES]
        return _render(
            request, "jobs.html", cleanup_options=cleanup_options, jobs=matching[:limit], total=len(matching), limit=limit, next_limit=limit + JOBS_PAGE_SIZE,
            status=status, q=q or "", all_count=len(everything),
            counts=[(name, JOB_STATUS_LABELS.get(name, name), counts[name]) for name in [*JOB_STATUS_ORDER, *[k for k in counts if k not in JOB_STATUS_ORDER]] if counts[name]],
            active=sum(counts.get(name, 0) for name in JOB_ACTIVE_STATUSES), error=error, nav_active="jobs")

    @app.post("/jobs/cleanup")
    def jobs_cleanup(hours: int | None = Form(None)):
        """Delete finished jobs older than one of the offered ages. Anything else is refused."""
        labels = dict(JOB_CLEANUP_AGES)
        if hours not in labels:
            return _back("/jobs", "Choose how old the jobs must be before deleting them.")
        try:
            result = client.job_cleanup(paths, hours)
        except client.PillarUnavailable as exc:
            return _back("/jobs", f"Could not delete jobs: {exc}")
        deleted = result.get("deleted", 0)
        kept = result.get("kept_for_safety", 0)
        note = f" {kept} kept because active work still depends on them." if kept else ""
        return _back("/jobs", f"Deleted {deleted} job{'' if deleted == 1 else 's'} older than {labels[hours]}.{note}")

    @app.get("/policies", response_class=HTMLResponse)
    def policies(request: Request, message: str | None = None):
        try:
            rows, error = client.policy_list(paths), None
        except client.PillarUnavailable as exc:
            rows, error = [], str(exc)
        pending = _try(lambda: client.policy_drafts(paths, status="pending"), default=[])
        return _render(
            request, "policies.html", policies=rows, error=error, message=message,
            pending_drafts=len(pending), gate_point_values=GATE_POINT_VALUES, nav_active="policies",
        )

    # Drafts awaiting approval. Declared before `/policies/{policy_id}` so "drafts" is never read as an id.

    @app.get("/policies/drafts", response_class=HTMLResponse)
    def policy_drafts(request: Request, message: str | None = None):
        try:
            pending, error = client.policy_drafts(paths, status="pending"), None
        except client.PillarUnavailable as exc:
            pending, error = [], str(exc)
        rejected = _try(lambda: client.policy_drafts(paths, status="rejected"), default=[])
        return _render(
            request, "drafts.html", pending=pending, rejected=rejected, error=error, message=message, nav_active="policies",
        )

    @app.get("/policies/drafts/{draft_id}", response_class=HTMLResponse)
    def policy_draft_detail(request: Request, draft_id: str):
        error = None
        try:
            draft = client.policy_draft(paths, draft_id)
        except client.PillarUnavailable as exc:
            draft, error = None, str(exc)
        registered = _try(lambda: _project_names(), default=[]) if draft else []
        return _render(
            request, "draft_detail.html", draft=draft, projects=sorted(registered), error=error,
            gate_point_values=GATE_POINT_VALUES, nav_active="policies",
        )

    @app.post("/policies/drafts/{draft_id}/approve")
    def policy_draft_approve(
        draft_id: str, reviewed: str | None = Form(None), gate_points: list[str] = Form([]),
        project: list[str] = Form([]), gating: list[str] = Form([]),
    ):
        """Gate 4, in the app: only with the reviewer's explicit confirmation."""
        back = f"/policies/drafts/{draft_id}"
        if reviewed != "on":
            return _back(back, "Tick the review box to approve: the policy text and test script must be reviewed first.")
        chosen = list(dict.fromkeys(project))
        try:
            job = client.create_job(
                paths, type="policy.approve_draft", target_pillar="policy", project_id=None,
                params={
                    "draft_id": draft_id, "reviewed": True, "gate_points": gate_points,
                    "projects": [{"project_id": name, "gating_enabled": name in gating} for name in chosen],
                },
                queue_key=None, depends_on=[], created_by="dashboard",
            )
        except client.PillarUnavailable as exc:
            return _back(back, f"Could not approve: {exc}")
        if job.get("status") != "succeeded":
            return _back(back, f"Not approved: {job.get('error') or 'the policy pillar refused it'}")
        return _back(f"/policies/{job['result']['id']}", "Approved and activated.")

    @app.post("/policies/drafts/{draft_id}/reject")
    def policy_draft_reject(draft_id: str, reason: str = Form("")):
        job = _try(lambda: client.create_job(
            paths, type="policy.reject_draft", target_pillar="policy", project_id=None,
            params={"draft_id": draft_id, "reason": reason}, queue_key=None, depends_on=[], created_by="dashboard",
        ))
        ok = job is not None and job.get("status") == "succeeded"
        return _back("/policies/drafts", "Draft rejected." if ok else f"Could not reject: {(job or {}).get('error', 'policy unreachable')}")

    @app.post("/policies/drafts/{draft_id}/restore")
    def policy_draft_restore(draft_id: str):
        job = _try(lambda: client.create_job(
            paths, type="policy.restore_draft", target_pillar="policy", project_id=None,
            params={"draft_id": draft_id}, queue_key=None, depends_on=[], created_by="dashboard",
        ))
        ok = job is not None and job.get("status") == "succeeded"
        return _back("/policies/drafts", "Draft restored." if ok else f"Could not restore: {(job or {}).get('error', 'policy unreachable')}")

    @app.get("/policies/{policy_id}", response_class=HTMLResponse)
    def policy_detail(request: Request, policy_id: str):
        script_link = None
        registered: list[str] = []
        try:
            policy = client.policy_get(paths, policy_id)
            error = None
        except client.PillarUnavailable as exc:
            policy, error = None, str(exc)
        if policy is not None:
            registered = _try(lambda: _project_names(), default=[])
            script_link = _try(client.policy_script_link, paths, policy.get("test_script", ""), default=None)
            selected = {p["project_id"]: p for p in policy.get("projects", [])}
            names = list(dict.fromkeys([*registered, *selected]))
            project_rows = [
                {"name": n, "selected": n in selected, "gating": selected.get(n, {}).get("gating_enabled", True),
                 "registered": n in registered}
                for n in sorted(names)
            ]
        else:
            project_rows = []
        return _render(
            request, "policy_detail.html", policy=policy, project_rows=project_rows, script_link=script_link,
            error=error, gate_point_values=GATE_POINT_VALUES, nav_active="policies",
        )

    @app.post("/policies/{policy_id}/configure")
    def policy_configure(policy_id: str, enabled: str | None = Form(None), gate_points: list[str] = Form([])):
        client.create_job(
            paths, type="policy.configure", target_pillar="policy", project_id=None,
            params={"policy_id": policy_id, "enabled": enabled == "true", "gate_points": gate_points},
            queue_key=None, depends_on=[], created_by="dashboard",
        )
        return RedirectResponse(f"/policies/{policy_id}", status_code=303)

    @app.post("/policies/{policy_id}/projects")
    def policy_projects(policy_id: str, project: list[str] = Form([]), gating: list[str] = Form([])):
        """The whole selection at once: `project` are the selected projects,
        `gating` the subset whose gating stays on."""
        chosen = list(dict.fromkeys(project))
        client.create_job(
            paths, type="policy.set_projects", target_pillar="policy", project_id=None,
            params={
                "policy_id": policy_id,
                "projects": [{"project_id": name, "gating_enabled": name in gating} for name in chosen],
            },
            queue_key=None, depends_on=[], created_by="dashboard",
        )
        return RedirectResponse(f"/policies/{policy_id}", status_code=303)

    return app
