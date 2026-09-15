"""What `lws` talks to: Core and Orchestration's Pillar Interfaces over
their Unix sockets, with a direct-read fallback for pure reads when a
daemon isn't running — the same fallback `lrun status` already uses
today (docs/architecture.md's CLI section).
"""

from __future__ import annotations

import httpx

from common.manifest import load_manifest
from common.paths import Paths
from common.uds_client import client_for_socket
from common import systemd
from orchestration import db as orchestration_db


class DaemonUnavailable(Exception):
    pass


def core_status(paths: Paths, *, timeout: float = 3.0) -> dict:
    try:
        with client_for_socket(paths.core_socket_path, timeout=timeout) as client:
            response = client.get("/v1/status")
        response.raise_for_status()
        return {"source": "daemon", **response.json()}
    except (httpx.ConnectError, httpx.TimeoutException, OSError, httpx.HTTPStatusError):
        return {"source": "fallback", "pillars": _core_status_fallback(paths)}


def _core_status_fallback(paths: Paths) -> dict:
    manifest = load_manifest(paths.manifest_path)
    pillars = {}
    for name, entry in manifest.pillars.items():
        if not entry.enabled:
            status = "disabled"
        elif entry.command is None:
            status = "not_installed"
        elif systemd.is_active(name):
            status = "active (unverified — daemon down, no health probe)"
        else:
            status = "inactive"
        pillars[name] = {"status": status, "socket_path": str(paths.socket_path(name))}
    return pillars


def create_job(paths: Paths, *, timeout: float = 10.0, **fields) -> dict:
    try:
        with client_for_socket(paths.socket_path("orchestration"), timeout=timeout) as client:
            response = client.post("/v1/jobs", json=fields)
        response.raise_for_status()
        return response.json()
    except (httpx.ConnectError, httpx.TimeoutException, OSError, httpx.HTTPStatusError) as exc:
        raise DaemonUnavailable(
            "Orchestration daemon unreachable — job creation needs the daemon "
            "(dispatch can't happen from a direct file read)"
        ) from exc


def list_jobs(paths: Paths, *, status: str | None = None, timeout: float = 5.0) -> dict:
    try:
        with client_for_socket(paths.socket_path("orchestration"), timeout=timeout) as client:
            params = {"status": status} if status else {}
            response = client.get("/v1/jobs", params=params)
        response.raise_for_status()
        return {"source": "daemon", "jobs": response.json()}
    except (httpx.ConnectError, httpx.TimeoutException, OSError, httpx.HTTPStatusError):
        from orchestration.models import JobStatus

        conn = orchestration_db.connect(paths.pillar_db_path("orchestration"))
        jobs = orchestration_db.list_jobs(conn, status=JobStatus(status) if status else None)
        conn.close()
        return {"source": "fallback", "jobs": [job.model_dump(mode="json") for job in jobs]}


def job_status(paths: Paths, job_id: str, *, timeout: float = 5.0) -> dict:
    try:
        with client_for_socket(paths.socket_path("orchestration"), timeout=timeout) as client:
            response = client.get(f"/v1/jobs/{job_id}")
        response.raise_for_status()
        return {"source": "daemon", "job": response.json()}
    except (httpx.ConnectError, httpx.TimeoutException, OSError, httpx.HTTPStatusError):
        conn = orchestration_db.connect(paths.pillar_db_path("orchestration"))
        job = orchestration_db.get_job(conn, job_id)
        conn.close()
        return {"source": "fallback", "job": job.model_dump(mode="json") if job else None}


def list_projects(paths: Paths, *, status: str | None = None, timeout: float = 5.0) -> dict:
    try:
        with client_for_socket(paths.socket_path("registry"), timeout=timeout) as client:
            params = {"status": status} if status else {}
            response = client.get("/v1/projects", params=params)
        response.raise_for_status()
        return {"source": "daemon", "projects": response.json()}
    except (httpx.ConnectError, httpx.TimeoutException, OSError, httpx.HTTPStatusError):
        from registry import db as registry_db
        from registry.models import ProjectStatus

        conn = registry_db.connect(paths.pillar_db_path("registry"))
        projects = registry_db.list_projects(conn, status=ProjectStatus(status) if status else None)
        conn.close()
        return {"source": "fallback", "projects": [p.model_dump(mode="json") for p in projects]}


class VersionPillarUnavailable(Exception):
    pass


