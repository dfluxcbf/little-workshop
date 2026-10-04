"""Usage & Efficiency's Pillar Interface (docs/pillars/usage.md).

Three kinds of route on the one Unix socket: the **intake** Claude Code posts to through the loopback
proxy (`/v1/logs`, `/v1/metrics`, `/v1/hooks/<event>`, `/v1/statusline`), which always answers at once
and never fails a session; the **reads** the Dashboard, the MCP server and `lws` proxy; and the
**Jobs** (`POST /v1/jobs/usage.<type>`), all of them code, none of them Claude.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import httpx
from fastapi import FastAPI, Request
from pydantic import BaseModel

from common import events
from common.incident import install_incident_reporting
from common.paths import Paths
from common.pillar_socket import resolve_pillar_socket
from common.shell import CommandRunner, DEFAULT_RUNNER
from common.uds_client import client_for_socket
from usage import (attribution, counterfactual, db, detectors, doctor, intake, mining, optimize, outcomes, pricing, profile, proxy,
                   rollup, transcripts)

TICK_SECONDS = 600.0
# A summary older than this is rolled up before it is read, so a session shows up within a minute
# instead of at the next ten-minute tick.
SUMMARY_ROLLUP_SECONDS = 60.0
ANALYSIS_DAYS = 14  # the period the detectors look at
EXPERIMENT_DAYS = 7  # an experiment compares this many days before a change with as many after
EXPERIMENT_SETTLE_DAYS = 14  # measured on its own this long after it began: a week to land, a week to measure
PROJECTS_CACHE_SECONDS = 60.0
AGENT_CURSOR = "agent_usage_since"
METHOD_KEY = "estimate_method"


class JobRequest(BaseModel):
    job_id: str
    project_id: str | None = None
    params: dict[str, Any] = {}


def fetch_projects(paths: Paths) -> list[dict] | None:
    """The registry's projects ({name, path, repo_url}), or None when Registry cannot be reached."""
    socket_path = resolve_pillar_socket(paths, "registry")
    if socket_path is None:
        return None
    try:
        with client_for_socket(socket_path, timeout=5.0) as client:
            response = client.get("/v1/projects")
        response.raise_for_status()
        return [p for p in response.json() if isinstance(p, dict) and p.get("name") and p.get("path")]
    except (httpx.HTTPError, OSError, ValueError):
        return None


def fetch_agent_usage(paths: Paths, since: str | None) -> list[dict] | None:
    """The run results the agent pillar stored after `since` (S1), or None when it cannot be reached."""
    socket_path = resolve_pillar_socket(paths, "agent")
    if socket_path is None:
        return None
    try:
        with client_for_socket(socket_path, timeout=10.0) as client:
            response = client.get("/v1/usage", params={"since": since} if since else {})
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPError, OSError, ValueError):
        return None


def _pillar_get(paths: Paths, pillar: str, path: str, params: dict | None = None, timeout: float = 15.0):
    socket_path = resolve_pillar_socket(paths, pillar)
    if socket_path is None:
        return None
    try:
        with client_for_socket(socket_path, timeout=timeout) as client:
            response = client.get(path, params=params or {})
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPError, OSError, ValueError):
        return None


def fetch_outcomes(paths: Paths, projects: list[str]) -> dict:
    """What the outcomes need from the other pillars: the succeeded Jobs Orchestration holds, and each
    project's requirements report. A pillar that cannot be reached contributes nothing this round."""
    return {"jobs": _pillar_get(paths, "orchestration", "/v1/jobs", {"status": "succeeded"}, timeout=30.0),
            "reports": {name: _pillar_get(paths, "requirements", "/v1/report", {"project_id": name}) for name in projects}}


