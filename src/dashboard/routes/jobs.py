"""Dashboard routes: new jobs and the Jobs pages."""

from __future__ import annotations

import json

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from dashboard import client, job_catalog, live
from dashboard.app import (
    DOC_UPDATE_JOB, JOBS_PAGE_SIZE, JOB_ACTIVE_STATUSES, JOB_CLEANUP_AGES, JOB_STATUS_LABELS, JOB_STATUS_ORDER,
    PEEK_JOB_TYPES, WEEKDAYS, _back, _fire_and_forget, _job_dependencies, _job_subjects, _local_path, _try,
)


def build_router(ctx) -> APIRouter:
    router = APIRouter()
    paths, templates, _render = ctx.paths, ctx.templates, ctx.render

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

    def _open_findings(project: str) -> list[dict]:
        return [{"value": f["id"], "label": f"{f['detector']} {f['title']} (about ${f['saving']:.2f} a week)"}
                for f in client.usage_findings(paths, project=project)]

    def _blend_files(project: str) -> list[dict]:
        def walk(nodes):
            for node in nodes:
                if node["type"] == "dir":
                    yield from walk(node["children"])
                elif node["kind"] == "blend":
                    yield node

        try:
            tree = client.asset_tree(paths, project)["tree"]
        except client.Refused as exc:
            raise ValueError(exc.detail) from exc
        return [{"value": n["path"], "label": n["path"],
                 "detail": {"ready": "exported", "stale": "changed since the export", "none": "not exported"}[n["bundle"]]} for n in walk(tree)]

    CHOICE_LOADERS = {"suites": _discover_suites, "findings": _open_findings, "blend": _blend_files}

    @router.get("/jobs/catalog/choices/{arg}")
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
            request, name, projects=_try(ctx.project_names, default=[]), catalog=job_catalog.CATALOG,
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

    @router.get("/jobs/new", response_class=HTMLResponse)
    def job_new(request: Request):
        return _job_new_page(request)

    @router.post("/jobs/new", response_class=HTMLResponse)
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
        schedule, refusal = ctx.parse_schedule(
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

    @router.get("/jobs/schedule")
    def job_schedule_redirect():
        # Schedule job was merged into New job; an old bookmark lands on it.
        return RedirectResponse("/jobs/new", status_code=307)

    @router.get("/jobs/schedule/{task_id}", response_class=HTMLResponse)
    def job_schedule_detail(request: Request, task_id: str):
        return ctx.schedule_detail(request, task_id, is_task=False)

    @router.post("/jobs/schedule/{task_id}/remove")
    def job_schedule_remove(task_id: str):
        try:
            client.remove_scheduled_task(paths, task_id)
        except client.PillarUnavailable as exc:
            return _back("/jobs/new", f"Could not remove it: {exc}")
        return _back("/jobs/new", "Scheduled job removed.")

    @router.get("/jobs", response_class=HTMLResponse)
    def jobs(request: Request, status: str | None = None, q: str | None = None, limit: int = JOBS_PAGE_SIZE):
        """One page of Jobs, newest first, as summaries: Orchestration counts, searches and pages them in its
        store, so the page reads the Jobs it shows and the counts of the rest."""
        limit = max(JOBS_PAGE_SIZE, min(limit, 1000))
        q = (q or "").strip()
        try:
            counted = client.job_counts(paths)
            matched = client.job_counts(paths, q=q) if q else counted
            known = not status or status in JOB_STATUS_ORDER
            shown = client.list_jobs(paths, status=status, q=q, newest_first=True, limit=limit, summary=True) if known else []
            error = None
        except client.PillarUnavailable as exc:
            counted, matched, shown, error = {}, {}, [], str(exc)
        counts = {name: 0 for name in JOB_STATUS_ORDER}
        counts.update(counted)
        all_count = sum(counted.values())
        total = matched.get(status, 0) if status else sum(matched.values())
        prunable = _try(lambda: client.job_cleanup_preview(paths, [hours for hours, _ in JOB_CLEANUP_AGES]), default={}) if all_count else {}
        cleanup_options = [{"hours": hours, "label": label, "count": prunable.get(str(hours), 0)} for hours, label in JOB_CLEANUP_AGES]
        job_steps = _try(lambda: client.jobs_steps(paths, [j["id"] for j in shown]), default={}) if shown else {}
        dependencies = _job_dependencies(paths, shown)
        policies = {p["id"]: p for p in _try(lambda: client.policy_list(paths, summary=True), default=[])} if any((j.get("type") or "").startswith("policy.") for j in shown) else {}
        subjects = {j["id"]: _job_subjects(j, job_steps.get(j["id"], []), policies) for j in shown}
        task_runs = _task_runs_of(shown)
        return _render(
            request, "jobs.html", job_steps_of=job_steps, dependencies=dependencies, subjects=subjects, task_runs=task_runs, cleanup_options=cleanup_options, jobs=shown, total=total, limit=limit, next_limit=limit + JOBS_PAGE_SIZE,
            status=status, q=q, all_count=all_count,
            counts=[(name, JOB_STATUS_LABELS.get(name, name), counts[name]) for name in [*JOB_STATUS_ORDER, *[k for k in counts if k not in JOB_STATUS_ORDER]] if counts[name]],
            active=sum(counts.get(name, 0) for name in JOB_ACTIVE_STATUSES), error=error, nav_active="jobs", live_topics="jobs")

    def _task_runs_of(jobs: list[dict]) -> dict[str, dict]:
        """{job id: the task (agent run) it started} for the `agent.run_task` Jobs among `jobs`."""
        found: dict[str, dict] = {}
        for project in {j.get("project_id") for j in jobs if j.get("type") == "agent.run_task"} - {None}:
            for run in _try(client.agent_runs, paths, project, default=[]):
                if run.get("job_id"):
                    found[run["job_id"]] = run
        return found

    ctx.task_runs_of = _task_runs_of

    @router.get("/jobs/{job_id}.json")
    def job_json(job_id: str):
        """The whole Job, params and result uncut: where a page that shows only part of them links."""
        try:
            job = client.get_job(paths, job_id)
        except client.PillarUnavailable as exc:
            return JSONResponse({"detail": str(exc)}, status_code=503)
        if job is None:
            return JSONResponse({"detail": f"no job {job_id}"}, status_code=404)
        return job

    @router.get("/jobs/{job_id}", response_class=HTMLResponse)
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
        dependencies = _job_dependencies(paths, [job]) if job else {}
        policies = {p["id"]: p for p in _try(lambda: client.policy_list(paths, summary=True), default=[])} if job and (job.get("type") or "").startswith("policy.") else {}
        task_run = _try(client.agent_run_for_job, paths, job_id) if job and job.get("type") == "agent.run_task" else None
        claude_peek = _peek_of(job_id) if job and job.get("type") in PEEK_JOB_TYPES else None
        remote_control = _remote_control_of(job_id, job.get("project_id") or "") if job and job.get("type") in PEEK_JOB_TYPES else None
        topics = [f"job:{job_id}", *([f"peek:{job_id}"] if claude_peek and claude_peek.get("running") else [])]
        return _render(
            request, "job_detail.html", job=job, error=error, subjects=_job_subjects(job, job.get("steps") or [], policies) if job else [], dependencies=dependencies.get(job["id"], []) if job else [],
            active=bool(job) and job["status"] in JOB_ACTIVE_STATUSES, task_run=task_run, peek=claude_peek, remote_control=remote_control,
            stop_refused=request.query_params.get("refused") == "1", nav_active="jobs", live_topics=",".join(topics))

    def _peek_of(job_id: str) -> dict | None:
        """What the job's Claude has done so far, for the page's peek card (its newest events). None when
        there is nothing to show: no session, or a finished one whose transcript is gone (older runs)."""
        peek = _try(client.agent_peek, paths, job_id)
        if not peek or not (peek.get("available") or peek.get("running")):
            return None
        return {**peek, "job_id": job_id}

    ctx.peek_of = _peek_of

    def _remote_control_of(job_id: str, project_id: str) -> dict | None:
        """Remote Control of the job's Claude session, for its page (the button, or the status). None when the
        job has no Claude session or there is nothing to show: not running and never activated."""
        state = _try(lambda: client.agent_remote_control(paths, job_id, project_id=project_id))
        if not state or not (state["running"] or state["session_name"]):
            return None
        return {**state, "job_id": job_id, "project_id": project_id}

    ctx.remote_control_of = _remote_control_of

    @router.post("/jobs/{job_id}/record-conflict/{index}")
    def job_record_conflict(job_id: str, index: int):
        """Record one pair a semantic conflict check suspected as a conflict (REQ-000000523)."""
        back = f"/jobs/{job_id}"
        job = _try(client.get_job, paths, job_id)
        suspected = ((job or {}).get("result") or {}).get("suspected") or []
        if not job or job.get("type") != "requirements.semantic_conflicts" or not 0 <= index < len(suspected):
            return _back(back, "That suspected conflict is not on this job.")
        result = _try(lambda: client.create_job(
            paths, type="requirements.record_semantic_conflict", target_pillar="requirements", project_id=job.get("project_id"),
            params=suspected[index], queue_key=None, depends_on=[], created_by="dashboard",
        ))
        if result is None or result.get("status") != "succeeded":
            return _back(back, f"Could not record it: {(result or {}).get('error', 'orchestration unreachable')}")
        pair = f"{suspected[index]['older_key']} / {suspected[index]['newer_key']}"
        return _back(f"/requirements/conflicts/{result['result']['id']}", f"Recorded {pair} as a conflict.")

    @router.post("/jobs/{job_id}/remote-control/{action}")
    def job_remote_control(job_id: str, action: str, project_id: str = Form(""), next: str = Form("")):
        """Activate remote control on the job's running Claude session (it is resumed with it, never
        interrupted), or deactivate it (only the connection ends). Any user may."""
        to = _local_path(next, f"/jobs/{job_id}")
        if action not in ("activate", "deactivate"):
            return _back(to, "Unknown remote-control action.")
        try:
            result = client.agent_remote_control_act(paths, job_id, action, project_id=project_id)
        except client.RemoteControlRefused as exc:
            live.publish(f"job:{job_id}")
            return _back(to, f"Could not {action} remote control: {exc}")
        except client.PillarUnavailable as exc:
            return _back(to, f"Could not {action} remote control: {exc}")
        live.publish(f"job:{job_id}")
        live.publish("tasks")
        if action == "activate":
            return _back(to, f"Remote control is active: session {result.get('session_name')}.")
        return _back(to, "Remote control deactivated; the job keeps running.")

    @router.get("/jobs/{job_id}/peek.json")
    def job_peek(job_id: str, after: int = 0):
        """The events of the job's Claude after byte `after`: a page that missed some catches up here."""
        try:
            peek = client.agent_peek(paths, job_id, after=after)
        except client.PillarUnavailable as exc:
            return JSONResponse({"detail": str(exc)}, status_code=503)
        if peek is None:
            return JSONResponse({"detail": "no Claude session of this job"}, status_code=404)
        return peek

    @router.post("/jobs/{job_id}/steps/{key}/answer")
    def job_step_answer(job_id: str, key: str, choice: str = Form("")):
        """The user's answer to an action waiting for them; the job carries on with it."""
        to = f"/jobs/{job_id}"
        try:
            client.answer_step(paths, job_id, key, choice)
        except client.PillarUnavailable as exc:
            return _back(to, f"Could not send the answer: {exc}")
        return _back(to, "Answer sent: the job carries on.")

    @router.post("/jobs/{job_id}/stop")
    def job_stop(job_id: str, force: str = Form(""), next: str = Form("")):
        """Stop a running Job, or cancel one still queued, from its own page (or a task's); a refusal is shown
        with its reason and the offer to force it. Forcing settles the Job as stopped whatever its pillar says:
        the user chose it after the page warned what it means."""
        to = _local_path(next, f"/jobs/{job_id}")
        try:
            result = client.stop_job(paths, job_id, force=force == "1")
        except client.PillarUnavailable as exc:
            return _back(to, f"Could not stop the job: {exc}")
        if not result.get("stopped"):
            refused = to + ("&" if "?" in to else "?") + "refused=1"
            return _back(refused, f"Could not stop the job: {result.get('reason') or 'it is no longer running'}")
        live.publish(f"job:{job_id}")
        return _back(to, "Job force-stopped: it no longer holds its queue." if result.get("forced") else "Job stopped.")

    @router.post("/jobs/cleanup")
    def jobs_cleanup(hours: int | None = Form(None)):
        """Delete finished jobs older than one of the offered ages. Anything else is refused."""
        labels = dict(JOB_CLEANUP_AGES)
        if hours not in labels:
            return _back("/jobs", "Choose how old the jobs must be before deleting them.")
        ctx.update_statistics()  # count the jobs about to go, so the Statistics page keeps them
        try:
            result = client.job_cleanup(paths, hours)
        except client.PillarUnavailable as exc:
            return _back("/jobs", f"Could not delete jobs: {exc}")
        deleted = result.get("deleted", 0)
        kept = result.get("kept_for_safety", 0)
        note = f" {kept} kept because active work still depends on them." if kept else ""
        return _back("/jobs", f"Deleted {deleted} job{'' if deleted == 1 else 's'} older than {labels[hours]}.{note}")

    return router
