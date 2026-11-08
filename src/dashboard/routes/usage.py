"""Dashboard routes: Claude usage, the toolkit and the triggers."""

from __future__ import annotations

import shlex

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from dashboard import client
from dashboard.app import WEEKDAYS, _back, _fire_and_forget, _try


def build_router(ctx) -> APIRouter:
    router = APIRouter()
    paths, _render = ctx.paths, ctx.render

    # -- Claude usage: what the Usage & Efficiency pillar holds, one week at a time ----

    @router.get("/usage", response_class=HTMLResponse)
    def usage_page(request: Request, week: str | None = None, project: str | None = None, origin: str | None = None):
        try:
            summary = client.usage_summary(paths, week=week or None, project=project or None, origin=origin or None)
            report = _try(client.usage_doctor, paths)
            findings = _try(lambda: client.usage_findings(paths, project=project or None, state="all"), default=[])
            candidates = _try(client.usage_candidates, paths, default=[])
            proposals = _try(client.usage_plugin_proposals, paths, default=[])
            method = _try(client.usage_estimate_method, paths)
            explorer = _try(client.usage_explorer, paths)
            error = None
        except client.PillarUnavailable:
            summary, report, findings, candidates, method, explorer, proposals = None, None, [], [], None, None, []
            error = "The Usage & Efficiency pillar is not reachable, so there is nothing to show. It is optional: check it on the Pillars page."
        return _render(request, "usage.html", summary=summary, doctor=report, error=error, nav_active="usage",
                       origins=("interactive", "job", "query", "subagent"), findings=findings, candidates=candidates,
                       proposals=proposals, projects=sorted(p["name"] for p in _try(client.list_projects, paths, default=[]) or []),
                       weekdays=WEEKDAYS, method=method, explorer=explorer, live_topics="usage")

    @router.post("/usage/candidates/{candidate_id}/{action}")
    def usage_candidate_action(candidate_id: str, action: str):
        """Make it a task (New task, filled with the candidate's task), dismiss, or open again."""
        if action == "automate":
            return RedirectResponse(f"/tasks/new?candidate={candidate_id}", status_code=303)
        if action in ("dismiss", "reopen"):
            _try(lambda: client.set_usage_candidate(paths, candidate_id, state="dismissed" if action == "dismiss" else "open"))
        return RedirectResponse("/usage#candidates", status_code=303)

    @router.post("/usage/draft_skill")
    def usage_draft_skill(kind: str = Form(...), row_id: str = Form(...), project: str = Form("")):
        """Draft as a skill (REQ-000000508): the fixed agent task writing the candidate or proposal as a draft skill."""
        if kind not in ("candidate", "proposal"):
            return _back("/usage", "Unknown kind.")
        result = _try(lambda: client.create_job(paths, type="usage.draft_skill", target_pillar="usage", project_id=project or None,
                                                params={f"{kind}_id": row_id}, queue_key="usage", depends_on=[], created_by="dashboard"))
        done = (result or {}).get("result") or {}
        if result is None or result.get("status") != "succeeded":
            return _back("/usage", f"Could not start the draft: {(result or {}).get('error') or 'the Usage pillar is unreachable'}")
        return _back("/tasks", f"Drafting skill {done.get('skill')} in {done.get('project_id')}: review it on its branch when the task ends.")

    # -- System > Toolkit: public skills, agents, commands and plugins per project (registry/toolkit.py) ----------

    @router.get("/toolkit", response_class=HTMLResponse)
    def toolkit_page(request: Request, project: str | None = None):
        sources = _try(client.toolkit_sources, paths)
        projects = sorted(p["name"] for p in _try(client.list_projects, paths, default=[]) or [] if p.get("status", "active") == "active")
        status = _try(lambda: client.toolkit_status(paths, project)) if project else None
        view = toolkit_view(sources or [], status) if status else None
        return _render(request, "toolkit.html", nav_active="toolkit", sources=sources, projects=projects, project=project, status=status,
                       view=view, error=None if sources is not None else "The Registry pillar is not reachable.")

    @router.post("/toolkit/{action}")
    def toolkit_action(action: str, repo: str = Form(""), project: str = Form(""), items: list[str] = Form([]),
                       pick: list[str] = Form([]), confirm_code: str = Form(""), mcp_name: str = Form(""),
                       mcp_command: str = Form(""), mcp_env: str = Form(""), licence: str = Form(""), version: str = Form("")):
        """Add a source (or the defaults), install, remove or update: each a Registry Job that outlasts the tap, so it
        runs in the background and its progress is on the Jobs page. An install takes every ticked item (`pick`,
        "<repo> <item id>", from any source) as one Job and one commit on the integration branch (REQ-000000533)."""
        if action not in ("add_source", "add_mcp", "seed", "install", "remove", "update"):
            return _back("/toolkit", "Unknown action.")
        back = f"/toolkit?project={project}" if project else "/toolkit"
        if action == "add_mcp":  # an MCP server a repository provides (REQ-000000535): registered, never installed here
            try:
                words = shlex.split(mcp_command)
            except ValueError as exc:
                return _back(back, f"The command could not be read: {exc}.")
            env = dict(line.split("=", 1) for line in mcp_env.splitlines() if "=" in line)
            if not (repo.strip() and mcp_name.strip() and words):
                return _back(back, "Give the repository, a server name and its command.")
            params = {"repo": repo.strip(), "licence": licence.strip(), "version": version.strip(), "mcp_servers": {
                mcp_name.strip(): {"command": words[0], "args": words[1:], **({"env": {k.strip(): v.strip() for k, v in env.items()}} if env else {})}}}
            _fire_and_forget(paths, type="registry.toolkit_add_source", target_pillar="registry", project_id=None,
                             params={k: v for k, v in params.items() if v}, queue_key="toolkit", depends_on=[], created_by="dashboard")
            return _back(back, f"Started: register MCP server {mcp_name.strip()} of {repo.strip()}. Nothing is installed.")
        params: dict = {"seed": True} if action == "seed" else {"repo": repo.strip()}
        if action == "install" and pick:
            selections: dict[str, list[str]] = {}
            for value in pick:
                source, _, item = value.strip().partition(" ")
                if source and item and item not in selections.setdefault(source, []):
                    selections[source].append(item)
            params = {"selections": [{"repo": r, "items": i} for r, i in selections.items()]}
        elif action in ("install", "remove"):
            params["items"] = items
        if action == "install" and not (pick or items):
            return _back(back, "Select at least one item to install.")
        if action in ("install", "update"):
            params["confirm_code"] = confirm_code == "1"
        kind = "add_source" if action == "seed" else action
        _fire_and_forget(paths, type=f"registry.toolkit_{kind}", target_pillar="registry", project_id=project or None, params=params,
                         queue_key=project or "toolkit", depends_on=[], created_by="dashboard")
        count = sum(len(s["items"]) for s in params.get("selections", [])) or len(items)
        what = f"install {count} item{'s' if count != 1 else ''}" if action == "install" else f"{action.replace('_', ' ')} {repo or 'the default sources'}"
        return _back(back, f"Started: {what}. Its progress is on the Jobs page; "
                           + ("the change is one commit on the project's integration branch." if project else "nothing is installed."))

    # -- Triggers: an event starts a Job (Orchestration) -------------------------------

    @router.get("/triggers", response_class=HTMLResponse)
    def triggers_page(request: Request):
        try:
            rows, error = client.triggers(paths), None
        except client.PillarUnavailable as exc:
            rows, error = [], str(exc)
        return _render(request, "triggers.html", triggers=rows, error=error, nav_active="triggers")

    @router.post("/triggers/install-defaults")
    def triggers_install_defaults():
        _try(lambda: client.trigger_action(paths, "POST", "/install-defaults"))
        return RedirectResponse("/triggers", status_code=303)

    @router.post("/triggers/{trigger_id}/{action}")
    def trigger_action(trigger_id: str, action: str):
        if action in ("enable", "disable"):
            _try(lambda: client.trigger_action(paths, "PATCH", f"/{trigger_id}", {"enabled": action == "enable"}))
        elif action == "remove":
            _try(lambda: client.trigger_action(paths, "DELETE", f"/{trigger_id}"))
        return RedirectResponse("/triggers", status_code=303)

    @router.post("/usage/findings/{finding_id}/{action}")
    def usage_finding_action(finding_id: str, action: str):
        """Dismiss a finding, or open a dismissed one again."""
        state = {"dismiss": "dismissed", "reopen": "open"}.get(action)
        if state is not None:
            _try(client.set_usage_finding_state, paths, finding_id, state)
        return RedirectResponse("/usage#findings", status_code=303)

    return router