def create_orchestration_job(paths: Paths, template: dict) -> dict:
    """Create a Job in Orchestration; raises RuntimeError when it cannot be reached."""
    socket_path = resolve_pillar_socket(paths, "orchestration")
    if socket_path is None:
        raise RuntimeError("Orchestration is unreachable")
    try:
        with client_for_socket(socket_path, timeout=30.0) as client:
            response = client.post("/v1/jobs", json=template)
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPError, OSError, ValueError) as exc:
        raise RuntimeError(f"Orchestration did not take the job: {exc}") from exc


def build_app(
    conn: sqlite3.Connection, paths: Paths, *, runner: CommandRunner = DEFAULT_RUNNER,
    create_job: Callable[[dict], dict] | None = None, emit: Callable[[str, str | None, dict], object] | None = None,
    outcome_sources: Callable[[list[str]], dict] | None = None,
    projects: Callable[[], list[dict] | None] | None = None, agent_usage: Callable[[str | None], list[dict] | None] | None = None,
    transcripts_root: Path | None = None, settings_path: Path | None = None,
) -> FastAPI:
    app = FastAPI(title="little-workshop Usage & Efficiency")
    install_incident_reporting(app, paths, pillar="usage")
    lock = threading.RLock()
    projects = projects or (lambda: fetch_projects(paths))
    agent_usage = agent_usage or (lambda since: fetch_agent_usage(paths, since))
    create_job = create_job or (lambda template: create_orchestration_job(paths, template))
    emit = emit or (lambda name, project, data: events.emit_in_background(paths, name, project, data))
    outcome_sources = outcome_sources or (lambda names: fetch_outcomes(paths, names))
    cache: dict = {"projects": [], "at": 0.0}

    def known_projects() -> list[dict]:
        if time.monotonic() - cache["at"] > PROJECTS_CACHE_SECONDS or not cache["projects"]:
            fetched = projects()
            if fetched is not None:  # an unreachable Registry keeps the last list it gave
                cache["projects"] = fetched
            cache["at"] = time.monotonic()
        return cache["projects"]

    def body_reason(parsed: dict) -> str | None:
        return parsed["session"].get("end_reason")

    def prices() -> dict:
        return pricing.load_prices(paths.pillar_config_dir(proxy.PILLAR))

    def statusline_path() -> Path:
        return paths.pillar_config_dir(proxy.PILLAR) / profile.STATUSLINE_FILE

    def attribute(session_ids) -> None:
        """Call with `lock` held."""
        registry = known_projects()
        for session_id in set(session_ids):
            session = db.get_session(conn, session_id)
            if session is not None:
                db.set_attribution(conn, session_id, attribution.project_of(session, registry), attribution.origin_of(session))

    def store(parsed: dict) -> dict:
        new_requests = new_calls = 0
        with lock:
            for session in parsed.get("sessions") or []:
                fields = {key: value for key, value in session.items() if key not in ("id", "seen", "first")}
                db.upsert_session(conn, session["id"], seen=session.get("seen"), **fields)
                if session.get("first"):
                    db.upsert_session(conn, session["id"], seen=session["first"])
            for request in parsed.get("requests") or []:
                new_requests += db.upsert_request(conn, request)
            for call in parsed.get("tool_calls") or []:
                new_calls += db.upsert_tool_call(conn, call)
            for event in parsed.get("events") or []:
                db.add_session_event(conn, event["session_id"], event["ts"], event["name"], event["data"])
            attribute(session["id"] for session in parsed.get("sessions") or [])
            conn.commit()
        return {"requests": new_requests, "tool_calls": new_calls}

    @app.get("/v1/health")
    def health() -> dict:
        with lock:
            count = conn.execute("SELECT COUNT(*) FROM requests").fetchone()[0]
        return {"ok": True, "request_count": count}

    # -- intake ------------------------------------------------------------------

    async def _json(request: Request) -> dict:
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001 - whatever Claude Code sent, its session must not see an error
            return {}
        return body if isinstance(body, dict) else {}

    @app.post("/v1/logs")
    async def otlp_logs(request: Request) -> dict:
        store(intake.parse_logs(await _json(request)))
        return {}

    @app.post("/v1/metrics")
    async def otlp_metrics(request: Request) -> dict:
        # Accepted so the exporter does not retry; the per-request log events carry everything stored.
        await request.body()
        return {}

    @app.post("/v1/hooks/{event}")
    async def hook(event: str, request: Request) -> dict:
        parsed = intake.parse_hook(event, await _json(request), now=db.now_iso())
        if parsed is None:
            return {}
        store({"sessions": [parsed["session"]]})
        with lock:
            db.add_session_event(conn, parsed["event"]["session_id"], parsed["event"]["ts"], event, parsed["event"]["data"])
            conn.commit()
        path = parsed["session"].get("transcript_path")
        if event == "SessionEnd":
            if path:
                threading.Thread(target=ingest_session, args=(Path(path),), daemon=True).start()
            with lock:
                session = db.get_session(conn, parsed["session"]["id"]) or {}
            emit("claude.session_ended", session.get("project"), {"session_id": parsed["session"]["id"], "reason": body_reason(parsed)})
        return {}

    @app.post("/v1/statusline")
    async def statusline(request: Request) -> dict:
        parsed = intake.parse_statusline(await _json(request), now=db.now_iso())
        if parsed is None:
            return {}
        with lock:
            db.add_plan_pressure(conn, parsed["ts"], parsed["session_id"], parsed["five_hour"], parsed["seven_day"])
            conn.commit()
        if parsed.get("session_id"):
            store({"sessions": [{"id": parsed["session_id"], "seen": parsed["ts"], "cwd": parsed.get("cwd"),
                                 "transcript_path": parsed.get("transcript_path")}]})
        return {}

    # -- work the Jobs and the tick share ------------------------------------------

    def ingest_file(path: Path) -> dict:
        try:
            stat = path.stat()
        except OSError:
            return {"requests": 0, "tool_calls": 0, "skipped": 0, "read": False}
        mark = f"{stat.st_mtime_ns}:{stat.st_size}:p{transcripts.PARSER_VERSION}"
        key = f"transcript:{path}"
        with lock:
            if db.get_meta(conn, key) == mark:
                return {"requests": 0, "tool_calls": 0, "skipped": 0, "read": False}
        parsed = transcripts.parse_transcript(path)
        added = store({"sessions": [parsed["session"]] if parsed["session"] else [], "requests": parsed["requests"],
                       "tool_calls": parsed["tool_calls"]})
        with lock:
            db.set_meta(conn, key, mark)
            conn.commit()
        return {**added, "skipped": parsed["skipped"], "read": True}

    def ingest_session(path: Path) -> None:
        """A session that ended: its transcript, then its subagents' (the subagents' requests need the session)."""
        ingest_file(path)
        for subagent in transcripts.subagent_files(path):
            ingest_file(subagent)

    def ingest_transcripts() -> dict:
        root = transcripts_root or transcripts.DEFAULT_ROOT
        totals = {"files": 0, "files_read": 0, "requests": 0, "tool_calls": 0, "skipped_lines": 0}
        for path in transcripts.transcript_files(root):
            result = ingest_file(path)
            totals["files"] += 1
            totals["files_read"] += int(result["read"])
            totals["requests"] += result["requests"]
            totals["tool_calls"] += result["tool_calls"]
            totals["skipped_lines"] += result["skipped"]
        return totals

    def pull_agent_usage() -> int:
        with lock:
            since = db.get_meta(conn, AGENT_CURSOR)
        rows = agent_usage(since)
        if not rows:
            return 0
        added = 0
        with lock:
            for row in rows:
                added += db.add_run_usage(conn, row)
            db.set_meta(conn, AGENT_CURSOR, max(row.get("created_at") or "" for row in rows) or (since or ""))
            conn.commit()
        return added

    last_rollup = {"at": None}

    def do_rollup() -> dict:
        last_rollup["at"] = time.monotonic()
        with lock:
            attribute(row["id"] for row in conn.execute("SELECT id FROM sessions").fetchall())
            days = rollup.rebuild(conn, prices(), polls=detectors.poll_request_ids(conn))
            cutoff = (date.today() - timedelta(days=proxy.load_config(paths)["retention_days"])).isoformat()
            pruned = db.prune_detail(conn, cutoff)
        return {"days": days, "pruned": pruned}

    def note_outcomes() -> int:
        names = [project["name"] for project in known_projects()]
        sources = outcome_sources(names)
        today = date.today().isoformat()
        with lock:
            added = outcomes.note_finished_branches(conn, sources.get("jobs"))
            for name, report in (sources.get("reports") or {}).items():
                outcomes.note_requirements(conn, name, report, today)
            conn.commit()
        return added

    def tick() -> dict:
        return {"run_usage": pull_agent_usage(), **do_rollup(), "branches_noted": note_outcomes()}

    app.state.tick = tick

    # -- findings and experiments -----------------------------------------------------

    def metric_value(detector: str, project: str, end: date, days: int) -> float | None:
        """What `detector` measures for `project` over the `days` ending on `end`; call with `lock` held."""
        period = detectors.Period(end - timedelta(days=days - 1), end)
        found = [m for m in detectors.measure(conn, period, prices(), proxy.load_config(paths), only=(detector,)) if m.project == project]
        return found[0].value if found else None

    def measure_experiment(experiment: dict, today: date) -> dict:
        after = metric_value(experiment["detector"], experiment["project"], today, EXPERIMENT_DAYS)
        after = 0.0 if after is None else after
        before = experiment["before_value"]
        improved = before is None or after < before
        db.finish_experiment(conn, experiment["id"], after, improved)
        if experiment.get("finding_id"):
            # A change that did not move its metric reopens the finding; one that did resolves it.
            db.set_finding_state(conn, experiment["finding_id"], db.FINDING_RESOLVED if improved else db.FINDING_OPEN)
        return {"experiment_id": experiment["id"], "before": before, "after": after, "improved": improved}

    def analyze(today: date | None = None) -> dict:
        today = today or date.today()
        period = detectors.Period(today - timedelta(days=ANALYSIS_DAYS - 1), today)
        with lock:
            measurements = detectors.measure(conn, period, prices(), proxy.load_config(paths))
            flagged = [m for m in measurements if m.flagged]
            known = {(f["detector"], f["project"]) for f in db.list_findings(conn, state=None) if f["state"] != db.FINDING_RESOLVED}
            for measurement in flagged:
                finding_id = db.record_finding(conn, measurement.as_dict(), *period.bounds)
                if (measurement.detector, measurement.project) not in known:
                    emit("usage.finding_opened", None if measurement.project == "all" else measurement.project,
                         {"finding_id": finding_id, "detector": measurement.detector})
            resolved = db.resolve_missing_findings(
                conn, {(m.detector, m.project) for m in flagged}, {(m.detector, m.project): m.value for m in measurements})
            due = [e for e in db.list_experiments(conn, state=db.EXPERIMENT_RUNNING)
                   if date.fromisoformat(e["created_at"][:10]) <= today - timedelta(days=EXPERIMENT_SETTLE_DAYS)]
            measured = [measure_experiment(experiment, today) for experiment in due]
            conn.commit()
        return {"period": list(period.bounds), "measurements": len(measurements), "flagged": len(flagged), "resolved": resolved,
                "experiments_measured": measured}

    app.state.analyze = analyze

    def mine_automation(today: date | None = None) -> dict:
        with lock:
            mining.resign(conn)
            replay = counterfactual.Replay(conn, prices())
            calibration = counterfactual.calibrate(conn, replay)
            db.set_meta(conn, METHOD_KEY, json.dumps(counterfactual.method_report(conn, replay, calibration)))
            found = mining.mine(conn, prices(), today=today, replay=replay, calibration=calibration)
            for candidate in found:
                db.record_candidate(conn, candidate)
            db.drop_stale_candidates(conn, {candidate["signature"] for candidate in found})
            for candidate in db.list_candidates(conn, state=None):
                if candidate.get("replacement"):
                    db.update_candidate(conn, candidate["id"], tokens_avoided=mining.tokens_avoided(conn, candidate))
            conn.commit()
        return {"candidates": len(found), "calibration": calibration.as_dict(), "by_class": {kind: sum(1 for c in found if c["class"] == kind)
                                                       for kind in (mining.CLASS_DETERMINISTIC, mining.CLASS_HYBRID, mining.CLASS_HEURISTIC)}}

    app.state.mine_automation = mine_automation

    # -- Jobs ----------------------------------------------------------------------

    @app.post("/v1/jobs/usage.ingest_transcripts")
    def job_ingest_transcripts(request: JobRequest) -> dict:
        result = ingest_transcripts()
        return {"outcome": "succeeded", "result": {**result, **do_rollup()}}

    @app.post("/v1/jobs/usage.rollup")
    def job_rollup(request: JobRequest) -> dict:
        return {"outcome": "succeeded", "result": tick()}

    @app.post("/v1/jobs/usage.doctor")
    def job_doctor(request: JobRequest) -> dict:
        with lock:
            report = doctor.run(conn, paths, runner=runner, settings_path=settings_path)
        return {"outcome": "succeeded", "result": report}

    @app.post("/v1/jobs/usage.configure_claude")
    def job_configure_claude(request: JobRequest) -> dict:
        """Dry run unless `params.apply` is true: the profile edits a file outside every repository."""
        target = settings_path or profile.default_settings_path()
        try:
            port = proxy.intake_port(paths)
            if request.params.get("apply") is True:
                return {"outcome": "succeeded", "result": profile.apply(target, port, statusline_path())}
            return {"outcome": "succeeded", "result": {**profile.plan(target, port, statusline_path()), "applied": False}}
        except (OSError, ValueError, RuntimeError) as exc:
            return {"outcome": "failed", "error": str(exc)}

    @app.post("/v1/jobs/usage.analyze")
    def job_analyze(request: JobRequest) -> dict:
        tick()
        return {"outcome": "succeeded", "result": analyze()}

    @app.post("/v1/jobs/usage.measure_experiment")
    def job_measure_experiment(request: JobRequest) -> dict:
        """Measure one running experiment now (`params.experiment_id`), whatever its age."""
        wanted = request.params.get("experiment_id")
        with lock:
            running = [e for e in db.list_experiments(conn, state=db.EXPERIMENT_RUNNING) if e["id"] == wanted]
            if not running:
                return {"outcome": "failed", "error": f"no running experiment {wanted!r}"}
            result = measure_experiment(running[0], date.today())
            conn.commit()
        return {"outcome": "succeeded", "result": result}

    @app.post("/v1/jobs/usage.optimize")
    def job_optimize(request: JobRequest) -> dict:
        """Start the fixed "Optimize Claude usage" task on a project for the chosen open findings."""
        project, chosen = request.project_id, request.params.get("finding_ids") or []
        if not project or not chosen:
            return {"outcome": "failed", "error": "missing project_id or params.finding_ids"}
        today = date.today()
        with lock:
            findings = [f for f in (db.get_finding(conn, finding_id) for finding_id in chosen) if f]
            findings = [f for f in findings if f["project"] in (project, "all") and f["state"] in (db.FINDING_OPEN, db.FINDING_ACCEPTED)]
        if not findings:
            return {"outcome": "failed", "error": "none of the chosen findings is open for this project"}
        template = {**optimize.task_job(project, findings), "created_by": "usage", "depends_on": []}
        try:
            job = create_job(template)
        except RuntimeError as exc:
            return {"outcome": "failed", "error": str(exc)}
        with lock:
            for finding in findings:
                db.set_finding_state(conn, finding["id"], db.FINDING_ACCEPTED)
                db.add_experiment(
                    conn, project=finding["project"], metric=finding["metric"], detector=finding["detector"], finding_id=finding["id"],
                    before_value=metric_value(finding["detector"], finding["project"], today, EXPERIMENT_DAYS),
                    change=f"job {job.get('id')}")
            conn.commit()
        return {"outcome": "succeeded", "result": {"task_job_id": job.get("id"), "findings": [f["id"] for f in findings]}}

    @app.post("/v1/jobs/usage.mine_automation")
    def job_mine_automation(request: JobRequest) -> dict:
        return {"outcome": "succeeded", "result": mine_automation()}

    @app.post("/v1/jobs/usage.automate")
    def job_automate(request: JobRequest) -> dict:
        """"Make it a task": start the fixed improvement task on little-workshop for one candidate."""
        with lock:
            candidate = db.get_candidate(conn, request.params.get("candidate_id") or "")
        if candidate is None or candidate["state"] not in (db.CANDIDATE_OPEN, db.CANDIDATE_ACCEPTED):
            return {"outcome": "failed", "error": "no open candidate with that id"}
        template = {**mining.automate_job(candidate, rollup.WORKSHOP_PROJECT), "created_by": "usage", "depends_on": []}
        try:
            job = create_job(template)
        except RuntimeError as exc:
            return {"outcome": "failed", "error": str(exc)}
        with lock:
            db.update_candidate(conn, candidate["id"], state=db.CANDIDATE_ACCEPTED)
            conn.commit()
        return {"outcome": "succeeded", "result": {"task_job_id": job.get("id"), "candidate_id": candidate["id"]}}

    @app.post("/v1/jobs/usage.purge_project")
    def job_purge_project(request: JobRequest) -> dict:
        if not request.project_id:
            return {"outcome": "failed", "error": "missing project_id"}
        with lock:
            removed = db.purge_project(conn, request.project_id)
        return {"outcome": "succeeded", "result": {"sessions_removed": removed}}

    # -- reads ---------------------------------------------------------------------

    @app.get("/v1/summary")
    def summary(week: str | None = None, project: str | None = None, origin: str | None = None) -> dict:
        if last_rollup["at"] is None or time.monotonic() - last_rollup["at"] >= SUMMARY_ROLLUP_SECONDS:
            do_rollup()
        with lock:
            result = rollup.summary(conn, prices(), week=week, project=project or None, origin=origin or None)
            rows = outcomes.weekly(conn, weeks=8, project=project or None, today=date.fromisoformat(result["week"]) + timedelta(days=6))
            result["outcomes"] = [row for row in rows if row["week"] == result["week"]]
            result["outcomes_by_week"] = rows
            return result

    @app.get("/v1/days")
    def days(start: str, end: str, project: str | None = None, origin: str | None = None) -> list[dict]:
        with lock:
            return rollup.days(conn, date.fromisoformat(start), date.fromisoformat(end), project=project or None, origin=origin or None)

    @app.get("/v1/findings")
    def findings(project: str | None = None, state: str | None = db.FINDING_OPEN) -> list[dict]:
        """Findings ranked by estimated saving; `state=all` lists every state. Each carries its experiments."""
        with lock:
            rows = db.list_findings(conn, project=project or None, state=None if state == "all" else state)
            experiments = db.list_experiments(conn)
        for row in rows:
            row["experiments"] = [e for e in experiments if e["finding_id"] == row["id"]]
            row.update(detectors.described(row["detector"]))
        return rows

    @app.post("/v1/findings/{finding_id}/state")
    def finding_state(finding_id: str, body: dict) -> dict:
        state = body.get("state")
        if state not in (db.FINDING_OPEN, db.FINDING_DISMISSED):
            return {"ok": False, "error": "a finding can be dismissed or reopened"}
        with lock:
            changed = db.set_finding_state(conn, finding_id, state)
            conn.commit()
        return {"ok": changed}

    @app.get("/v1/candidates")
    def candidates(state: str | None = db.CANDIDATE_OPEN, limit: int | None = None) -> list[dict]:
        """Automation candidates ranked by the weighted tokens they cost per week; `state=all` lists every
        state, `limit` keeps the first ones."""
        with lock:
            rows = db.list_candidates(conn, state=None if state == "all" else state)
        return rows[:max(limit, 0)] if limit is not None else rows

    @app.get("/v1/estimate_method")
    def estimate_method() -> dict:
        """What the candidates' estimates rest on: the constants fitted on the stored turns, their error on
        the newest turns they were not fitted on, and how long an addition to a conversation is re-read."""
        with lock:
            stored = db.get_meta(conn, METHOD_KEY)
        return {"method": json.loads(stored) if stored else None}

    @app.post("/v1/candidates/{candidate_id}")
    def edit_candidate(candidate_id: str, body: dict) -> dict:
        """Dismiss, reopen or mark done, and name the replacement (a signature prefix) whose runs count as tokens avoided."""
        fields = {key: body[key] for key in ("state", "replacement") if key in body}
        if fields.get("state") == db.CANDIDATE_ACCEPTED:
            return {"ok": False, "error": "a candidate is accepted by making it a task (usage.automate)"}
        with lock:
            try:
                changed = db.update_candidate(conn, candidate_id, **fields)
            except ValueError as exc:
                return {"ok": False, "error": str(exc)}
            conn.commit()
        return {"ok": changed}

    @app.get("/v1/call_outcomes")
    def call_outcomes(signature: str | None = None, limit: int = 50) -> list[dict]:
        """How the stored calls ended, per signature (`signature` matches its start): ok, failed, held,
        running, cancelled or unknown, and the test counts their output printed."""
        with lock:
            return db.call_outcomes(conn, signature=signature or None, limit=max(1, min(limit, 500)))

    @app.get("/v1/experiments")
    def experiments() -> list[dict]:
        with lock:
            return db.list_experiments(conn)

    @app.get("/v1/outcomes")
    def outcomes_by_week(weeks: int = 8, project: str | None = None) -> list[dict]:
        """Per week and project: weighted tokens beside finished agent tasks, finished branches and
        requirements turned to done, and the tokens per each."""
        with lock:
            return outcomes.weekly(conn, weeks=max(1, min(weeks, 52)), project=project or None)

    @app.get("/v1/doctor")
    def doctor_report() -> dict:
        with lock:
            return {"report": doctor.latest(conn)}

    @app.get("/v1/config")
    def config() -> dict:
        settings = proxy.load_config(paths)
        port = settings.get("intake_port")
        return {**settings, "endpoint": profile.endpoint(int(port)) if port else None,
                "launch_env": profile.telemetry_env(int(port)) if port else {}}

    @app.get("/v1/plan_pressure")
    def plan_pressure() -> dict:
        """The latest plan usage a statusline reported, and whether Claude-based scheduled jobs should wait."""
        with lock:
            latest = db.latest_plan_pressure(conn)
        threshold = proxy.load_config(paths)["plan_pressure_hold_percent"]
        five = latest["five_hour_pct"] if latest else None
        age = None
        if latest:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(latest["ts"])).total_seconds()
        # A reading older than the window it describes says nothing about now.
        stale = age is None or age > 5 * 3600
        return {"five_hour_percent": five, "seven_day_percent": latest["seven_day_pct"] if latest else None,
                "reported_at": latest["ts"] if latest else None, "threshold_percent": threshold,
                "hold": bool(not stale and five is not None and five >= threshold)}

    return app


def run_tick_forever(app: FastAPI, stop: threading.Event, interval: float = TICK_SECONDS) -> None:
    while not stop.wait(interval):
        try:
            app.state.tick()
        except Exception:  # noqa: BLE001 - a failed round must not end the loop; the next one retries
            pass
