"""Dashboard routes: the backlog."""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from dashboard import client, live, markdown, prefs
from dashboard.app import (
    BACKLOG_PRIORITIES, BACKLOG_STATUSES, _back, _fire_and_forget, _try, backlog_activity, demote_headings,
)


def build_router(ctx) -> APIRouter:
    router = APIRouter()
    paths, _render = ctx.paths, ctx.render

    # ---- Backlog: each project's entries, refined with Claude into tasks (Registry & Portfolio) ----

    def _backlog_entry(project: str, entry_id: str) -> dict | None:
        """The entry, or None when it is not (or no longer) in this project's backlog."""
        entry = client.backlog_get(paths, entry_id)
        return entry if entry is not None and entry.get("project_name") == project else None

    @router.get("/backlog", response_class=HTMLResponse)
    def backlog_index(request: Request):
        error = None
        try:
            names = prefs.ordered(paths, "dashboard", ctx.project_names())
            entries = client.backlog_list(paths, status="open")  # the counts are of what is still to do
        except client.PillarUnavailable as exc:
            names, entries, error = [], [], str(exc)
        counts: dict[str, int] = {}
        for entry in entries:
            counts[entry["project_name"]] = counts.get(entry["project_name"], 0) + 1
        return _render(request, "backlog.html", projects=[{"name": n, "count": counts.get(n, 0)} for n in names],
                       error=error, nav_active="backlog", live_topics="backlog")

    @router.get("/backlog/{project}", response_class=HTMLResponse)
    def backlog_project(request: Request, project: str, status: str = "open"):
        """The entries in one state (`status`: open by default, done, dropped or all), highest priority first,
        then the most recently updated (REQ-000000524)."""
        status = status if status in (*BACKLOG_STATUSES, "all") else "open"
        try:
            entries, error = client.backlog_list(paths, project, status=None if status == "all" else status), None
        except client.PillarUnavailable as exc:
            entries, error = [], str(exc)
        entries.sort(key=lambda e: e.get("updated_at") or "", reverse=True)
        entries.sort(key=lambda e: BACKLOG_PRIORITIES.index(e.get("priority") or "normal"))
        activity = {e["id"]: backlog_activity(e) for e in entries}
        return _render(request, "backlog_project.html", project=project, entries=entries, activity=activity, status=status,
                       statuses=(*BACKLOG_STATUSES, "all"), error=error, nav_active="backlog", live_topics="backlog")

    @router.post("/backlog/{project}/add")
    def backlog_add(project: str, text: str = Form("")):
        try:
            entry = client.backlog_add(paths, project, text)
        except client.Refused as exc:
            return _back(f"/backlog/{project}", f"Not added: {exc.detail}")
        except client.PillarUnavailable as exc:
            return _back(f"/backlog/{project}", f"Not added: {exc}")
        live.publish("backlog")
        return _back(f"/backlog/{project}/{entry['id']}", "Added to the backlog.")

    @router.get("/backlog/{project}/{entry_id}", response_class=HTMLResponse)
    def backlog_entry(request: Request, project: str, entry_id: str):
        try:
            entry, error = _backlog_entry(project, entry_id), None
        except client.PillarUnavailable as exc:
            entry, error = None, str(exc)
        activity = backlog_activity(entry) if entry else backlog_activity({})
        refined_html = demote_headings(markdown.render_comment(entry["refined_entry"], project=project, path="")) if entry and entry["refined_entry"] else ""
        # While Claude works on the entry, its session is shown as it grows (the Job's peek).
        working = activity["refine"] or activity["prompt"]
        claude_peek = ctx.peek_of(working) if working else None
        topics = ["backlog", *([f"peek:{working}"] if claude_peek and claude_peek.get("running") else [])]
        # The task launched from the entry: once its run succeeded, the page offers to mark the entry done.
        task_run = _try(client.agent_run_for_job, paths, entry["task_job"]) if entry and entry.get("task_job") else None
        task_succeeded = bool(task_run) and task_run.get("status") in ("awaiting_finish", "finished") and task_run.get("completion_status") == "done"
        return _render(request, "backlog_entry.html", project=project, entry_id=entry_id, entry=entry, activity=activity,
                       refined_html=refined_html, peek=claude_peek, error=error, nav_active="backlog", live_topics=",".join(topics),
                       task_run=task_run, task_succeeded=task_succeeded, statuses=BACKLOG_STATUSES, priorities=BACKLOG_PRIORITIES)

    @router.post("/backlog/{project}/{entry_id}/state")
    def backlog_state(project: str, entry_id: str, status: str = Form(""), priority: str = Form("")):
        """Set the entry's status and/or priority (REQ-000000524)."""
        back = f"/backlog/{project}/{entry_id}"
        fields = {k: v for k, v in (("status", status), ("priority", priority)) if v}
        if not fields:
            return _back(back, "Nothing to change.")
        try:
            if _backlog_entry(project, entry_id) is None:
                return _back(f"/backlog/{project}", "That entry is no longer in the backlog.")
            client.backlog_edit(paths, entry_id, **fields)
        except (client.Refused, client.PillarUnavailable) as exc:
            return _back(back, f"Not changed: {getattr(exc, 'detail', exc)}")
        live.publish("backlog")
        return _back(back, f"Marked {status}." if status else f"Priority set to {priority}.")

    def _save_backlog_edits(project: str, entry_id: str, primary_entry: str | None, refinement_notes: str | None) -> str | None:
        """Save what the user changed in the entry's two fields; the refusal's reason, or None once saved."""
        try:
            entry = _backlog_entry(project, entry_id)
            if entry is None:
                return "the entry is no longer in the backlog"
            changed = {key: value for key, value in (("primary_entry", primary_entry), ("refinement_notes", refinement_notes))
                       if value is not None and value.replace("\r\n", "\n") != entry[key]}
            if changed:
                client.backlog_edit(paths, entry_id, **{k: v.replace("\r\n", "\n") for k, v in changed.items()})
                live.publish("backlog")
        except client.Refused as exc:
            return exc.detail
        except client.PillarUnavailable as exc:
            return str(exc)
        return None

    @router.post("/backlog/{project}/{entry_id}/save")
    def backlog_save(project: str, entry_id: str, primary_entry: str | None = Form(None), refinement_notes: str | None = Form(None)):
        back = f"/backlog/{project}/{entry_id}"
        refusal = _save_backlog_edits(project, entry_id, primary_entry, refinement_notes)
        return _back(back, f"Not saved: {refusal}" if refusal else "Saved.")

    def _start_backlog_job(project: str, entry_id: str, kind: str, job_type: str, started: str) -> RedirectResponse:
        back = f"/backlog/{project}/{entry_id}"
        entry = _try(_backlog_entry, project, entry_id)
        if entry is not None and backlog_activity(entry)[kind]:
            return _back(back, "Claude is already working on it.")
        _fire_and_forget(paths, type=job_type, target_pillar="registry", project_id=project, params={"entry_id": entry_id},
                         queue_key=None, depends_on=[], created_by="dashboard")
        return _back(back, started)

    @router.post("/backlog/{project}/{entry_id}/refine")
    def backlog_refine(project: str, entry_id: str, primary_entry: str | None = Form(None), refinement_notes: str | None = Form(None)):
        """Save what was typed, then have Claude refine the entry from it."""
        refusal = _save_backlog_edits(project, entry_id, primary_entry, refinement_notes)
        if refusal:
            return _back(f"/backlog/{project}/{entry_id}", f"Not refined: {refusal}")
        return _start_backlog_job(project, entry_id, "refine", "registry.backlog_refine",
                                  "Claude is refining the entry: the refined entry replaces the one shown when it is done.")

    @router.post("/backlog/{project}/{entry_id}/prompt")
    def backlog_prompt(project: str, entry_id: str):
        entry = _try(_backlog_entry, project, entry_id)
        if entry is None:
            return _back(f"/backlog/{project}", "That entry is no longer in the backlog.")
        if not entry["refined_entry"].strip():
            return _back(f"/backlog/{project}/{entry_id}", "Refine the entry first: the task prompt is written from the refined entry.")
        return _start_backlog_job(project, entry_id, "prompt", "registry.backlog_task_prompt",
                                  "Claude is writing the task prompt: it replaces the one shown when it is done.")

    @router.post("/backlog/{project}/{entry_id}/delete")
    def backlog_delete(project: str, entry_id: str):
        try:
            if _backlog_entry(project, entry_id) is not None:
                client.backlog_delete(paths, entry_id)
        except (client.Refused, client.PillarUnavailable) as exc:
            return _back(f"/backlog/{project}/{entry_id}", f"Not deleted: {getattr(exc, 'detail', exc)}")
        live.publish("backlog")
        return _back(f"/backlog/{project}", "Deleted from the backlog: nothing of it is kept.")

    return router
