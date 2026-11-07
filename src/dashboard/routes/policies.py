"""Dashboard routes: the policy bypass, gated Jobs and the Policies pages."""

from __future__ import annotations

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from dashboard import client
from dashboard.app import (
    GATE_POINT_VALUES, POLICY_STATUS_FILTERS, _back, _latest_policy_results, _local_path,
    _policy_associated_projects, _try, policy_matches,
)


def build_router(ctx) -> APIRouter:
    router = APIRouter()
    paths, _render = ctx.paths, ctx.render

    @router.post("/policy-bypass/off")
    def policy_bypass_off(next: str = Form("/")):
        """The warning's button: `bypass_policies` back to FALSE at once."""
        to = _local_path(next, "/")
        try:
            client.policy_bypass_off(paths)
        except client.PillarUnavailable as exc:
            return _back(to, f"Could not turn the policy bypass off: {exc}")
        return _back(to, "bypass_policies is FALSE again: policies gate as usual.")

    @router.post("/jobs/{job_id}/gate/{action}")
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

    @router.get("/policies", response_class=HTMLResponse)
    def policies(
        request: Request, message: str | None = None, q: str = "", status: str = "", gate: list[str] = Query(default=[]),
        project: list[str] = Query(default=[]), gating: str = "",
    ):
        try:
            rows, error = client.policy_list(paths, summary=True), None
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
        filters = {"q": q, "status": status if status in dict(POLICY_STATUS_FILTERS) else "", "gate": [g for g in gate if g in GATE_POINT_VALUES],
                   "project": list(dict.fromkeys(project)), "gating": gating == "1"}
        for p in rows:
            p["matches"] = policy_matches(p, q=filters["q"], status=filters["status"], gates=filters["gate"], projects=filters["project"],
                                          gating=filters["gating"])
        governed = sorted({name for p in rows for name in _policy_associated_projects(p)} | set(_try(ctx.project_names, default=[])))
        return _render(
            request, "policies.html", policies=rows, error=error, message=message, filters=filters, status_filters=POLICY_STATUS_FILTERS,
            filter_projects=governed, shown=sum(1 for p in rows if p["matches"]),
            pending_drafts=len(pending), gate_point_values=GATE_POINT_VALUES, nav_active="policies", live_topics="policies",
        )

    # Drafts awaiting approval. Declared before `/policies/{policy_id}` so "drafts" is never read as an id.

    @router.get("/policies/drafts", response_class=HTMLResponse)
    def policy_drafts(request: Request, message: str | None = None):
        try:
            pending, error = client.policy_drafts(paths, status="pending"), None
        except client.PillarUnavailable as exc:
            pending, error = [], str(exc)
        rejected = _try(lambda: client.policy_drafts(paths, status="rejected"), default=[])
        return _render(
            request, "drafts.html", pending=pending, rejected=rejected, error=error, message=message, nav_active="policies",
        )

    @router.get("/policies/drafts/{draft_id}", response_class=HTMLResponse)
    def policy_draft_detail(request: Request, draft_id: str):
        error = None
        try:
            draft = client.policy_draft(paths, draft_id)
        except client.PillarUnavailable as exc:
            draft, error = None, str(exc)
        registered = _try(lambda: ctx.project_names(), default=[]) if draft else []
        draft_gating = {p["project_id"]: p["gating_enabled"] for p in (draft or {}).get("projects", [])}
        return _render(
            request, "draft_detail.html", draft=draft, projects=sorted(registered), draft_gating=draft_gating,
            error=error, gate_point_values=GATE_POINT_VALUES, nav_active="policies",
        )

    @router.post("/policies/drafts/{draft_id}/approve")
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

    @router.post("/policies/drafts/{draft_id}/reject")
    def policy_draft_reject(draft_id: str, reason: str = Form("")):
        job = _try(lambda: client.create_job(
            paths, type="policy.reject_draft", target_pillar="policy", project_id=None,
            params={"draft_id": draft_id, "reason": reason}, queue_key=None, depends_on=[], created_by="dashboard",
        ))
        ok = job is not None and job.get("status") == "succeeded"
        return _back("/policies/drafts", "Draft rejected." if ok else f"Could not reject: {(job or {}).get('error', 'policy unreachable')}")

    @router.post("/policies/drafts/{draft_id}/restore")
    def policy_draft_restore(draft_id: str):
        job = _try(lambda: client.create_job(
            paths, type="policy.restore_draft", target_pillar="policy", project_id=None,
            params={"draft_id": draft_id}, queue_key=None, depends_on=[], created_by="dashboard",
        ))
        ok = job is not None and job.get("status") == "succeeded"
        return _back("/policies/drafts", "Draft restored." if ok else f"Could not restore: {(job or {}).get('error', 'policy unreachable')}")

    # ---- New/Edit Policy: authored by Claude, filed as a draft -----------

    @router.get("/policies/new", response_class=HTMLResponse)
    def policy_new(request: Request):
        return _render(
            request, "policy_new.html", policy=None, gate_point_values=GATE_POINT_VALUES,
            author_error=None, nav_active="new-policy",
        )

    @router.post("/policies/new", response_class=HTMLResponse)
    def policy_new_post(
        request: Request, ears_text: str = Form(...), topic: str = Form(...), target_pillar: str = Form(...),
        scope_level: str = Form("all_projects"), scope_target: str = Form(""), gate_points: list[str] = Form([]),
    ):
        return _author_policy(
            request, None, ears_text, topic, target_pillar, scope_level, scope_target, gate_points, "policy_new.html",
        )

    @router.get("/policies/{policy_id}/edit", response_class=HTMLResponse)
    def policy_edit(request: Request, policy_id: str):
        policy = _try(lambda: client.policy_get(paths, policy_id))
        if policy is None:
            return _render(request, "policy_new.html", policy=None, gate_point_values=GATE_POINT_VALUES, author_error="Policy not found.", nav_active="policies")
        return _render(
            request, "policy_new.html", policy=policy, gate_point_values=GATE_POINT_VALUES,
            author_error=None, nav_active="policies",
        )

    @router.post("/policies/{policy_id}/edit", response_class=HTMLResponse)
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

    @router.get("/policies/{policy_id}", response_class=HTMLResponse)
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
            registered = _try(lambda: ctx.project_names(), default=[])
            script_link = _try(client.policy_script_link, paths, policy.get("test_script", ""), default=None)
            selected = {p["project_id"]: p for p in policy.get("projects", [])}
            names = list(dict.fromkeys([*registered, *selected]))
            project_rows = [
                {"name": n, "selected": n in selected, "gating": selected.get(n, {}).get("gating_enabled", True),
                 "registered": n in registered}
                for n in sorted(names)
            ]
            latest = _latest_policy_results(paths, policy_id=policy["id"])
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

    @router.post("/policies/{policy_id}/configure")
    def policy_configure(policy_id: str, enabled: str | None = Form(None), gate_points: list[str] = Form([])):
        client.create_job(
            paths, type="policy.configure", target_pillar="policy", project_id=None,
            params={"policy_id": policy_id, "enabled": enabled == "true", "gate_points": gate_points},
            queue_key=None, depends_on=[], created_by="dashboard",
        )
        return RedirectResponse(f"/policies/{policy_id}", status_code=303)

    @router.post("/policies/{policy_id}/projects")
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

    return router
