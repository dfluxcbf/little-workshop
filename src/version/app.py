"""Version Control & Release's Pillar Interface. Mutating operations
follow the `POST /v1/jobs/<type>` dispatch convention; read-only status/
config queries are plain `GET`s, the same split Registry & Portfolio's
own interface already makes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from common.paths import Paths
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
from version.commands.topic_branch import cmd_topic_finish, cmd_topic_start, cmd_topic_status
from version.branch_audit import audit_as_dicts
from version.common import CommandResult
from version.gitops import GitError
from version.resolve import ProjectResolutionError, resolve_project_path


class JobRequest(BaseModel):
    job_id: str
    project_id: str | None = None
    params: dict[str, Any] = {}


JobHandler = Callable[[Path, dict[str, Any]], CommandResult]


def _init(repo_root: Path, params: dict) -> CommandResult:
    return cmd_init(repo_root, params.get("production_branch"), params.get("integration_branch"))


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
}

STATUS_HANDLERS: dict[str, Callable[[Path], CommandResult]] = {
    "feature": lambda repo_root: cmd_topic_status(repo_root, prefix="feature"),
    "improvement": lambda repo_root: cmd_topic_status(repo_root, prefix="improvement"),
    "hotfix": cmd_hotfix_status,
    "release": cmd_release_status,
}


def build_app(paths: Paths, resolve: Callable[[Paths, str | None], Path] = resolve_project_path) -> FastAPI:
    app = FastAPI(title="little-workshop Version Control & Release")

    @app.get("/v1/health")
    def health() -> dict:
        return {"ok": True}

    @app.post("/v1/jobs/{job_type}")
    def run_job(job_type: str, request: JobRequest) -> dict:
        handler = JOB_HANDLERS.get(job_type)
        if handler is None:
            raise HTTPException(status_code=404, detail=f"unknown job type {job_type!r}")

        try:
            repo_root = resolve(paths, request.project_id)
        except ProjectResolutionError as exc:
            return {"outcome": "failed", "error": str(exc)}

        try:
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
