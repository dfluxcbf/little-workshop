"""Dashboard routes: requirement conflicts and the Report page."""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from dashboard import client, prefs
from dashboard.app import (
    CONFLICT_DECISIONS, CONFLICT_KINDS, VERDICT_ORDER, _back, _try, build_report, only_registered, registered_names,
)


def build_router(ctx) -> APIRouter:
    router = APIRouter()
    paths, _render = ctx.paths, ctx.render

    # -- Requirement conflicts: found by Requirements & Quality, resolved by a Claude task ----------

    @router.get("/requirements/conflicts", response_class=HTMLResponse)
    def requirement_conflicts(request: Request, status: str = "open", project: str = ""):
        try:
            rows, error = client.requirement_conflicts(paths, project_id=project or None, status=status or None), None
            if not project:
                rows = only_registered(rows, registered_names(paths))
        except client.PillarUnavailable as exc:
            rows, error = [], str(exc)
        return _render(request, "conflicts.html", conflicts=rows, status=status, project=project, error=error,
                       conflict_kinds=CONFLICT_KINDS, nav_active="conflicts", live_topics="conflicts")

    @router.get("/requirements/conflicts/{conflict_id}", response_class=HTMLResponse)
    def requirement_conflict_detail(request: Request, conflict_id: str):
        conflict = _try(lambda: client.get_requirement_conflict(paths, conflict_id), default=None)
        return _render(request, "conflict_detail.html", c=conflict, conflict_kinds=CONFLICT_KINDS,
                       decisions=CONFLICT_DECISIONS, nav_active="conflicts", live_topics="conflicts")

    @router.post("/requirements/conflicts/{conflict_id}/resolve")
    def requirement_conflict_resolve(conflict_id: str, decision: str = Form(...), guidance: str = Form(""),
                                     review_mode: str = Form("manual")):
        back = f"/requirements/conflicts/{conflict_id}"
        if decision not in CONFLICT_DECISIONS:
            return _back(back, "Choose how to resolve the conflict.")
        if decision == "other" and not guidance.strip():
            return _back(back, "Another approach needs a description of what to do.")
        conflict = _try(lambda: client.get_requirement_conflict(paths, conflict_id), default=None)
        if conflict is None:
            return _back(back, "The conflict could not be read: Requirements & Quality is unreachable or no longer has it.")
        result = _try(lambda: client.create_job(
            paths, type="agent.resolve_requirement_conflict", target_pillar="agent", project_id=conflict["project_id"],
            params={"conflict_id": conflict_id, "decision": decision, "guidance": guidance.strip() if decision == "other" else "",
                    "review_mode": review_mode},
            queue_key=conflict["project_id"], depends_on=[], created_by="dashboard",
        ))
        if result is None or result.get("status") not in ("running", "succeeded", "queued"):
            return _back(back, f"Could not start the fix: {(result or {}).get('error', 'orchestration unreachable')}")
        return _back(back, "A Claude task is resolving the conflict — it is on the Tasks page.")

    # ---- Report: requirements and tests with their verdicts ---------------

    @router.get("/report", response_class=HTMLResponse)
    def report(request: Request, project: str | None = None, q: str = "", verdict: str = ""):
        error = None
        try:
            names = prefs.ordered(paths, "dashboard", ctx.project_names())
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

    @router.post("/report/{project}/approve")
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

    return router
