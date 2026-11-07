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

import asyncio
import contextlib
import fnmatch
import importlib.resources
import json
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from common import serving
from common.job_steps import format_seconds
from common.paths import Paths
from common.incident import install_incident_reporting
from dashboard import client, deletions, live

GATE_POINT_VALUES = [
    "commit", "build_install", "server_launch", "deploy_redeploy", "self_redeploy", "server_stop",
    "release", "package_publish", "branch_finish", "project_removal", "project_registration",
]

# Documentation review's update job, and the Job statuses that count as "not finished yet".
DOC_UPDATE_JOB = "agent.update_docs"
ASSET_EXPORT_JOB = "agent.export_asset"
# A backlog entry's own record of Claude's work on it (Registry & Portfolio keeps it on the entry), by kind:
# (since, job, error) columns. Claude's answer comes within the 600s of an agent.query that reads the
# project's checkout (Orchestration gives up at 630s), so a mark older than BACKLOG_ACTIVITY_STALE
# belongs to a Job that never came back.
BACKLOG_ACTIVITY = {"refine": ("refining_since", "refining_job", "refine_error"),
                    "prompt": ("prompting_since", "prompting_job", "prompt_error")}
BACKLOG_ACTIVITY_STALE = timedelta(minutes=15)
_UNFINISHED = {"refine": "the last refinement did not finish", "prompt": "the last task prompt did not finish"}
STATISTICS_UPDATE_SECONDS = 600  # how often the running Dashboard counts new jobs, checks and incidents
LIVE_KEEPALIVE_SECONDS = 15.0  # a comment on an idle event stream, so proxies keep it open
LIVE_STOP_CHECK_SECONDS = 1.0  # how soon an open event stream notices the Dashboard is stopping
LIVE_MAX_TOPICS = 20
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
# Requirements & Quality's conflict kinds (requirements/conflicts.py) and the three ways to resolve one.
# A backlog entry's states and priorities (REQ-000000524), as Registry & Portfolio keeps them.
BACKLOG_STATUSES = ("open", "done", "dropped")
BACKLOG_PRIORITIES = ("high", "normal", "low")
CONFLICT_KINDS = {"duplicate_key": "Same key", "contradiction": "Contradiction", "different_value": "Different values",
                  "semantic": "In meaning (Claude)"}
CONFLICT_DECISIONS = {
    "older_wins": "Older requirement wins",
    "newer_wins": "Newer requirement wins",
    "other": "Another approach",
}

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


# The statuses of an `agent.run_task` Job that the Tasks page may show without a run (a succeeded one never is).
TASK_JOB_STATUSES_WITHOUT_RUN = ("queued", "running", "pending_confirmation", "failed", "cancelled", "stopped")


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


# The colour class of a job's state: the same words the pills use.
_JOB_STATE_CLASS = {
    "succeeded": "ok", "failed": "fail", "running": "run", "pending_confirmation": "warn",
    "queued": "idle", "cancelled": "idle", "stopped": "idle",
}


def _job_dependencies(paths: Paths, jobs: list[dict]) -> dict[str, list[dict]]:
    """What each job waits on, in words: {job id: [{label, state, state_class, href}]}. A dependency that is
    a task links to the task page, any other job to its job page; one that has been deleted from the history
    is shown as such, without a link. Only the Jobs waited on are read."""
    wanted = {d for j in jobs for d in j.get("depends_on") or []}
    by_id = {j["id"]: j for j in _try(lambda: client.list_jobs(paths, ids=sorted(wanted), summary=True), default=[])} if wanted else {}
    runs: dict[str, dict] = {}
    for project in {by_id[d].get("project_id") for d in wanted if d in by_id and by_id[d].get("type") == "agent.run_task"} - {None}:
        for run in _try(client.agent_runs, paths, project, default=[]):
            if run.get("job_id"):
                runs[run["job_id"]] = run
    out: dict[str, list[dict]] = {}
    for job in jobs:
        deps = []
        for dep_id in job.get("depends_on") or []:
            dep = by_id.get(dep_id)
            if dep is None:
                deps.append({"label": f"A job no longer in the history ({dep_id[:8]})", "state": "gone", "state_class": "gone", "href": None})
                continue
            run = runs.get(dep_id)
            what = f"Task: {run['title']}" if run else dep["type"]
            where = f" · {dep['project_id']}" if dep.get("project_id") else ""
            deps.append({
                "label": f"{what}{where}", "state": dep["status"].replace("_", " "),
                "state_class": _JOB_STATE_CLASS.get(dep["status"], "idle"),
                "href": f"/tasks/{dep['project_id']}/{run['id']}" if run else f"/jobs/{dep_id}",
            })
        out[job["id"]] = deps
    return out


