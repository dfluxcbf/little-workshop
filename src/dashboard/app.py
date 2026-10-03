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
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from common.job_steps import format_seconds
from common.paths import Paths
from common.incident import install_incident_reporting
from common.workspace import WORKSPACE_ID, workspace_dir
from dashboard import client, job_catalog, markdown, prefs

GATE_POINT_VALUES = [
    "commit", "build_install", "server_launch", "deploy_redeploy", "self_redeploy", "server_stop",
    "release", "package_publish", "branch_finish", "project_removal", "project_registration",
]

# Documentation review's update job, and the Job statuses that count as "not finished yet".
DOC_UPDATE_JOB = "agent.update_docs"
ACTIVE_JOB_STATUSES = ("queued", "running", "pending_confirmation")

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


# The Task list's four sections, in page order: (key, heading).
TASK_GROUPS = (("queued", "Queued"), ("running", "Running"), ("attention", "Needs attention"), ("done", "Done"))
_TASK_GROUP_OF_STATUS = {
    "waiting": "queued", "running": "running",
    "awaiting_finish": "attention", "failed": "attention", "stopped": "attention",
    "finished": "done", "skipped": "done", "cancelled": "done",
}


# A failed or stopped task asks for attention until the user moves it to the done list, or this long
# after it failed, whichever comes first. It keeps its status, so it can be retried from the done list too.
TASK_ATTENTION_DAYS = 7
_DISMISSABLE_STATUSES = ("failed", "stopped")


def _task_group(row: dict | str, now: datetime | None = None) -> str:
    status = row if isinstance(row, str) else row["status"]
    group = _TASK_GROUP_OF_STATUS.get(status, "attention")
    if group != "attention" or isinstance(row, str) or status not in _DISMISSABLE_STATUSES:
        return group
    if row.get("dismissed_at"):
        return "done"
    since = _parse_time(row.get("updated_at") or row.get("created_at"))
    if since is not None and since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)  # a pillar's own timestamps are UTC
    if since is not None and (now or datetime.now(timezone.utc)) - since >= timedelta(days=TASK_ATTENTION_DAYS):
        return "done"
    return group


def _task_rows_without_a_run(jobs: list[dict], runs: list[dict]) -> list[dict]:
    """`agent.run_task` Jobs that have no Agentic Development run yet: still queued,
    dispatched but waiting for the agent's one-task-at-a-time lock, or failed/stopped
    before the run was ever created. A Job whose run exists is already shown as that run."""
    with_a_run = {r.get("job_id") for r in runs if r.get("job_id")}
    rows = []
    for job in jobs:
        if job.get("type") != "agent.run_task" or job["id"] in with_a_run:
            continue
        status = job.get("status", "queued")
        if status in ("queued", "running", "pending_confirmation"):
            status = "waiting"
        elif status == "succeeded":
            continue  # finished without a run to show — nothing to act on
        rows.append({
            "id": job["id"], "project_id": job.get("project_id"), "status": status, "job_only": True,
            "title": (job.get("params") or {}).get("title") or job["id"][:8], "created_at": job.get("created_at"),
            "updated_at": job.get("updated_at"), "error": job.get("error"),
        })
    return rows


def _try(fn, *args, default=None):
    """A panel whose pillar is down renders as "unknown", never a 500."""
    try:
        return fn(*args)
    except client.PillarUnavailable:
        return default


def _fire_and_forget(paths: Paths, on_done=None, **job) -> None:
    """Deploys and CI checks outlast a browser request (a cold `bazel run
    //:install` takes a minute); the job is submitted from a thread and its
    progress read back from the Jobs page, instead of hanging the tap.
    `on_done` runs once the job has finished (or the submit gave up)."""
    def submit() -> None:
        try:
            client.create_job(paths, timeout=320.0, **job)
        except client.PillarUnavailable:
            pass
        finally:
            if on_done is not None:
                on_done()

    threading.Thread(target=submit, daemon=True).start()


# A project "starting up": the Job types behind the Dashboard's Launch and
# Start-session buttons, and the statuses in which one is still under way.
STARTING_JOB_KINDS = {"deployment.deploy": "launch", "agent.session_start": "session"}
STARTING_JOB_STATUSES = ("queued", "running", "pending_confirmation")