def toolkit_view(sources: list[dict], status: dict) -> dict:
    """The Toolkit page's project view (REQ-000000537): per source, what is still available to install (installed
    items left out) and what the project's lock records, with the registered descriptions; recommended items only
    while some are not installed."""
    installed = {s["repo"]: s for s in status.get("installed") or []}
    known = {s["repo"]: {i["id"]: i for i in s.get("items") or []} for s in sources}
    available, have = [], []
    for source in sources:
        owned = {i["id"] for i in (installed.get(source["repo"]) or {}).get("items", [])}
        items = [i for i in source.get("items") or [] if i["id"] not in owned]
        available.append({"repo": source["repo"], "licence": source.get("licence"), "sha": source.get("sha") or "",
                          "items": items, "installed": len(owned), "code": sum(1 for i in items if i.get("runs_code"))})
    for repo, entry in sorted(installed.items()):
        items = [{**item, "name": item["id"].split(":", 1)[-1], "description": (known.get(repo, {}).get(item["id"]) or {}).get("description", "")}
                 for item in entry.get("items", [])]
        current = next((s.get("sha") for s in sources if s["repo"] == repo), None)
        have.append({"repo": repo, "licence": entry.get("licence"), "sha": entry.get("sha") or "", "items": items,
                     "behind": bool(current and entry.get("sha") and current != entry.get("sha")),
                     "missing": sum(1 for i in items if i.get("missing"))})
    available.sort(key=lambda g: (not g["items"], g["repo"].lower()))
    return {"available": available, "installed": have, "recommended": status.get("recommended") or [],
            "counts": {"available": sum(len(g["items"]) for g in available), "installed": sum(len(g["items"]) for g in have),
                       "sources": len(sources)}}