_START_BRANCH_PREFIX = {"version.feature_start": "feature", "version.improvement_start": "improvement", "version.hotfix_start": "hotfix"}
_VERSION_STEP_KEYS = ("finalize_version", "set_version", "bump_version", "next_patch")


def _arrow(text: str) -> str:
    return text.replace("->", "→")


def _job_subjects(job: dict, steps: list[dict], policies: dict[str, dict]) -> list[dict]:
    """What a job is about, so its card says more than its type: the policy a policy job runs, and for a
    version job the branch and the version number (with the transition, when it changes).
    [{"kind": "policy", "topic", "text"} | {"kind": "branch" | "version", "text"}]."""
    params, result, kind = job.get("params") or {}, job.get("result") or {}, job.get("type") or ""
    details = {s["key"]: s.get("detail") for s in steps if s.get("state") == "succeeded" and s.get("detail")}
    subjects: list[dict] = []
    if kind.startswith("policy."):
        policy = policies.get(params.get("policy_id") or "")
        topic = (policy or {}).get("topic") or params.get("topic")
        text = (policy or {}).get("ears_text") or params.get("ears_text")
        if topic or text:
            subjects.append({"kind": "policy", "topic": topic or "", "text": text or ""})
    elif kind.startswith("version."):
        branch = result.get("branch") or result.get("merged_branch")
        if not branch and kind in _START_BRANCH_PREFIX and params.get("name"):
            branch = f"{_START_BRANCH_PREFIX[kind]}/{params['name']}"
        if not branch and result.get("hotfix"):
            branch = f"hotfix/{result['hotfix']}"
        if branch and kind.endswith("_finish") and details.get("check_branch", "").startswith(branch + " ->"):
            branch = details["check_branch"]
        elif not branch and details.get("check_branch"):
            branch = details["check_branch"]
        if branch:
            subjects.append({"kind": "branch", "text": _arrow(branch)})
        versions = [_arrow(details[k]) for k in _VERSION_STEP_KEYS if k in details and ("->" in details[k] or " at " in details[k])]
        if not versions:
            known = [result[k] for k in ("version", "new_version", "stable_version", "production_version", "next_version") if result.get(k)]
            versions = list(dict.fromkeys(known))
        subjects += [{"kind": "version", "text": v} for v in versions]
    return subjects


def backlog_activity(entry: dict, now: datetime | None = None) -> dict:
    """What Claude is doing for a backlog entry, read from the entry itself: {"refine": the Job id under way
    or None, "prompt": ..., "refine_error": why the last one failed or None, "prompt_error": ...}."""
    now = now or datetime.now(timezone.utc)
    activity: dict = {}
    for kind, (since_key, job_key, error_key) in BACKLOG_ACTIVITY.items():
        since = _parse_time(entry.get(since_key))
        if since is not None and since.tzinfo is None:
            since = since.replace(tzinfo=timezone.utc)
        fresh = since is not None and now - since < BACKLOG_ACTIVITY_STALE
        activity[kind] = entry.get(job_key) if fresh and entry.get(job_key) else None
        activity[f"{kind}_error"] = entry.get(error_key) or (_UNFINISHED[kind] if since is not None and not fresh else None)
    return activity


def demote_headings(html: str, by: int = 2) -> str:
    """Markdown shown inside a page's card: its `# title` must not be a second <h1> of the page."""
    return re.sub(r"<(/?)h([1-6])\b", lambda m: f"<{m.group(1)}h{min(int(m.group(2)) + by, 6)}", html)


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
    under_way = _try(lambda: client.list_jobs(paths, status=STARTING_JOB_STATUSES, types=list(STARTING_JOB_KINDS), summary=True), default=[])
    for job in under_way:
        kind = STARTING_JOB_KINDS.get(job.get("type"))
        if kind and job.get("project_id"):
            found.setdefault(job["project_id"], set()).add(kind)
    return found


