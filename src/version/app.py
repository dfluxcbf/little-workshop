"""Version Control & Release's Pillar Interface. Mutating operations
follow the `POST /v1/jobs/<type>` dispatch convention; read-only status/
config queries are plain `GET`s, the same split Registry & Portfolio's
own interface already makes.
"""

from __future__ import annotations

import threading
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from common.job_steps import StepAborted, StepFailed, StepReporter, reporting
from common.paths import Paths
from common.incident import KIND_UNEXPECTED, install_incident_reporting, report_problem
from version.commands.config import cmd_config_add_hook, cmd_config_list, cmd_config_remove_hook
from version.commands.hotfix import cmd_hotfix_finish, cmd_hotfix_start, cmd_hotfix_status
from version.commands.init import cmd_init
from version.commands.issue import cmd_issue_list, cmd_issue_set, cmd_issue_unset
from version.commands.release import (
    cmd_release_candidate_cut,
    cmd_release_candidate_list,
    cmd_release_candidate_promote,
    cmd_release_finish,
    cmd_release_start,
    cmd_release_status,
)
from version.commands.support import cmd_support_finish, cmd_support_start
from version.commands.sync_version_files import cmd_sync_version_files
from version.release_job import ReleaseFailed, ReleaseRefused, run_release
from version.release_services import PillarReleaseServices, ServiceUnavailable
from version.commands.topic_branch import cmd_topic_finish, cmd_topic_start, cmd_topic_status
from version.branch_audit import audit_as_dicts
from version.common import CommandResult
from version.gitops import GitError
from version.resolve import ProjectResolutionError, resolve_project_path
from version.running import RunningJobs
from version.stabilize import stabilize


# Jobs that may stop and ask the user (uncommitted changes, merge conflicts) run in the background
# and report back by job id, as `version.release` does: a question can wait for hours, far longer
# than a dispatch call lasts. Everything else answers within the call.
ASKING_JOB_TYPES = frozenset({
    "version.feature_start", "version.improvement_start", "version.hotfix_start",
    "version.feature_finish", "version.improvement_finish", "version.hotfix_finish",
    "version.release_start", "version.release_candidate_cut", "version.release_finish",
})


class JobRequest(BaseModel):
    job_id: str
    project_id: str | None = None
    params: dict[str, Any] = {}


JobHandler = Callable[[Path, dict[str, Any]], CommandResult]


def _init(repo_root: Path, params: dict) -> CommandResult:
    return cmd_init(repo_root, params.get("production_branch"), params.get("integration_branch"), params.get("project_id"))


def _feature_start(repo_root: Path, params: dict) -> CommandResult:
    return cmd_topic_start(repo_root, prefix="feature", name=params["name"], issue=params.get("issue"), force=params.get("force", False))


def _feature_finish(repo_root: Path, params: dict) -> CommandResult:
    return cmd_topic_finish(repo_root, prefix="feature", commit_label="feat", force=params.get("force", False), action=params.get("action"))


def _improvement_start(repo_root: Path, params: dict) -> CommandResult:
    return cmd_topic_start(repo_root, prefix="improvement", name=params["name"], issue=params.get("issue"), force=params.get("force", False))


def _improvement_finish(repo_root: Path, params: dict) -> CommandResult:
    return cmd_topic_finish(repo_root, prefix="improvement", commit_label="impr", force=params.get("force", False), action=params.get("action"))


def _hotfix_start(repo_root: Path, params: dict) -> CommandResult:
    return cmd_hotfix_start(repo_root, params["name"], issue=params.get("issue"), force=params.get("force", False))


def _hotfix_finish(repo_root: Path, params: dict) -> CommandResult:
    return cmd_hotfix_finish(repo_root, force=params.get("force", False), action=params.get("action"))


def _release_start(repo_root: Path, params: dict) -> CommandResult:
    return cmd_release_start(repo_root, force=params.get("force", False))


def _release_candidate_cut(repo_root: Path, params: dict) -> CommandResult:
    return cmd_release_candidate_cut(repo_root, number=params.get("number"), force=params.get("force", False))