def version_status(paths: Paths, kind: str, project_id: str, *, timeout: float = 5.0) -> dict:
    try:
        with client_for_socket(paths.socket_path("version"), timeout=timeout) as client:
            response = client.get(f"/v1/status/{kind}", params={"project_id": project_id})
        response.raise_for_status()
        return response.json()
    except (httpx.ConnectError, httpx.TimeoutException, OSError, httpx.HTTPStatusError) as exc:
        raise VersionPillarUnavailable(
            "Version Control & Release daemon unreachable — this pillar has no "
            "database of its own, so there is no local fallback"
        ) from exc


def version_config(paths: Paths, project_id: str, *, timeout: float = 5.0) -> dict:
    try:
        with client_for_socket(paths.socket_path("version"), timeout=timeout) as client:
            response = client.get("/v1/config", params={"project_id": project_id})
        response.raise_for_status()
        return response.json()
    except (httpx.ConnectError, httpx.TimeoutException, OSError, httpx.HTTPStatusError) as exc:
        raise VersionPillarUnavailable("Version Control & Release daemon unreachable") from exc


def version_release_candidates(paths: Paths, project_id: str, *, timeout: float = 5.0) -> dict:
    try:
        with client_for_socket(paths.socket_path("version"), timeout=timeout) as client:
            response = client.get("/v1/release/candidates", params={"project_id": project_id})
        response.raise_for_status()
        return response.json()
    except (httpx.ConnectError, httpx.TimeoutException, OSError, httpx.HTTPStatusError) as exc:
        raise VersionPillarUnavailable("Version Control & Release daemon unreachable") from exc


class DeploymentPillarUnavailable(Exception):
    pass


def _deployment_get(paths: Paths, path: str, *, params: dict | None = None, timeout: float = 5.0) -> dict:
    try:
        with client_for_socket(paths.socket_path("deployment"), timeout=timeout) as client:
            response = client.get(path, params=params or {})
        response.raise_for_status()
        return response.json()
    except (httpx.ConnectError, httpx.TimeoutException, OSError, httpx.HTTPStatusError) as exc:
        raise DeploymentPillarUnavailable("Deployment & Servers daemon unreachable") from exc


def deployment_status(paths: Paths, project_id: str) -> dict:
    return _deployment_get(paths, f"/v1/status/{project_id}")


def deployment_history(paths: Paths, project_id: str) -> dict:
    return _deployment_get(paths, f"/v1/deployments/{project_id}")


def deployment_confirmation(paths: Paths, project_id: str) -> dict:
    return _deployment_get(paths, f"/v1/confirmations/{project_id}")


def deployment_branches(paths: Paths, project_id: str) -> dict:
    return _deployment_get(paths, f"/v1/branches/{project_id}")


def provision_worktree(paths: Paths, project_id: str, commitish: str, *, timeout: float = 30.0) -> dict:
    try:
        with client_for_socket(paths.socket_path("deployment"), timeout=timeout) as client:
            response = client.post("/v1/worktrees", json={"project_id": project_id, "commitish": commitish})
        response.raise_for_status()
        return response.json()
    except (httpx.ConnectError, httpx.TimeoutException, OSError, httpx.HTTPStatusError) as exc:
        raise DeploymentPillarUnavailable("Deployment & Servers daemon unreachable") from exc


def release_worktree(paths: Paths, project_id: str, worktree_id: str, *, timeout: float = 30.0) -> dict:
    try:
        with client_for_socket(paths.socket_path("deployment"), timeout=timeout) as client:
            response = client.delete(f"/v1/worktrees/{worktree_id}", params={"project_id": project_id})
        response.raise_for_status()
        return response.json()
    except (httpx.ConnectError, httpx.TimeoutException, OSError, httpx.HTTPStatusError) as exc:
        raise DeploymentPillarUnavailable("Deployment & Servers daemon unreachable") from exc


def deployment_rollback_timeout(paths: Paths, project_id: str, deploy_id: str, *, timeout: float = 3600.0) -> dict:
    """Calls Deployment & Servers' PI directly — never through Orchestration
    — so the confirm-gate watchdog's rollback fires even if Orchestration
    itself is down."""
    try:
        with client_for_socket(paths.socket_path("deployment"), timeout=timeout) as client:
            response = client.post(f"/v1/rollback-timeout/{project_id}", json={"deploy_id": deploy_id})
        response.raise_for_status()
        return response.json()
    except (httpx.ConnectError, httpx.TimeoutException, OSError, httpx.HTTPStatusError) as exc:
        raise DeploymentPillarUnavailable("Deployment & Servers daemon unreachable") from exc