def _local_path(target: str, default: str) -> str:
    return target if target.startswith("/") and not target.startswith("//") else default


def _back(to: str, message: str) -> RedirectResponse:
    from urllib.parse import quote

    return RedirectResponse(f"{to}{'&' if '?' in to else '?'}message={quote(message)}", status_code=303)


# The Job types whose Claude session the agent can show as it works (the peek): a task, an update of
# docs, and the Jobs that ask Claude for an answer (agent.query records the session against them).
PEEK_JOB_TYPES = ("agent.run_task", "agent.update_docs", "agent.query", "registry.backlog_refine", "registry.backlog_task_prompt",
                  "policy.author", "requirements.semantic_conflicts")


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
            # REQ-000000500: judged on its own tests, or on its suites' whole runs
            "basis": row.get("basis"), "own_tests": row.get("own_tests") or {},
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
    "usage": "Usage & Efficiency",
}


# An incident's kind, as its chip says it (Agentic Development's incidents).
INCIDENT_KINDS = {"exception": "exception", "crash": "crash", "unexpected": "unexpected behaviour"}


def pillar_name(identifier: str) -> str:
    """The pillar's full name, capitalised as in docs/architecture.md; an unknown identifier is shown as given."""
    return PILLAR_NAMES.get(identifier, identifier)


def _latest_policy_results(paths: Paths, *, failing: bool = False, policy_id: str | None = None) -> dict[tuple[str, str], dict]:
    """{(policy_id, project_id): latest recorded check result}, across every
    policy and project that has ever been checked (or of one policy; with
    `failing`, only those whose latest result failed). Shared by collect_attention
    and both policies pages so "what's currently failing" is computed the
    same way everywhere. Policy & Compliance picks the latest itself
    (REQ-000000390); the fold below is what keeps an older pillar, which
    answers with the whole history, giving the same answer."""
    results = _try(lambda: client.policy_check_results(paths, latest=True, outcome="fail" if failing else None, policy_id=policy_id),
                   default=[])
    latest: dict[tuple[str, str], dict] = {}
    for result in results:  # oldest first
        if policy_id is None or result["policy_id"] == policy_id:
            latest[(result["policy_id"], result["project_id"])] = result
    return {key: result for key, result in latest.items() if result["outcome"] == "fail"} if failing else latest


def _policy_associated_projects(policy: dict) -> set[str]:
    return {p["project_id"] for p in policy.get("projects", [])}


def _failing_checks(policies: list[dict], latest: dict[tuple[str, str], dict]) -> list[tuple[dict, str, dict]]:
    """(policy, project, latest result) for every enabled policy whose latest check on a project still
    associated with it failed: what "a policy is failing" means on the attention list and in the status."""
    by_id = {p["id"]: p for p in policies}
    failing = []
    for (policy_id, project), result in latest.items():
        policy = by_id.get(policy_id)
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
        failing.append((policy, project, result))
    return failing


POLICY_STATUS_FILTERS = (("", "Any status"), ("enabled", "Enabled"), ("disabled", "Disabled"), ("failing", "Failing"))


def glob_pattern(text: str) -> str | None:
    """The search text as a glob over a whole name or description: `*` is any text and `?` one
    character; without either, the text may appear anywhere. Matching ignores case."""
    text = text.strip().lower()
    if not text:
        return None
    text = text.replace("[", "[[]")  # brackets are plain characters here (static/policies' search does the same)
    return text if ("*" in text or "?" in text) else f"*{text}*"