def _release_candidate_promote(repo_root: Path, params: dict) -> CommandResult:
    return cmd_release_candidate_promote(repo_root, params["rc_number"])


def _release_finish(repo_root: Path, params: dict) -> CommandResult:
    return cmd_release_finish(repo_root, force=params.get("force", False), action=params.get("action"))


def _support_start(repo_root: Path, params: dict) -> CommandResult:
    return cmd_support_start(repo_root, params["version"], force=params.get("force", False))


def _support_finish(repo_root: Path, params: dict) -> CommandResult:
    return cmd_support_finish(repo_root)


def _config_add_hook(repo_root: Path, params: dict) -> CommandResult:
    return cmd_config_add_hook(repo_root, params["hook_path"])


def _config_remove_hook(repo_root: Path, params: dict) -> CommandResult:
    return cmd_config_remove_hook(repo_root, params["hook_path"])


def _issue_set(repo_root: Path, params: dict) -> CommandResult:
    return cmd_issue_set(repo_root, params["issue_number"], branch=params.get("branch"))


def _issue_unset(repo_root: Path, params: dict) -> CommandResult:
    return cmd_issue_unset(repo_root, branch=params.get("branch"))


def _branch_audit(repo_root: Path, params: dict) -> CommandResult:
    violations = audit_as_dicts(repo_root)
    status = "violations" if violations else "clean"
    return CommandResult(success=True, message=f"{len(violations)} violation(s)", data={"status": status, "violations": violations})


def _stabilize(repo_root: Path, params: dict) -> CommandResult:
    result = stabilize(repo_root, execute=params.get("execute", False))
    success = result.status in ("stabilized", "skipped")
    return CommandResult(success=success, message=result.message or result.status, data=asdict(result))


def _sync_version_files(repo_root: Path, params: dict) -> CommandResult:
    return cmd_sync_version_files(repo_root)


JOB_HANDLERS: dict[str, JobHandler] = {
    "version.init": _init,
    "version.feature_start": _feature_start,
    "version.feature_finish": _feature_finish,
    "version.improvement_start": _improvement_start,
    "version.improvement_finish": _improvement_finish,
    "version.hotfix_start": _hotfix_start,
    "version.hotfix_finish": _hotfix_finish,
    "version.release_start": _release_start,
    "version.release_candidate_cut": _release_candidate_cut,
    "version.release_candidate_promote": _release_candidate_promote,
    "version.release_finish": _release_finish,
    "version.support_start": _support_start,
    "version.support_finish": _support_finish,
    "version.config_add_hook": _config_add_hook,
    "version.config_remove_hook": _config_remove_hook,
    "version.issue_set": _issue_set,
    "version.issue_unset": _issue_unset,
    "version.branch_audit": _branch_audit,
    "version.stabilize": _stabilize,
    "version.sync_version_files": _sync_version_files,
}

STATUS_HANDLERS: dict[str, Callable[[Path], CommandResult]] = {
    "feature": lambda repo_root: cmd_topic_status(repo_root, prefix="feature"),
    "improvement": lambda repo_root: cmd_topic_status(repo_root, prefix="improvement"),
    "hotfix": cmd_hotfix_status,
    "release": cmd_release_status,
}


# How long an asking Job is waited for inside the dispatch call before it carries on in the background.
SYNC_GRACE_SECONDS = 12.0
INTERRUPTED_JOB_REASON = (
    "interrupted: the version pillar restarted while this Job was running, so it stopped part-way; the "
    "repository may be in the middle of it (a branch, a merge or a tag), so check it before running the Job again"
)


def running_jobs_path(paths: Paths) -> Path:
    """Where `RunningJobs` keeps the Jobs this pillar accepted and still runs."""
    return paths.state_home / "pillars" / "version-running.json"