class _Starting:
    """Launches and session starts this dashboard has submitted and not yet
    seen finish. Covers the gap before Orchestration has the Job on record
    (a fire-and-forget deploy is submitted from a thread), so the page a
    tap redirects to already shows the launch as under way."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._keys: set[tuple[str, str]] = set()

    def claim(self, kind: str, project: str) -> bool:
        with self._lock:
            if (kind, project) in self._keys:
                return False
            self._keys.add((kind, project))
            return True

    def release(self, kind: str, project: str) -> None:
        with self._lock:
            self._keys.discard((kind, project))

    def snapshot(self) -> set[tuple[str, str]]:
        with self._lock:
            return set(self._keys)


def starting_up(paths: Paths, starting: _Starting) -> dict[str, set[str]]:
    """project -> the kinds ("launch", "session") currently under way for it,
    whether submitted from here or from anywhere else (CLI, MCP, schedule)."""
    found: dict[str, set[str]] = {}
    for kind, project in starting.snapshot():
        found.setdefault(project, set()).add(kind)
    for status in STARTING_JOB_STATUSES:
        for job in _try(lambda: client.list_jobs(paths, status=status), default=[]):
            kind = STARTING_JOB_KINDS.get(job.get("type"))
            if kind and job.get("project_id"):
                found.setdefault(job["project_id"], set()).add(kind)
    return found


def _local_path(target: str, default: str) -> str:
    return target if target.startswith("/") and not target.startswith("//") else default


def _back(to: str, message: str) -> RedirectResponse:
    from urllib.parse import quote

    return RedirectResponse(f"{to}{'&' if '?' in to else '?'}message={quote(message)}", status_code=303)


# Requirements and tests share one set of verdict words, listed in the order
# the Report shows them: what needs a human first, then what is fine.
VERDICT_ORDER = ["draft", "error", "failed", "skipped", "no tests", "passed"]


def requirement_verdict(row: dict) -> str:
    """One word for a requirement's state: draft (awaiting approval), or how its
    linked tests stand — error, failed, skipped, no tests, passed."""
    if row.get("status") != "published":
        return "draft"
    if row.get("done"):
        return "passed"
    # "verdict" comes from the Requirements pillar's report; an older pillar only gives "tested"
    return row.get("verdict") or ("failed" if row.get("tested") else "no tests")


def _natural_key(text: str) -> tuple:
    """REQ-9 before REQ-10, whatever the zero-padding."""
    return tuple(int(part) if part.isdigit() else part for part in re.split(r"(\d+)", text))


def _verdict_rank(verdict: str) -> int:
    return VERDICT_ORDER.index(verdict) if verdict in VERDICT_ORDER else len(VERDICT_ORDER)


AGGREGATE_SUITE_NAME = "Unit tests"  # tools/import_requirements.py's REGISTER_SUITE, kept in sync by name


def build_report(paths: Paths, project: str) -> dict:
    """Every requirement (description, verdict) and every test (name, verdict — the latest run) of one project."""
    rows = {r["requirement_id"]: r for r in client.requirements_report(paths, project).get("requirements", [])}
    requirements = []
    for req in client.requirements_list(paths, project):
        row = rows.get(req["id"], {})
        requirements.append({
            "id": req["id"], "key": req["key"], "title": req.get("title", ""), "description": req.get("description", ""),
            "verdict": requirement_verdict({**row, "status": row.get("status", req.get("status"))}),
        })
    requirements.sort(key=lambda r: (_verdict_rank(r["verdict"]), _natural_key(r["key"])))
    all_suites = {s["id"]: s["name"] for s in client.requirements_suites(paths, project)}
    # AGGREGATE_SUITE_NAME ("Unit tests", tools/import_requirements.py's REGISTER_SUITE): a
    # self-application project's own whole-test-run bucket, used only for a requirement's
    # done/passing state — every real test is also recorded under its own per-module suite
    # (tools/import_requirements.py's import_module_suites), which is what a human wants to
    # browse here; the aggregate would otherwise duplicate every test under one giant heading.
    suites = {sid: name for sid, name in all_suites.items() if name != AGGREGATE_SUITE_NAME}
    all_results = client.requirements_test_results(paths, project)
    # The aggregate suite's latest run is what requirements are judged by, so a test it ran
    # more recently than its module suite did takes that newer verdict here as well.
    aggregate_ids = {sid for sid, name in all_suites.items() if name == AGGREGATE_SUITE_NAME}
    aggregate_runs = [r for r in all_results if r["suite_id"] in aggregate_ids]
    newest_run = max((r["executed_at"] for r in aggregate_runs), default=None)
    newest_by_test = {r["test_name"]: r for r in aggregate_runs if r["executed_at"] == newest_run}
    latest: dict[tuple[str, str], dict] = {}
    for result in all_results:
        if result["suite_id"] not in suites:
            continue
        key = (result["suite_id"], result["test_name"])
        if key not in latest or result["executed_at"] >= latest[key]["executed_at"]:
            latest[key] = result
    for key, result in latest.items():
        newer = newest_by_test.get(key[1])
        if newer is not None and newer["executed_at"] > result["executed_at"]:
            latest[key] = newer
    tests = [
        {"suite": suites[sid], "name": name, "verdict": r["verdict"], "detail": r.get("detail", "")}
        for (sid, name), r in latest.items()
    ]
    tests.sort(key=lambda t: (t["suite"], _verdict_rank(t["verdict"]), t["name"]))
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


def _latest_policy_results(paths: Paths) -> dict[tuple[str, str], dict]:
    """{(policy_id, project_id): latest recorded check result}, across every
    policy and project that has ever been checked. Shared by collect_attention
    and both policies pages so "what's currently failing" is computed the
    same way everywhere."""
    latest: dict[tuple[str, str], dict] = {}
    for result in _try(client.policy_check_results, paths, default=[]):  # oldest first
        latest[(result["policy_id"], result["project_id"])] = result
    return latest


def _policy_associated_projects(policy: dict) -> set[str]:
    return {p["project_id"] for p in policy.get("projects", [])}


def collect_attention(paths: Paths) -> list[dict]:
    """What needs a human right now: an enabled policy whose latest check on a
    project still currently associated with it failed, and every Job waiting
    for a confirmation. Both are read live from their owning pillar; a pillar
    that is down contributes nothing."""
    items: list[dict] = []
    policies = {p["id"]: p for p in _try(client.policy_list, paths, default=[])}
    latest = _latest_policy_results(paths)
    for (policy_id, project), result in latest.items():
        policy = policies.get(policy_id)
        if not (result["outcome"] == "fail" and policy and policy.get("enabled", True)):
            continue
        # A stale result from before the project was removed from this
        # policy's own `projects` list (policy.set_projects) must not keep
        # showing up forever -- 2026-09-22's finding: disassociating
        # back-merge-no-ff from little-home/little-meals/little-mindcheck
        # (a category error for apps that don't implement gitflow themselves)
        # left their old fail results still in the history table, and this
        # function never checked current association at all.
        associated = _policy_associated_projects(policy)
        if associated and project not in associated:
            continue
        items.append({
            "kind": "policy", "tag": "Policies", "project": project, "title": f"{policy['topic']} is failing",
            "detail": (result.get("detail") or "").strip()[:240], "href": f"/policies/{policy_id}",
            "since": result.get("checked_at"),
        })
    for job in _try(lambda: client.list_jobs(paths, status="pending_confirmation"), default=[]):
        items.append({
            "kind": "confirmation", "tag": "Jobs", "job_id": job.get("id"), "project": job.get("project_id") or "", "title": f"{job['type']} awaits confirmation",
            "detail": "", "href": "/jobs?status=pending_confirmation", "since": job.get("created_at"),
        })
    for job in _try(lambda: client.list_jobs(paths, status="running"), default=[]):
        if (job.get("progress") or {}).get("attention"):
            items.append({
                "kind": "job_question", "tag": "Jobs", "job_id": job.get("id"), "project": job.get("project_id") or "",
                "title": f"{job['type']} needs your answer", "detail": (job["progress"].get("current") or "")[:240],
                "href": f"/jobs/{job['id']}", "since": job.get("updated_at"),
            })
    for incident in _try(lambda: client.list_incidents(paths, status="open"), default=[]):
        items.append({
            "kind": "incident", "tag": "Incidents", "project": incident["project_id"],
            "title": f"{incident['exception_type']} in {incident['pillar']}",
            "detail": incident.get("message", "")[:240], "href": f"/incidents/{incident['id']}",
            "since": incident.get("created_at"),
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
    if moment.tzinfo is None:  # a pillar's own timestamps are UTC; a bare one must not 500 the page
        moment = moment.replace(tzinfo=timezone.utc)
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
    # No Swagger/ReDoc pages: `/docs` is the documentation-review page.
    app = FastAPI(title="little-workshop Dashboard", docs_url=None, redoc_url=None)
    install_incident_reporting(app, paths, pillar="dashboard")
    templates = _templates()
    static_dir = importlib.resources.files("dashboard") / "static"
    templates.env.filters["reltime"] = relative_time
    templates.env.filters["pretty"] = pretty_json
    templates.env.globals["pillar_name"] = pillar_name
    templates.env.globals["job_duration"] = job_duration
    templates.env.filters["secs"] = format_seconds
    # Cache-buster for every `/static/...?v=` URL: the newest static file, so a change to
    # any of them (review.js too, not only app.css) is fetched anew rather than served
    # from the browser's or the service worker's cache.
    templates.env.globals["static_v"] = int(max(
        os.path.getmtime(os.path.join(str(static_dir), name))
        for name in os.listdir(str(static_dir)) if os.path.isfile(os.path.join(str(static_dir), name))
    ))
    # follow_symlink: Bazel runfiles trees are symlink forests.
    app.mount("/static", StaticFiles(directory=str(static_dir), follow_symlink=True), name="static")
    starting = _Starting()

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

    @app.get("/starting.json")
    def starting_json() -> dict:
        return {project: sorted(kinds) for project, kinds in starting_up(paths, starting).items()}

    @app.get("/settings", response_class=HTMLResponse)
    def settings(request: Request):
        items = collect_attention(paths)
        return _render(request, "settings.html", items=items, attention_count=len(items), nav_active="settings")

    # -- Incidents: an unhandled exception, awaiting the user's confirmation -----

    @app.get("/incidents", response_class=HTMLResponse)
    def incidents(request: Request, status: str = "open"):
        rows = _try(lambda: client.list_incidents(paths, status=status or None), default=[])
        return _render(request, "incidents.html", incidents=rows, status=status, nav_active="incidents")

    @app.get("/incidents/{incident_id}", response_class=HTMLResponse)
    def incident_detail(request: Request, incident_id: str):
        incident = _try(lambda: client.get_incident(paths, incident_id), default=None)
        return _render(request, "incident_detail.html", incident=incident, nav_active="incidents")

    @app.post("/incidents/{incident_id}/confirm")
    def incident_confirm(incident_id: str, review_mode: str = Form("auto_approve")):
        result = _try(lambda: client.create_job(
            paths, type="agent.confirm_incident", target_pillar="agent", project_id=None,
            params={"incident_id": incident_id, "review_mode": review_mode},
            queue_key=None, depends_on=[], created_by="dashboard",
        ))
        if result is None or result.get("status") not in ("running", "succeeded"):
            return _back(f"/incidents/{incident_id}", f"Could not confirm: {(result or {}).get('error', 'agent pillar unreachable')}")
        return _back("/tasks", "Confirmed — the fix is under way.")

    @app.post("/incidents/{incident_id}/dismiss")
    def incident_dismiss(incident_id: str):
        result = _try(lambda: client.create_job(
            paths, type="agent.dismiss_incident", target_pillar="agent", project_id=None,
            params={"incident_id": incident_id}, queue_key=None, depends_on=[], created_by="dashboard",
        ))
        if result is None or result.get("status") != "succeeded":
            return _back(f"/incidents/{incident_id}", f"Could not dismiss: {(result or {}).get('error', 'agent pillar unreachable')}")
        return _back("/incidents", "Dismissed.")

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
        chosen = project if project in names else None
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
        req_counts = {v: n for v in VERDICT_ORDER if (n := sum(1 for r in data["requirements"] if r["verdict"] == v))}
        test_counts = {v: n for v in VERDICT_ORDER if (n := sum(1 for t in data["tests"] if t["verdict"] == v))}
        suites: dict[str, list[dict]] = {}
        for t in tests:
            suites.setdefault(t["suite"], []).append(t)
        return _render(
            request, "report.html", projects=names, project=chosen, q=q, verdict=verdict, requirements=reqs,
            suites=suites, req_counts=req_counts, test_counts=test_counts, req_total=len(data["requirements"]),
            test_total=len(data["tests"]), verdicts=VERDICT_ORDER, error=error, nav_active="report",
        )

    @app.post("/report/{project}/approve")
    def approve_requirements(project: str, requirement_id: list[str] = Form(default=[]), back: str = Form("")):
        """Publish drafted requirements — one ordinary `requirements.publish_requirement` Job each."""
        to = back if back.startswith("/report") else f"/report?project={project}"
        if not requirement_id:
            return _back(to, "No draft requirement selected.")
        approved, failed = 0, []
        for rid in dict.fromkeys(requirement_id):
            result = _try(lambda: client.create_job(
                paths, type="requirements.publish_requirement", target_pillar="requirements", project_id=project,
                params={"requirement_id": rid}, queue_key=None, depends_on=[], created_by="dashboard",
            ))
            if result is not None and result.get("status") == "succeeded":
                approved += 1
            else:
                failed.append((result or {}).get("error") or "requirements unreachable")
        message = f"Approved {approved} requirement{'' if approved == 1 else 's'}."
        if failed:
            message += f" {len(failed)} could not be approved: {failed[0]}"
        return _back(to, message)

    # ---- Project controls: server, session, work (on the Dashboard) -------

    @app.post("/projects/{name}/deploy")
    def deploy(name: str, branch: str = Form("dev")):
        if "launch" in starting_up(paths, starting).get(name, ()) or not starting.claim("launch", name):
            return _back(f"/?expand={name}", f"{name} is already launching — progress is on the Jobs page.")
        _fire_and_forget(
            paths, on_done=lambda: starting.release("launch", name), type="deployment.deploy", target_pillar="deployment", project_id=name, params={"branch": branch},
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

    # ---- Docs: documentation review ------------------------------------

    def _doc_update_entries(project: str) -> list[dict]:
        """This project's update-doc jobs that have not started or are running:
        Orchestration Jobs still queued/running, and scheduled ones."""
        entries = []
        for job in _try(client.list_jobs, paths, default=[]):
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

    @app.get("/docs", response_class=HTMLResponse)
    def docs_index(request: Request):
        try:
            names, error = _project_names(), None
        except client.PillarUnavailable as exc:
            names, error = [], str(exc)
        return _render(request, "docs.html", projects=names, error=error, nav_active="docs")

    @app.get("/docs/{project}", response_class=HTMLResponse)
    def docs_project(request: Request, project: str):
        error = None
        try:
            overview = client.docs_overview(paths, project)
        except (client.PillarUnavailable, client.Refused) as exc:
            overview, error = {"branch": "", "branches": [], "files": [], "dirty": False}, str(exc)
        entries = _doc_update_entries(project)
        branch = overview["branch"]
        open_comments = sum(f["open"] for f in overview["files"])
        recent = [u for u in _try(client.doc_updates, paths, project, default=[]) if u["status"] not in ("starting", "running")][:5]
        return _render(
            request, "docs_project.html", project=project, overview=overview, entries=entries,
            blocked=[e for e in entries if e["branch"] == branch], open_comments=open_comments, recent=recent,
            error=error, nav_active="docs",
        )

    @app.post("/docs/{project}/checkout")
    def docs_checkout(project: str, branch: str = Form(...), back: str = Form("")):
        back = back if back.startswith(f"/docs/{project}") else f"/docs/{project}"
        result = _try(lambda: client.create_job(
            paths, type="agent.docs_checkout", target_pillar="agent", project_id=project,
            params={"branch": branch}, queue_key=project, depends_on=[], created_by="dashboard",
        ))
        if result is None or result.get("status") != "succeeded":
            return _back(back, f"Could not check out {branch}: {(result or {}).get('error') or 'orchestration unreachable'}")
        return _back(back, f"Checked out {branch}.")

    @app.post("/docs/{project}/update")
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
        schedule, refusal = _parse_schedule("once", at, "", [], back)
        if refusal is not None:
            return refusal
        try:
            client.create_scheduled_task(paths, name=f"Update docs: {project} ({branch})", job_template=template, schedule=schedule)
        except client.PillarUnavailable as exc:
            return _back(back, f"Could not schedule the update: {exc}")
        return _back(back, f"Update-doc job for {branch} scheduled.")

    @app.post("/docs/{project}/update/scheduled/{task_id}/edit")
    def docs_update_edit(project: str, task_id: str, remote_control: str | None = Form(None), at: str = Form("")):
        back = f"/docs/{project}"
        task = _try(client.get_scheduled_task, paths, task_id)
        template = (task or {}).get("job_template") or {}
        if template.get("type") != DOC_UPDATE_JOB or template.get("project_id") != project:
            return _back(back, "That scheduled update no longer exists.")
        schedule, refusal = _parse_schedule("once", at, "", [], back)
        if refusal is not None:
            return refusal
        template = {**template, "params": {**(template.get("params") or {}), "remote_control": remote_control == "on"}}
        try:
            client.edit_scheduled_task(paths, task_id, job_template=template, schedule=schedule)
        except client.PillarUnavailable as exc:
            return _back(back, f"Could not change it: {exc}")
        return _back(back, "Scheduled update changed.")

    @app.post("/docs/{project}/update/scheduled/{task_id}/remove")
    def docs_update_remove(project: str, task_id: str):
        try:
            client.remove_scheduled_task(paths, task_id)
        except client.PillarUnavailable as exc:
            return _back(f"/docs/{project}", f"Could not delete it: {exc}")
        return _back(f"/docs/{project}", "Scheduled update deleted.")

    @app.post("/docs/{project}/update/jobs/{job_id}/stop")
    def docs_update_stop(project: str, job_id: str):
        try:
            result = client.stop_job(paths, job_id)
        except client.PillarUnavailable as exc:
            return _back(f"/docs/{project}", f"Could not stop it: {exc}")
        if not result.get("stopped"):
            return _back(f"/docs/{project}", f"Could not stop it: {result.get('reason')}")
        return _back(f"/docs/{project}", "Update-doc job stopped.")

    @app.get("/docs/{project}/view", response_class=HTMLResponse)
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

    @app.get("/docs/{project}/image")
    def docs_image(project: str, path: str):
        try:
            body, media_type = client.doc_image(paths, project, path)
        except client.Refused as exc:
            return Response(exc.detail, status_code=exc.status)
        except client.PillarUnavailable:
            return Response("agent pillar unreachable", status_code=503)
        return Response(body, media_type=media_type, headers={"Content-Security-Policy": "script-src 'none'"})

    @app.put("/docs/{project}/comments/{comment_id}")
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

    @app.delete("/docs/{project}/comments/{comment_id}")
    def docs_comment_delete(project: str, comment_id: str):
        try:
            return client.delete_review_comment(paths, project, comment_id)
        except client.Refused as exc:
            return JSONResponse({"detail": exc.detail}, status_code=exc.status)
        except client.PillarUnavailable as exc:
            return JSONResponse({"detail": str(exc)}, status_code=503)

    # ---- Tasks: Claude on a real branch, under review -------------------

    def _parse_schedule(kind: str, at: str, time_of_day: str, day: list[str], back: str):
        """Shared by New Task's and New Job's own "When" fields."""
        if kind == "once":
            try:
                when = datetime.fromisoformat(at)
            except ValueError:
                return None, _back(back, "Choose the date and time to start it.")
            if when <= datetime.now():
                return None, _back(back, "That time has already passed — choose a time in the future.")
            return {"kind": "once", "at": when.isoformat()}, None
        if kind == "recurring":
            days = sorted({int(d) for d in day if d.isdigit() and 0 <= int(d) <= 6})
            if not days or not re.match(r"^([01]\d|2[0-3]):[0-5]\d$", time_of_day):
                return None, _back(back, "Choose at least one weekday and a time of day.")
            return {"kind": "recurring", "days_of_week": days, "time_of_day": time_of_day}, None
        return None, _back(back, "Choose when it should run.")

    def _is_recurring_task(project: str, title: str, job: dict | None) -> bool:
        """A run launched by a weekly schedule comes back by itself; retrying it by hand makes no sense."""
        if not job or job.get("created_by") != "scheduler":
            return False
        return any(
            (t.get("schedule") or {}).get("kind") == "recurring" and (t.get("job_template") or {}).get("project_id") == project
            and ((t.get("job_template") or {}).get("params") or {}).get("title") == title
            for t in _try(client.scheduled_tasks, paths, default=[])
        )

    @app.get("/tasks", response_class=HTMLResponse)
    def tasks(request: Request):
        error = None
        rows: list[dict] = []
        try:
            names = _project_names()
        except client.PillarUnavailable as exc:
            names, error = [], str(exc)
        for name in names:
            rows += _try(client.agent_runs, paths, name, default=[])
        rows += _task_rows_without_a_run(_try(client.list_jobs, paths, default=[]), rows)
        rows.sort(key=lambda r: r.get("created_at") or "", reverse=True)
        task_sections = {key: [r for r in rows if _task_group(r) == key] for key, _ in TASK_GROUPS}
        return _render(request, "tasks.html", runs=rows, task_sections=task_sections, task_groups=TASK_GROUPS, error=error, nav_active="tasks")

    @app.get("/tasks/new", response_class=HTMLResponse)
    def task_new(request: Request, project: str | None = None):
        error = None
        try:
            names = _project_names()
        except client.PillarUnavailable as exc:
            names, error = [], str(exc)
        scheduled = [t for t in _try(lambda: client.scheduled_tasks(paths), default=[]) if (t.get("job_template") or {}).get("type") == "agent.run_task"]
        return _render(
            request, "task_new.html", projects=names, selected=project, scheduled=scheduled, weekdays=WEEKDAYS,
            error=error, nav_active="new-task",
        )

    @app.post("/tasks/new")
    def task_create(
        project: str = Form(...), title: str = Form(...), description: str = Form(""),
        intent: str = Form("task"), review_mode: str = Form("manual"), when: str = Form("immediate"),
        kind: str = Form("once"), at: str = Form(""), time_of_day: str = Form(""), day: list[str] = Form(default=[]),
        include_standards: list[str] = Form(default=["1"]),
    ):
        params = {
            "title": title, "description": description, "intent": intent, "review_mode": review_mode,
            "include_standards": "1" in include_standards,
        }
        if when == "immediate":
            result = _try(lambda: client.create_job(
                paths, type="agent.run_task", target_pillar="agent", project_id=project,
                params=params, queue_key=project, depends_on=[], created_by="dashboard",
            ))
            if result is None or result.get("status") not in ("running", "succeeded"):
                return _back("/tasks/new", f"Could not start the task: {(result or {}).get('error', 'orchestration unreachable')}")
            return _back("/tasks", f"Task started on {project}.")
        schedule, refusal = _parse_schedule(kind, at, time_of_day, day, "/tasks/new")
        if refusal is not None:
            return refusal
        template = {
            "type": "agent.run_task", "target_pillar": "agent", "project_id": project,
            "params": params, "queue_key": project, "depends_on": [],
        }
        try:
            client.create_scheduled_task(paths, name=f"{project}: {title}", job_template=template, schedule=schedule)
        except client.PillarUnavailable as exc:
            return _back("/tasks/new", f"Could not schedule the task: {exc}")
        return _back("/tasks/new", f"Task scheduled on {project}.")

    def _schedule_detail(request: Request, task_id: str, *, is_task: bool):
        base, back, back_label, nav = (
            ("/tasks/schedule", "/tasks/new", "New task", "new-task") if is_task
            else ("/jobs/schedule", "/jobs/new", "New job", "new-job")
        )
        try:
            task, error = client.get_scheduled_task(paths, task_id), None
        except client.PillarUnavailable as exc:
            task, error = None, str(exc)
        if task is not None and ((task.get("job_template") or {}).get("type") == "agent.run_task") != is_task:
            task = None  # a job opened under /tasks/schedule, or the reverse: not this page's to show
        last_job = _try(client.get_job, paths, task["last_job_id"]) if task and task.get("last_job_id") else None
        return _render(
            request, "schedule_detail.html", t=task, is_task=is_task, last_job=last_job, error=error,
            detail_base=base, back=back, back_label=back_label, weekdays=WEEKDAYS, nav_active=nav,
        )

    @app.get("/tasks/schedule/{task_id}", response_class=HTMLResponse)
    def task_schedule_detail(request: Request, task_id: str):
        return _schedule_detail(request, task_id, is_task=True)

    @app.post("/tasks/schedule/{task_id}/remove")
    def task_schedule_remove(task_id: str):
        try:
            client.remove_scheduled_task(paths, task_id)
        except client.PillarUnavailable as exc:
            return _back("/tasks/new", f"Could not remove it: {exc}")
        return _back("/tasks/new", "Scheduled task removed.")

    @app.get("/tasks/{project}/{run_id}", response_class=HTMLResponse)
    def task_detail(request: Request, project: str, run_id: str):
        run = _try(client.agent_run, paths, project, run_id)
        if run is None:
            return _render(request, "tasks.html", runs=[], error=f"no such task {run_id!r} on {project}", nav_active="tasks")
        job = _try(client.get_job, paths, run["job_id"]) if run.get("job_id") else None
        description = run.get("description") or str(((job or {}).get("params") or {}).get("description") or "")  # older runs: the Job has it
        can_retry = run.get("status") == "failed" and not _is_recurring_task(project, run.get("title"), job)
        can_dismiss = _task_group(run) == "attention" and run.get("status") in _DISMISSABLE_STATUSES
        return _render(request, "task_detail.html", run=run, description=description, can_retry=can_retry, can_dismiss=can_dismiss, nav_active="tasks")

    def _retryable_run(project: str, run_id: str):
        run = _try(client.agent_run, paths, project, run_id)
        if not run or run.get("status") != "failed":
            return None, None
        job = _try(client.get_job, paths, run["job_id"]) if run.get("job_id") else None
        if _is_recurring_task(project, run.get("title"), job):
            return None, None
        params = dict((job or {}).get("params") or {})
        params.update({k: run.get(k) for k in ("title", "intent", "review_mode") if run.get(k)})
        params["description"] = run.get("description") or params.get("description") or ""
        kept = {k: params.get(k, "") for k in ("title", "description", "intent", "review_mode")}
        kept["include_standards"] = params.get("include_standards", True)
        return run, kept

    @app.get("/tasks/{project}/{run_id}/retry", response_class=HTMLResponse)
    def task_retry(request: Request, project: str, run_id: str):
        run, params = _retryable_run(project, run_id)
        if run is None:
            return _back("/tasks", "Only a failed one-off task can be retried.")
        return _render(request, "task_retry.html", run=run, params=params, nav_active="tasks")

    @app.post("/tasks/{project}/{run_id}/retry")
    def task_retry_start(project: str, run_id: str, when: str = Form("immediate"), at: str = Form("")):
        run, params = _retryable_run(project, run_id)
        back = f"/tasks/{project}/{run_id}/retry"
        if run is None:
            return _back("/tasks", "Only a failed one-off task can be retried.")
        if when == "once":
            schedule, refusal = _parse_schedule("once", at, "", [], back)
            if refusal is not None:
                return refusal
            template = {
                "type": "agent.run_task", "target_pillar": "agent", "project_id": project,
                "params": params, "queue_key": project, "depends_on": [],
            }
            try:
                client.create_scheduled_task(paths, name=f"{project}: {params['title']}", job_template=template, schedule=schedule)
            except client.PillarUnavailable as exc:
                return _back(back, f"Could not schedule the task: {exc}")
            return _back("/tasks", f"Retry scheduled on {project}.")
        result = _try(lambda: client.create_job(
            paths, type="agent.run_task", target_pillar="agent", project_id=project,
            params=params, queue_key=project, depends_on=[], created_by="dashboard",
        ))
        if result is None or result.get("status") not in ("running", "succeeded"):
            return _back(back, f"Could not start the task: {(result or {}).get('error', 'orchestration unreachable')}")
        return _back("/tasks", f"Task started again on {project}.")

    @app.post("/tasks/{project}/{run_id}/{action}")
    def task_settle(project: str, run_id: str, action: str):
        if action not in ("finish", "skip", "dismiss"):
            return _back("/tasks", "Unknown action.")
        result = _try(lambda: client.create_job(
            paths, type=f"agent.{action}_task", target_pillar="agent", project_id=project, params={"run_id": run_id},
            queue_key=project, depends_on=[], created_by="dashboard",
        ))
        ok = result is not None and result.get("status") == "succeeded"
        if action == "dismiss":
            return _back("/tasks", "Moved to the done list." if ok else f"Could not move it: {(result or {}).get('error', 'orchestration unreachable')}")
        return _back(f"/tasks/{project}/{run_id}", f"Task {action}ed." if ok else f"Could not {action}: {(result or {}).get('error', 'orchestration unreachable')}")

    # ---- Pillars: the system's own components ---------------------------

    # ---- New Job (right away or scheduled): project -> job -> arguments (dashboard/job_catalog.py) ----

    def _discover_suites(project: str) -> list[dict]:
        """The "Test suites" choices: every suite, with how many of the project's
        current tests it covers (collected, not run)."""
        result = client.create_job(
            paths, timeout=340.0, type="requirements.discover_tests", target_pillar="requirements", project_id=project,
            params={"commitish": job_catalog.COMMITISH}, queue_key=None, depends_on=[], created_by="dashboard",
        )
        if result.get("status") != "succeeded":
            raise ValueError(result.get("error") or "test discovery failed")
        suites = sorted(result["result"]["suites"], key=lambda s: (-len(s["tests"]), s["name"].lower()))
        return [
            {"value": s["id"], "label": s["name"], "disabled": not s["tests"],
             "detail": f"{len(s['tests'])} test{'' if len(s['tests']) == 1 else 's'}" if s["tests"] else "no tests found"}
            for s in suites
        ]

    CHOICE_LOADERS = {"suites": _discover_suites}

    @app.get("/jobs/catalog/choices/{arg}")
    def job_arg_choices(arg: str, project: str = "") -> Response:
        loader = CHOICE_LOADERS.get(arg)
        if loader is None or not project:
            return Response(json.dumps({"error": "unknown argument or no project"}), status_code=404, media_type="application/json")
        try:
            choices = loader(project)
        except (client.PillarUnavailable, ValueError) as exc:
            return Response(json.dumps({"error": str(exc)}), status_code=502, media_type="application/json")
        return Response(json.dumps({"choices": choices}), media_type="application/json")

    def _job_page(request: Request, name: str, **context) -> HTMLResponse:
        return _render(
            request, name, projects=_try(_project_names, default=[]), catalog=job_catalog.CATALOG,
            chosen=context.pop("chosen", {}), **context,
        )

    def _scheduled_jobs() -> list[dict]:
        return [t for t in _try(lambda: client.scheduled_tasks(paths), default=[]) if (t.get("job_template") or {}).get("type") not in ("agent.run_task", DOC_UPDATE_JOB)]

    def _job_new_page(request: Request, build_error: str | None = None, chosen: dict | None = None,
                      when: str = "immediate") -> HTMLResponse:
        return _job_page(
            request, "job_new.html", build_error=build_error, chosen=chosen or {}, when=when,
            scheduled=_scheduled_jobs(), weekdays=WEEKDAYS, nav_active="new-job",
        )

    @app.get("/jobs/new", response_class=HTMLResponse)
    def job_new(request: Request):
        return _job_new_page(request)

    @app.post("/jobs/new", response_class=HTMLResponse)
    async def job_new_post(request: Request):
        """One form: "When" decides whether the job starts right away or is scheduled."""
        form = await request.form()
        project, job_key = str(form.get("project") or ""), str(form.get("job") or "")
        when = str(form.get("when") or "immediate")

        def render(error: str) -> HTMLResponse:
            return _job_new_page(request, error, {"project": project, "job": job_key}, when)

        template, err = job_catalog.build_job(project, job_key, {k: form.getlist(k) for k in form.keys()})
        if err:
            return render(err)
        label = job_catalog.BY_KEY[job_key].label
        if when == "immediate":
            # Builds and test runs outlast a browser request; progress is on the Jobs page.
            _fire_and_forget(paths, **template, depends_on=[], created_by="dashboard")
            return _back("/jobs", f"{label} started for {project}.")
        schedule, refusal = _parse_schedule(
            when, str(form.get("at") or ""), str(form.get("time_of_day") or ""),
            [str(d) for d in form.getlist("day")], "/jobs/new",
        )
        if refusal is not None:
            return render("Choose when it should run.")
        try:
            client.create_scheduled_task(paths, name=f"{label}: {project}", job_template=template, schedule=schedule)
        except client.PillarUnavailable as exc:
            return render(f"Could not schedule: {exc}")
        return _back("/jobs/new", f"{label} for {project} scheduled.")

    @app.get("/jobs/schedule")
    def job_schedule_redirect():
        # Schedule job was merged into New job; an old bookmark lands on it.
        return RedirectResponse("/jobs/new", status_code=307)

    @app.get("/jobs/schedule/{task_id}", response_class=HTMLResponse)
    def job_schedule_detail(request: Request, task_id: str):
        return _schedule_detail(request, task_id, is_task=False)

    @app.post("/jobs/schedule/{task_id}/remove")
    def job_schedule_remove(task_id: str):
        try:
            client.remove_scheduled_task(paths, task_id)
        except client.PillarUnavailable as exc:
            return _back("/jobs/new", f"Could not remove it: {exc}")
        return _back("/jobs/new", "Scheduled job removed.")

    @app.get("/pillars", response_class=HTMLResponse)
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
        shown = matching[:limit]
        job_steps = _try(lambda: client.jobs_steps(paths, [j["id"] for j in shown]), default={}) if shown else {}
        return _render(
            request, "jobs.html", job_steps_of=job_steps, cleanup_options=cleanup_options, jobs=shown, total=len(matching), limit=limit, next_limit=limit + JOBS_PAGE_SIZE,
            status=status, q=q or "", all_count=len(everything),
            counts=[(name, JOB_STATUS_LABELS.get(name, name), counts[name]) for name in [*JOB_STATUS_ORDER, *[k for k in counts if k not in JOB_STATUS_ORDER]] if counts[name]],
            active=sum(counts.get(name, 0) for name in JOB_ACTIVE_STATUSES), error=error, nav_active="jobs")

    @app.get("/jobs/{job_id}", response_class=HTMLResponse)
    def job_detail(request: Request, job_id: str):
        """One job and its checklist: what it does, where it is, how long each action took and is expected to take,
        and the question of any action waiting for the user. Refreshes itself while the job is active."""
        try:
            job, error = client.get_job(paths, job_id), None
        except client.PillarUnavailable as exc:
            job, error = None, str(exc)
        if job is None and error is None:
            return templates.TemplateResponse(request, "jobs.html", {
                "jobs": [], "total": 0, "counts": [], "status": None, "q": "", "all_count": 0, "cleanup_options": [], "active": 0,
                "next_limit": JOBS_PAGE_SIZE, "error": f"No job {job_id}.", "message": None, "nav_active": "jobs", "attention_count": 0,
            }, status_code=404)
        return _render(request, "job_detail.html", job=job, error=error, active=bool(job) and job["status"] in JOB_ACTIVE_STATUSES, nav_active="jobs")

    @app.post("/jobs/{job_id}/steps/{key}/answer")
    def job_step_answer(job_id: str, key: str, choice: str = Form("")):
        """The user's answer to an action waiting for them; the job carries on with it."""
        to = f"/jobs/{job_id}"
        try:
            client.answer_step(paths, job_id, key, choice)
        except client.PillarUnavailable as exc:
            return _back(to, f"Could not send the answer: {exc}")
        return _back(to, "Answer sent: the job carries on.")

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

    @app.post("/jobs/{job_id}/gate/{action}")
    def job_gate_action(job_id: str, action: str, next: str = Form("/jobs?status=pending_confirmation")):
        """Decide a policy-gated Job that waits in pending_confirmation: confirm it
        anyway, cancel it, or settle it as failed."""
        to = _local_path(next, "/jobs?status=pending_confirmation")
        calls = {
            "confirm": (client.confirm_gated_job, "confirmed", "Confirmed: the job is running despite the failed policy check.", "Could not confirm"),
            "cancel": (client.stop_job, "stopped", "Job cancelled.", "Could not cancel"),
            "fail": (client.fail_gated_job, "timed_out", "Job marked as failed.", "Could not fail"),
        }
        if action not in calls:
            return _back(to, f"Unknown action: {action}")
        call, flag, done, refusal = calls[action]
        try:
            result = call(paths, job_id)
        except client.PillarUnavailable as exc:
            return _back(to, f"{refusal}: {exc}")
        if not result.get(flag):
            return _back(to, f"{refusal}: {result.get('reason') or 'it is no longer waiting for confirmation'}")
        return _back(to, done)

    @app.get("/policies", response_class=HTMLResponse)
    def policies(request: Request, message: str | None = None):
        try:
            rows, error = client.policy_list(paths), None
        except client.PillarUnavailable as exc:
            rows, error = [], str(exc)
        pending = _try(lambda: client.policy_drafts(paths, status="pending"), default=[])
        if rows:
            latest = _latest_policy_results(paths)
            for p in rows:
                associated = _policy_associated_projects(p)
                p["failing_count"] = sum(
                    1 for (pid, proj), r in latest.items()
                    if pid == p["id"] and proj in associated and r["outcome"] == "fail"
                )
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
        draft_gating = {p["project_id"]: p["gating_enabled"] for p in (draft or {}).get("projects", [])}
        return _render(
            request, "draft_detail.html", draft=draft, projects=sorted(registered), draft_gating=draft_gating,
            error=error, gate_point_values=GATE_POINT_VALUES, nav_active="policies",
        )

    @app.post("/policies/drafts/{draft_id}/approve")
    def policy_draft_approve(
        draft_id: str, gate_points: list[str] = Form([]), project: list[str] = Form([]), gating: list[str] = Form([]),
    ):
        """Gate 4, in the app: pressing Approve is itself the reviewer's confirmation —
        no separate checkbox or typed reason needed."""
        back = f"/policies/drafts/{draft_id}"
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
        remaining = _try(lambda: client.policy_drafts(paths, status="pending"), default=[])
        next_draft = next((d for d in remaining if d["id"] != draft_id), None)
        if next_draft:
            return _back(f"/policies/drafts/{next_draft['id']}", "Approved and activated. Next draft:")
        return _back("/policies", "Approved and activated. No more drafts awaiting approval.")

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

    # ---- New/Edit Policy: authored by Claude, filed as a draft -----------

    @app.get("/policies/new", response_class=HTMLResponse)
    def policy_new(request: Request):
        return _render(
            request, "policy_new.html", policy=None, gate_point_values=GATE_POINT_VALUES,
            author_error=None, nav_active="new-policy",
        )

    @app.post("/policies/new", response_class=HTMLResponse)
    def policy_new_post(
        request: Request, ears_text: str = Form(...), topic: str = Form(...), target_pillar: str = Form(...),
        scope_level: str = Form("all_projects"), scope_target: str = Form(""), gate_points: list[str] = Form([]),
    ):
        return _author_policy(
            request, None, ears_text, topic, target_pillar, scope_level, scope_target, gate_points, "policy_new.html",
        )

    @app.get("/policies/{policy_id}/edit", response_class=HTMLResponse)
    def policy_edit(request: Request, policy_id: str):
        policy = _try(lambda: client.policy_get(paths, policy_id))
        if policy is None:
            return _render(request, "policy_new.html", policy=None, gate_point_values=GATE_POINT_VALUES, author_error="Policy not found.", nav_active="policies")
        return _render(
            request, "policy_new.html", policy=policy, gate_point_values=GATE_POINT_VALUES,
            author_error=None, nav_active="policies",
        )

    @app.post("/policies/{policy_id}/edit", response_class=HTMLResponse)
    def policy_edit_post(
        request: Request, policy_id: str, ears_text: str = Form(...), topic: str = Form(...),
        target_pillar: str = Form(...), scope_level: str = Form("all_projects"), scope_target: str = Form(""),
        gate_points: list[str] = Form([]),
    ):
        return _author_policy(
            request, policy_id, ears_text, topic, target_pillar, scope_level, scope_target, gate_points, "policy_new.html",
        )

    def _author_policy(request, policy_id, ears_text, topic, target_pillar, scope_level, scope_target, gate_points, template_name):
        params = {
            "ears_text": ears_text, "topic": topic, "target_pillar": target_pillar, "scope_level": scope_level,
            "created_by": "user", "gate_points": gate_points, "save_draft": True, "source": "dashboard",
        }
        if scope_level != "all_projects":
            params["scope_target"] = scope_target
        if policy_id:
            params["policy_id"] = policy_id
        try:
            job = client.create_job(
                paths, timeout=180.0, type="policy.author", target_pillar="policy", project_id=None,
                params=params, queue_key=None, depends_on=[], created_by="dashboard",
            )
        except client.PillarUnavailable as exc:
            job = {"status": "failed", "error": str(exc)}
        if job.get("status") != "succeeded":
            fallback = {
                "ears_text": ears_text, "topic": topic, "target_pillar": target_pillar, "scope_level": scope_level,
                "scope_target": scope_target, "gate_points": gate_points, "id": policy_id,
            }
            return _render(
                request, template_name, policy=fallback, gate_point_values=GATE_POINT_VALUES,
                author_error=job.get("error") or "The authoring pipeline refused this policy.", nav_active="policies",
            )
        draft_id = job["result"].get("draft_id")
        if draft_id:
            return _back(f"/policies/drafts/{draft_id}", "Authored — review and approve when ready.")
        return _back("/policies", "Authored.")

    @app.get("/policies/{policy_id}", response_class=HTMLResponse)
    def policy_detail(request: Request, policy_id: str):
        script_link = None
        registered: list[str] = []
        project_status: list[dict] = []
        failing_count = 0
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
            latest = _latest_policy_results(paths)
            for name in sorted(selected):
                result = latest.get((policy["id"], name))
                outcome = result.get("outcome") if result else None
                if outcome == "fail":
                    failing_count += 1
                project_status.append({
                    "name": name, "gating": selected[name].get("gating_enabled", True),
                    "outcome": outcome, "detail": (result.get("detail") or "").strip() if result else "",
                    "checked_at": result.get("checked_at") if result else None,
                })
        else:
            project_rows = []
        return _render(
            request, "policy_detail.html", policy=policy, project_rows=project_rows, script_link=script_link,
            project_status=project_status, failing_count=failing_count,
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