def policy_matches(policy: dict, *, q: str = "", status: str = "", gates=(), projects=(), gating: bool = False) -> bool:
    """The Policies page's filters, combined: the text against the name and the description, the status,
    any of the chosen gate points, any of the chosen projects governed, and gating on in some project."""
    pattern = glob_pattern(q)
    if pattern and not (fnmatch.fnmatchcase((policy.get("topic") or "").lower(), pattern)
                        or fnmatch.fnmatchcase((policy.get("ears_text") or "").lower(), pattern)):
        return False
    enabled = policy.get("enabled", True)
    if (status == "enabled" and not enabled) or (status == "disabled" and enabled) or (status == "failing" and not policy.get("failing_count")):
        return False
    if gates and not set(gates) & set(policy.get("gate_points") or []):
        return False
    if projects and not set(projects) & _policy_associated_projects(policy):
        return False
    return not gating or any(p.get("gating_enabled") for p in policy.get("projects", []))


# The purge Job each pillar runs when a project is deleted (Registry's project.remove runs first); Orchestration's own
# records are purged last, directly. Requirements are never purged by it.
ORCHESTRATION_PURGE = "orchestration.purge"
DATA_PURGE_JOBS = (("usage.purge_project", "usage"), ("policy.purge_project", "policy"), ("agent.purge_project", "agent"),
                   ("deployment.purge_project", "deployment"), ("build.purge_project", "build"))


def registered_names(paths: Paths) -> set[str] | None:
    """The names of the registered projects, or None when the Registry does not answer (then nothing is quieted)."""
    try:
        return {project["name"] for project in client.list_projects(paths)}
    except client.PillarUnavailable:
        return None


def only_registered(rows: list[dict], names: set[str] | None, key: str = "project_id") -> list[dict]:
    """The rows about a registered project or about none: what concerns a project that is no longer registered
    raises no attention, status or incident (its requirements are kept apart, under System > Data management)."""
    if names is None:
        return rows
    return [row for row in rows if not row.get(key) or row[key] in names]


def collect_attention(paths: Paths) -> list[dict]:
    """What needs a human right now: an enabled policy whose latest check on a
    project still currently associated with it failed, every Job waiting
    for a confirmation, a project's open requirement conflicts and every open incident. Both are read live from their owning pillar; a pillar
    that is down contributes nothing."""
    items: list[dict] = []
    policies = _try(lambda: client.policy_list(paths, summary=True), default=[])
    for policy, project, result in _failing_checks(policies, _latest_policy_results(paths, failing=True)):
        items.append({
            "kind": "policy", "tag": "Policies", "project": project, "title": f"{policy['topic']} is failing",
            "detail": (result.get("detail") or "").strip()[:240], "href": f"/policies/{policy['id']}",
            "since": result.get("checked_at"),
        })
    for job in _try(lambda: client.list_jobs(paths, status="pending_confirmation", summary=True), default=[]):
        items.append({
            "kind": "confirmation", "tag": "Jobs", "job_id": job.get("id"), "project": job.get("project_id") or "", "title": f"{job['type']} awaits confirmation",
            "detail": "", "href": "/jobs?status=pending_confirmation", "since": job.get("created_at"),
        })
    for job in _try(lambda: client.list_jobs(paths, status="running", summary=True), default=[]):
        if (job.get("progress") or {}).get("attention"):
            items.append({
                "kind": "job_question", "tag": "Jobs", "job_id": job.get("id"), "project": job.get("project_id") or "",
                "title": f"{job['type']} needs your answer", "detail": (job["progress"].get("current") or "")[:240],
                "href": f"/jobs/{job['id']}", "since": job.get("updated_at"),
            })
    open_conflicts: dict[str, int] = {}
    for conflict in _try(lambda: client.requirement_conflicts(paths, status="open"), default=[]):
        open_conflicts[conflict["project_id"]] = open_conflicts.get(conflict["project_id"], 0) + 1
    for project, count in sorted(open_conflicts.items()):  # one item per project: a duplicated key alone can make many
        items.append({
            "kind": "requirement_conflict", "tag": "Requirements", "project": project,
            "title": f"{count} requirement conflict{'' if count == 1 else 's'} to resolve",
            "detail": "", "href": f"/requirements/conflicts?project={project}", "since": None,
        })
    for incident in _try(lambda: client.list_incidents(paths, status="open"), default=[]):
        items.append({
            "kind": "incident", "tag": "Incidents", "project": incident["project_id"],
            "title": f"{incident['exception_type']} in {incident['pillar']}",
            "detail": incident.get("message", "")[:240], "href": f"/incidents/{incident['id']}",
            "since": incident.get("created_at"),
        })
    items = only_registered(items, registered_names(paths), key="project")
    now = datetime.now(timezone.utc)
    for deletion in deletions.load(paths).values():
        if deletions.stuck(deletion, now):  # no `project`: the project is unregistered, which would hide it
            owing = ", ".join(step["pillar"] for step in deletion["pending"])
            recent = "; ".join(f"{e['pillar']}: {e['error']}" for e in deletion["errors"][-3:])
            items.append({
                "kind": "deletion", "tag": "Data", "title": f"Deleting {deletion['project']} has not finished",
                "detail": f"Still owing: {owing}. {recent}"[:240], "href": "/data#deletions", "since": deletion["started_at"],
            })
    return items