def _asking_outcome(reporter: StepReporter, handler: JobHandler, repo_root: Path, request: JobRequest) -> dict | None:
    """Run `handler` with steps reported; the outcome as a dispatch answer, or None when the Job was
    settled by someone else (stopped, or its wait ran out) while a step waited."""
    try:
        with reporting(reporter):
            result = handler(repo_root, request.params)
    except StepAborted:
        return None
    except (StepFailed, GitError, FileNotFoundError, KeyError) as exc:
        return {"outcome": "failed", "error": str(exc)}
    except Exception as exc:  # never leave the Job running forever
        return {"outcome": "failed", "error": f"{type(exc).__name__}: {exc}"}
    if result.success:
        return {"outcome": "succeeded", "result": result.data, "message": result.message}
    return {"outcome": "failed", "error": result.message}


def _run_asking_job(
    services, reporter: StepReporter, handler: JobHandler, repo_root: Path, request: JobRequest,
    running: RunningJobs, job_type: str,
) -> dict:
    """The answer to the dispatch call: the Job's own outcome when it finishes within `SYNC_GRACE_SECONDS`
    (almost always, unless it stops to ask the user), otherwise `accepted`, and the Job reports back through
    Orchestration's completion callback when it ends. An accepted Job is in `running` until then."""
    lock = threading.Lock()
    state: dict = {"detached": False, "outcome": None, "finished": False}
    finished = threading.Event()

    def work() -> None:
        outcome = _asking_outcome(reporter, handler, repo_root, request)
        with lock:
            state["outcome"], state["finished"] = outcome, True
            detached = state["detached"]
        finished.set()
        if detached:
            running.remove(request.job_id)
        if detached and outcome is not None:
            ok = outcome["outcome"] == "succeeded"
            services.complete(
                request.job_id, succeeded=ok,
                result={**(outcome.get("result") or {}), "message": outcome.get("message")} if ok else None,
                error=None if ok else outcome["error"],
            )

    threading.Thread(target=work, daemon=True).start()
    finished.wait(SYNC_GRACE_SECONDS)
    with lock:
        if state["finished"]:
            return state["outcome"] or {"outcome": "failed", "error": "the job was settled while it waited"}
        state["detached"] = True
        running.add(request.job_id, job_type, request.project_id)  # under the lock: before the thread can remove it
    return {"outcome": "accepted", "result": {"dispatched": True}}