# -- The status Claude Code shows of a project and of the workshop (REQ-000000411) -------------------------

STATUS_FAILED_JOB_HOURS = 2  # a failed Job counts for this long after it failed
STATUS_INCIDENT_HOURS = 2  # an open incident counts for this long after it was recorded
STATUS_SCHEDULE_HOURS = 1  # a scheduled task counts once it is due within this long
DEAD_PILLAR_STATUSES = ("crashed", "faulted")
STATUS_ITEMS = 20  # items listed per topic; the counts stay exact up to STATUS_READ_LIMIT
STATUS_READ_LIMIT = 100


def _status_topic(items: list[dict] | None, **extra) -> dict:
    """One topic of the status: how many there are and the first of them; a `count` of None says its pillar
    did not answer, which a reader shows as unknown rather than as none."""
    if items is None:
        return {"count": None, "items": [], **extra}
    return {"count": len(items), "items": items[:STATUS_ITEMS], **extra}


def _status_job(job: dict, *, at: str) -> dict:
    title = (job.get("params") or {}).get("title") or job.get("type")
    return {"id": job["id"], "type": job.get("type"), "title": title, "at": job.get(at), "progress": job.get("progress"),
            "error": (job.get("error") or "").strip()[:240] or None, "href": f"/jobs/{job['id']}"}


def collect_status(paths: Paths, project_id: str | None, *, now: datetime | None = None) -> dict:
    """What Claude Code shows of the workshop: for the project a session works in, its server (None when it
    has none), its running Jobs, its tasks that need attention (the Tasks page's group), its Jobs failed in
    the last STATUS_FAILED_JOB_HOURS, its scheduled tasks due within STATUS_SCHEDULE_HOURS and its failing
    policies; across every project, the open incidents of the last STATUS_INCIDENT_HOURS and the pillars Core
    sees crashed or faulted. Each read is a filtered one on the owning pillar, all of them at once; a pillar
    that does not answer is listed in `unavailable` and its topics have a `count` of None. A project the
    Registry does not know (or none) gets only the workshop's topics."""
    now = now or datetime.now(timezone.utc)
    unavailable: set[str] = set()

    def read(pillar: str, fn, *args, **kwargs):
        try:
            return fn(paths, *args, **kwargs)
        except client.PillarUnavailable:
            unavailable.add(pillar)
            return None

    def project_reads() -> dict:
        names = read("registry", client.list_projects)
        if not project_id or names is None or project_id not in {p.get("name") for p in names}:
            return {}
        failed_since = now - timedelta(hours=STATUS_FAILED_JOB_HOURS)
        calls = {
            "server": ("deployment", client.deployment_status_optional, (project_id,), {}),
            "running": ("orchestration", client.list_jobs, (), {"status": "running", "project_id": project_id, "summary": True,
                                                                  "newest_first": True, "limit": STATUS_READ_LIMIT}),
            "runs": ("agent", client.agent_runs, (project_id,), {}),
            "task_jobs": ("orchestration", client.list_jobs, (), {"types": ["agent.run_task"], "status": TASK_JOB_STATUSES_WITHOUT_RUN,
                                                                    "project_id": project_id, "summary": True}),
            "failed": ("orchestration", client.list_jobs, (), {"status": "failed", "project_id": project_id, "updated_since": failed_since,
                                                                 "summary": True, "newest_first": True, "limit": STATUS_READ_LIMIT}),
            "scheduled": ("orchestration", client.scheduled_tasks, (), {}),
            "policies": ("policy", client.policy_list, (), {"summary": True}),
            "tests": ("orchestration", client.list_jobs, (), {"types": ["requirements.run_tests"], "project_id": project_id,
                                                                "summary": True, "newest_first": True, "limit": 1}),
        }
        with ThreadPoolExecutor(max_workers=len(calls)) as pool:
            futures = {key: pool.submit(read, pillar, fn, *args, **kwargs) for key, (pillar, fn, args, kwargs) in calls.items()}
            return {key: future.result() for key, future in futures.items()}

    with ThreadPoolExecutor(max_workers=3) as pool:
        project_future = pool.submit(project_reads)
        incidents_future = pool.submit(read, "agent", client.list_incidents, status="open")
        core_future = pool.submit(read, "core", client.core_status)
        reads, incidents, core = project_future.result(), incidents_future.result(), core_future.result()
        if incidents is not None:
            incidents = only_registered(incidents, registered_names(paths))

    status: dict = {"project": project_id if reads else None, "generated_at": now.isoformat()}
    if reads:
        server = reads["server"]
        status["server"] = None if server is None and "deployment" not in unavailable else {
            "known": server is not None, "running": bool(server and server.get("unit") == "active"),
            "listening": bool(server and server.get("listening")), "unit": (server or {}).get("unit")}
        status["running"] = _status_topic(None if reads["running"] is None else [_status_job(j, at="created_at") for j in reads["running"]])

        runs, task_jobs = reads["runs"], reads["task_jobs"]
        attention = None
        if runs is not None and task_jobs is not None:
            rows = runs + _task_rows_without_a_run(task_jobs, runs)
            attention = [{"id": r["id"], "title": r.get("title"), "status": r["status"], "at": r.get("updated_at") or r.get("created_at"),
                          "href": f"/jobs/{r['id']}" if r.get("job_only") else f"/tasks/{project_id}/{r['id']}"}
                         for r in sorted(rows, key=lambda r: r.get("updated_at") or r.get("created_at") or "", reverse=True)
                         if _task_group(r, now) == "attention"]
        status["tasks_attention"] = _status_topic(attention)

        failed = reads["failed"]
        status["failed_jobs"] = _status_topic(None if failed is None else [_status_job(j, at="updated_at") for j in failed],
                                              hours=STATUS_FAILED_JOB_HOURS)

        scheduled = None
        if reads["scheduled"] is not None:
            local_now = now.astimezone().replace(tzinfo=None)  # Orchestration keeps `next_run_at` in local time
            scheduled = []
            for task in reads["scheduled"]:
                due = _parse_time(task.get("next_run_at"))
                if task.get("state") != "active" or (task.get("job_template") or {}).get("project_id") != project_id or due is None:
                    continue
                due = due.astimezone().replace(tzinfo=None) if due.tzinfo else due
                if timedelta(minutes=-1) <= due - local_now <= timedelta(hours=STATUS_SCHEDULE_HOURS):
                    scheduled.append({"id": task["id"], "title": task.get("name"), "at": task.get("next_run_at"),
                                      "href": f"/schedule/item/scheduled/{task['id']}"})
            scheduled.sort(key=lambda item: item["at"] or "")
        status["scheduled"] = _status_topic(scheduled, hours=STATUS_SCHEDULE_HOURS)

        failing = None
        if reads["policies"] is not None:
            failing = [{"id": policy["id"], "title": policy.get("topic"), "detail": (result.get("detail") or "").strip()[:240] or None,
                        "at": result.get("checked_at"), "href": f"/policies/{policy['id']}"}
                       for policy, project, result in _failing_checks(reads["policies"], _latest_policy_results(paths, failing=True))
                       if project == project_id]
        status["policies"] = _status_topic(failing)

        # The project's latest recorded test run, whenever it was: what "do the tests pass" means here.
        tests = reads["tests"]
        if tests is None:
            status["tests"] = {"known": False}
        elif not tests:
            status["tests"] = None
        else:
            run = tests[0]
            result = run.get("result") or {}
            verdicts = result.get("verdicts") or {}
            status["tests"] = {"known": True, "status": run.get("status"), "ran": result.get("ran"), "passed": verdicts.get("passed", 0),
                               "failed": result.get("failing_total", 0), "at": run.get("updated_at"), "href": f"/jobs/{run['id']}"}

    recent = None
    if incidents is not None:
        since = now - timedelta(hours=STATUS_INCIDENT_HOURS)
        recent = []
        for incident in incidents:
            at = _parse_time(incident.get("created_at"))
            if at is not None and (at if at.tzinfo else at.replace(tzinfo=timezone.utc)) >= since:
                recent.append({"id": incident["id"], "title": f"{incident.get('exception_type')} in {incident.get('pillar')}",
                               "project": incident.get("project_id"), "at": incident.get("created_at"), "href": f"/incidents/{incident['id']}"})
        recent.sort(key=lambda item: item["at"] or "", reverse=True)
    status["incidents"] = _status_topic(recent, hours=STATUS_INCIDENT_HOURS)

    dead, watched = None, None
    if core is not None:
        pillars = core.get("pillars") or {}
        watched = sum(1 for p in pillars.values() if p.get("status") != "disabled")
        dead = [{"id": name, "title": name, "status": p.get("status"), "crash_count": p.get("crash_count", 0)}
                for name, p in sorted(pillars.items()) if p.get("status") in DEAD_PILLAR_STATUSES]
    status["dead_pillars"] = _status_topic(dead, of=watched)
    status["unavailable"] = sorted(unavailable)
    return status