def build_app(
    paths: Paths, resolve: Callable[[Paths, str | None], Path] = resolve_project_path, release_services=None,
) -> FastAPI:
    app = FastAPI(title="little-workshop Version Control & Release")
    install_incident_reporting(app, paths, pillar="version")
    release_services = release_services or PillarReleaseServices(paths)
    running = RunningJobs(running_jobs_path(paths))

    def settle_once() -> list[str]:
        left = []
        for job_id, job in running.all().items():
            if release_services.complete(job_id, succeeded=False, result=None, error=INTERRUPTED_JOB_REASON) is not True:
                left.append(job_id)  # Orchestration did not take it: tried again
                continue
            running.remove(job_id)
            project = job.get("project_id") or "no project"
            report_problem(
                paths, pillar="version", context=f"job {job_id}", kind=KIND_UNEXPECTED, title="InterruptedJob",
                message=f"the version pillar restarted while {job['type']} on {project} was running: the Job was failed "
                        "part-way, and its repository may be in the middle of it (a release branch, a merge, a tag). "
                        "Check `lws version status` and the Job's steps before running it again",
                detail=f"job {job_id}\ntype {job['type']}\nproject {project}\naccepted {job.get('since', '?')}",
            )
        return left

    def settle_interrupted_jobs(*, retry_seconds: float = 30.0, tries: int = 60) -> list[str]:
        """Startup, before serving (REQ-000000410): fail each Job the previous process accepted and never
        finished. A release or branch finish cut part-way may have left its repository mid-way, so it is
        never run again on its own; an incident says where to look. Orchestration may not be back yet
        (a redeploy restarts it too): what it does not take is tried again in the background."""
        left = settle_once()
        if left and tries > 0:
            def retry() -> None:
                for _ in range(tries):
                    time.sleep(retry_seconds)
                    if not settle_once():
                        return

            threading.Thread(target=retry, daemon=True, name="version: settle interrupted jobs").start()
        return left

    app.state.settle_interrupted_jobs = settle_interrupted_jobs

    def reporter_for(job_id: str) -> StepReporter:
        return StepReporter(paths, job_id)

    @app.get("/v1/health")
    def health() -> dict:
        return {"ok": True}

    @app.post("/v1/jobs/version.release")
    def job_release(request: JobRequest) -> dict:
        """The Release job: conditions, release start, changelog, release finish,
        relaunch. It waits on test runs and policy checks that take many minutes,
        so it runs on a background thread and reports back by job id, like
        `agent.run_task`."""
        try:
            repo_root = resolve(paths, request.project_id)
        except ProjectResolutionError as exc:
            return {"outcome": "failed", "error": str(exc)}

        def finish(**outcome) -> None:
            running.remove(request.job_id)
            release_services.complete(request.job_id, **outcome)

        def worker() -> None:
            try:
                with reporting(reporter_for(request.job_id)):
                    result = run_release(repo_root, request.project_id, request.params, release_services, job_id=request.job_id)
            except StepFailed as exc:
                finish(succeeded=False, result=None, error=str(exc))
            except ReleaseRefused as exc:
                finish(succeeded=False, result={"checks": exc.checks} if exc.checks else None, error=str(exc))
            except (ReleaseFailed, ServiceUnavailable, GitError, FileNotFoundError, KeyError) as exc:
                finish(succeeded=False, result=None, error=str(exc))
            except Exception as exc:  # never leave the Job running forever
                finish(succeeded=False, result=None, error=f"release crashed: {exc!r}")
                raise
            else:
                finish(succeeded=True, result=result, error=None)

        running.add(request.job_id, "version.release", request.project_id)
        threading.Thread(target=worker, daemon=True).start()
        return {"outcome": "accepted", "result": {"dispatched": True}}

    @app.post("/v1/jobs/{job_type}")
    def run_job(job_type: str, request: JobRequest) -> dict:
        handler = JOB_HANDLERS.get(job_type)
        if handler is None:
            raise HTTPException(status_code=404, detail=f"unknown job type {job_type!r}")

        try:
            repo_root = resolve(paths, request.project_id)
        except ProjectResolutionError as exc:
            return {"outcome": "failed", "error": str(exc)}

        if job_type in ASKING_JOB_TYPES:
            return _run_asking_job(release_services, reporter_for(request.job_id), handler, repo_root, request, running, job_type)

        try:
            with reporting(reporter_for(request.job_id)):
                result = handler(repo_root, request.params)
        except (GitError, FileNotFoundError, KeyError) as exc:
            return {"outcome": "failed", "error": str(exc)}

        if result.success:
            return {"outcome": "succeeded", "result": result.data, "message": result.message}
        return {"outcome": "failed", "error": result.message}

    @app.get("/v1/status/{kind}")
    def status(kind: str, project_id: str) -> dict:
        handler = STATUS_HANDLERS.get(kind)
        if handler is None:
            raise HTTPException(status_code=404, detail=f"unknown status kind {kind!r}")
        try:
            repo_root = resolve(paths, project_id)
        except ProjectResolutionError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        result = handler(repo_root)
        return {"success": result.success, "message": result.message, "data": result.data}

    @app.get("/v1/config")
    def config(project_id: str) -> dict:
        try:
            repo_root = resolve(paths, project_id)
        except ProjectResolutionError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        result = cmd_config_list(repo_root)
        return result.data["config"]

    @app.get("/v1/issues")
    def issues(project_id: str) -> dict:
        try:
            repo_root = resolve(paths, project_id)
        except ProjectResolutionError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        result = cmd_issue_list(repo_root)
        return result.data

    @app.get("/v1/release/candidates")
    def release_candidates(project_id: str) -> dict:
        try:
            repo_root = resolve(paths, project_id)
        except ProjectResolutionError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        result = cmd_release_candidate_list(repo_root)
        if not result.success:
            raise HTTPException(status_code=400, detail=result.message)
        return result.data

    return app