# The status page's topics (REQ-000000412), with the icons the status mod draws on the phone:
# (key, icon, label, the page that lists them in full, what a count above zero says: run | warn | fail).
STATUS_PROJECT_TOPICS = (
    ("running", "⚙️", "Running jobs", "/jobs?status=running&q={project}", "run"),
    ("tasks_attention", "❗", "Tasks needing attention", "/tasks", "warn"),
    ("failed_jobs", "❌", "Failed jobs", "/jobs?status=failed&q={project}", "fail"),
    ("scheduled", "⏰", "Scheduled", "/schedule", "run"),
    ("policies", "⛔", "Failing policies", "/policies?status=failing&project={project}", "fail"),
)
STATUS_WORKSHOP_TOPICS = (
    ("incidents", "🔥", "Incidents", "/incidents", "fail"),
    ("dead_pillars", "💀", "Dead pillars", "/pillars", "fail"),
)
# What the page follows live; its time windows also move by the clock, so it looks again every minute too.
STATUS_LIVE_TOPICS = "jobs,tasks,schedule,policies,incidents,pillars"


def _status_topics(status: dict, project_id: str) -> tuple[list[dict], list[dict]]:
    """The page's tiles and panels: each topic of `status` with its icon, label, window and link."""
    def topic(key, icon, label, href, tone):
        data = status.get(key) or {"count": None, "items": []}
        window = f"last {data['hours']} h" if key in ("failed_jobs", "incidents") else f"next {data['hours']} h" if key == "scheduled" else None
        count = data["count"]
        return {"key": key, "icon": icon, "label": label, "window": window, "count": count, "of": data.get("of"),
                "items": data["items"], "href": href.format(project=project_id), "tone": tone if count else ("idle" if count == 0 else "unknown")}
    project = [topic(*spec) for spec in STATUS_PROJECT_TOPICS] if status.get("project") else []
    return project, [topic(*spec) for spec in STATUS_WORKSHOP_TOPICS]


JOB_STATUS_ORDER = ["running", "pending_confirmation", "queued", "failed", "succeeded", "cancelled", "stopped"]
JOB_STATUS_LABELS = {
    "running": "Running", "pending_confirmation": "Needs confirmation", "queued": "Queued",
    "failed": "Failed", "succeeded": "Succeeded", "cancelled": "Cancelled", "stopped": "Stopped",
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
    """'just now', '5m ago', '3h ago', '2d ago' — for a job's timestamp; 'in 25m' for one still ahead."""
    moment = _parse_time(value)
    if moment is None:
        return ""
    if moment.tzinfo is None:  # a pillar's own timestamps are UTC; a bare one must not 500 the page
        moment = moment.replace(tzinfo=timezone.utc)
    seconds = int(((now or datetime.now(timezone.utc)) - moment).total_seconds())
    if seconds <= -45:
        ahead = -seconds
        unit, size = next(((u, n) for u, n in (("d", 86400), ("h", 3600), ("m", 60)) if ahead >= n), ("m", 60))
        return f"in {max(1, round(ahead / size))}{unit}"
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


JSON_SHOWN = 20_000  # characters of a Job's params or result a page shows; the whole is at /jobs/{id}.json


def json_view(value, limit: int = JSON_SHOWN) -> dict:
    """A Job's params or result as a page shows it: {text (at most `limit` characters), cut (it was longer),
    length (of the whole), omitted (what a listed Job's summary left out, `_omitted`, or [])}."""
    omitted = list(value.get("_omitted") or []) if isinstance(value, dict) else []
    if omitted:
        value = {key: item for key, item in value.items() if key != "_omitted"}
    text = pretty_json(value)
    return {"text": text[:limit], "cut": len(text) > limit, "length": len(text), "omitted": omitted}


def build_app(paths: Paths) -> FastAPI:
    # No Swagger/ReDoc pages: `/docs` is the documentation-review page.
    background: list = []  # loops started with the server, each given the event that ends it

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI):
        stop = threading.Event()
        for loop in background:
            threading.Thread(target=loop, args=(stop,), daemon=True).start()
        yield
        stop.set()

    app = FastAPI(title="little-workshop Dashboard", docs_url=None, redoc_url=None, lifespan=lifespan)
    install_incident_reporting(app, paths, pillar="dashboard")
    templates = _templates()
    static_dir = importlib.resources.files("dashboard") / "static"
    templates.env.filters["reltime"] = relative_time
    templates.env.filters["pretty"] = pretty_json
    templates.env.globals["pillar_name"] = pillar_name
    templates.env.globals["incident_kinds"] = INCIDENT_KINDS
    templates.env.globals["job_duration"] = job_duration
    templates.env.globals["json_view"] = json_view
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
        # The user's policy bypass, warned on every page while it is TRUE.
        context.setdefault("bypass", _try(lambda: client.policy_bypass(paths), default={}) or {})
        # The topics the page's live stream follows (static/live.js); every page follows "attention" for the banner.
        context.setdefault("live_topics", "")
        return templates.TemplateResponse(request, name, context)

    # -- live pages: one event stream per open page (dashboard/live.py, static/live.js) -------------

    @app.get("/live/events")
    async def live_events(request: Request, topics: str = ""):
        wanted = [t for t in dict.fromkeys(topics.split(",")) if t][:LIVE_MAX_TOPICS]

        async def stream():
            # Subscribed here, not before: only a stream that starts can end, and its end unsubscribes.
            subscriber = live.HUB.subscribe(wanted, asyncio.get_running_loop())
            try:
                yield f"retry: {int(live.RETRY_SECONDS * 1000)}\n\n"
                idle = 0.0
                # A stopping Dashboard ends its streams, which held its stop open for up to a minute;
                # each page reconnects to the next process after `retry` (REQ-000000404).
                while not serving.stopping.is_set():
                    try:
                        message = await asyncio.wait_for(subscriber.queue.get(), timeout=LIVE_STOP_CHECK_SECONDS)
                    except asyncio.TimeoutError:
                        idle += LIVE_STOP_CHECK_SECONDS
                        if idle < LIVE_KEEPALIVE_SECONDS:
                            continue
                        idle = 0.0
                        if await request.is_disconnected():
                            return
                        yield ": keepalive\n\n"
                        continue
                    idle = 0.0
                    yield f"event: update\ndata: {json.dumps(message)}\n\n"
            finally:
                live.HUB.unsubscribe(subscriber)

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # What the route modules (dashboard/routes/) share; each also leaves there the helpers another uses.
    ctx = SimpleNamespace(paths=paths, templates=templates, static_dir=static_dir, starting=starting, background=background,
                          render=_render)
    from dashboard.routes import AREAS  # here, not at the top: the route modules import this module's helpers

    for area in AREAS:
        app.include_router(area.build_router(ctx))
    return app
