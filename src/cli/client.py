"""What `lws` talks to: Core and Orchestration's Pillar Interfaces over
their Unix sockets, with a direct-read fallback for pure reads when a
daemon isn't running — the same fallback `lrun status` already uses
today (docs/architecture.md's CLI section).
"""

from __future__ import annotations

import os

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


def create_job(paths: Paths, *, timeout: float = 30.0, **fields) -> dict:
    """Create a Job and return it once dispatched. The Job's checklist is shown on stderr while it runs
    (`cli.progress`); `LWS_NO_PROGRESS=1` turns that off."""
    if os.environ.get("LWS_NO_PROGRESS"):
        return post_job(paths, timeout=timeout, **fields)
    from cli import progress

    return progress.create_and_follow(paths, timeout=timeout, **fields)


def post_job(paths: Paths, *, timeout: float = 30.0, **fields) -> dict:
    try:
        with client_for_socket(paths.socket_path("orchestration"), timeout=timeout) as client:
            response = client.post("/v1/jobs", json=fields)
        response.raise_for_status()
        return response.json()
    except httpx.ReadTimeout as exc:
        # It took the request: the Job exists and may well be running.
        raise DaemonUnavailable(
            f"Orchestration took the job but has not answered within {timeout:g}s; it may still be "
            "working on it -- check `lws job list` before retrying"
        ) from exc
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


def stop_job(paths: Paths, job_id: str, *, timeout: float = 10.0) -> dict:
    try:
        with client_for_socket(paths.socket_path("orchestration"), timeout=timeout) as client:
            response = client.post(f"/v1/jobs/{job_id}/stop")
        response.raise_for_status()
        return response.json()
    except (httpx.ConnectError, httpx.TimeoutException, OSError, httpx.HTTPStatusError) as exc:
        raise DaemonUnavailable("Orchestration daemon unreachable — stopping a job needs the daemon") from exc


def policy_confirm_job(paths: Paths, job_id: str, *, timeout: float = 10.0) -> dict:
    try:
        with client_for_socket(paths.socket_path("orchestration"), timeout=timeout) as client:
            response = client.post(f"/v1/jobs/{job_id}/policy_confirm")
        response.raise_for_status()
        return response.json()
    except (httpx.ConnectError, httpx.TimeoutException, OSError, httpx.HTTPStatusError) as exc:
        raise DaemonUnavailable("Orchestration daemon unreachable — confirming a gated job needs the daemon") from exc


def policy_confirm_timeout_job(paths: Paths, job_id: str, *, timeout: float = 10.0) -> dict:
    try:
        with client_for_socket(paths.socket_path("orchestration"), timeout=timeout) as client:
            response = client.post(f"/v1/jobs/{job_id}/policy_confirm_timeout")
        response.raise_for_status()
        return response.json()
    except (httpx.ConnectError, httpx.TimeoutException, OSError, httpx.HTTPStatusError) as exc:
        raise DaemonUnavailable("Orchestration daemon unreachable — the confirm-gate watchdog needs the daemon") from exc


def create_scheduled_task(paths: Paths, *, timeout: float = 10.0, **fields) -> dict:
    try:
        with client_for_socket(paths.socket_path("orchestration"), timeout=timeout) as client:
            response = client.post("/v1/scheduled_tasks", json=fields)
        response.raise_for_status()
        return response.json()
    except (httpx.ConnectError, httpx.TimeoutException, OSError, httpx.HTTPStatusError) as exc:
        raise DaemonUnavailable("Orchestration daemon unreachable — scheduling needs the daemon") from exc


def list_scheduled_tasks(paths: Paths, *, timeout: float = 5.0) -> dict:
    try:
        with client_for_socket(paths.socket_path("orchestration"), timeout=timeout) as client:
            response = client.get("/v1/scheduled_tasks")
        response.raise_for_status()
        return {"source": "daemon", "scheduled_tasks": response.json()}
    except (httpx.ConnectError, httpx.TimeoutException, OSError, httpx.HTTPStatusError):
        conn = orchestration_db.connect(paths.pillar_db_path("orchestration"))
        tasks = orchestration_db.list_scheduled_tasks(conn)
        conn.close()
        return {"source": "fallback", "scheduled_tasks": [t.model_dump(mode="json") for t in tasks]}


def get_scheduled_task(paths: Paths, task_id: str, *, timeout: float = 5.0) -> dict:
    try:
        with client_for_socket(paths.socket_path("orchestration"), timeout=timeout) as client:
            response = client.get(f"/v1/scheduled_tasks/{task_id}")
        response.raise_for_status()
        return {"source": "daemon", "scheduled_task": response.json()}
    except (httpx.ConnectError, httpx.TimeoutException, OSError, httpx.HTTPStatusError):
        conn = orchestration_db.connect(paths.pillar_db_path("orchestration"))
        task = orchestration_db.get_scheduled_task(conn, task_id)
        conn.close()
        return {"source": "fallback", "scheduled_task": task.model_dump(mode="json") if task else None}


def edit_scheduled_task(paths: Paths, task_id: str, *, timeout: float = 10.0, **fields) -> dict:
    try:
        with client_for_socket(paths.socket_path("orchestration"), timeout=timeout) as client:
            response = client.patch(f"/v1/scheduled_tasks/{task_id}", json=fields)
        response.raise_for_status()
        return response.json()
    except (httpx.ConnectError, httpx.TimeoutException, OSError, httpx.HTTPStatusError) as exc:
        raise DaemonUnavailable("Orchestration daemon unreachable — editing a schedule needs the daemon") from exc


def remove_scheduled_task(paths: Paths, task_id: str, *, timeout: float = 10.0) -> dict:
    try:
        with client_for_socket(paths.socket_path("orchestration"), timeout=timeout) as client:
            response = client.delete(f"/v1/scheduled_tasks/{task_id}")
        response.raise_for_status()
        return response.json()
    except (httpx.ConnectError, httpx.TimeoutException, OSError, httpx.HTTPStatusError) as exc:
        raise DaemonUnavailable("Orchestration daemon unreachable — removing a schedule needs the daemon") from exc


def trigger_scheduled_task(paths: Paths, task_id: str, *, timeout: float = 30.0) -> dict:
    try:
        with client_for_socket(paths.socket_path("orchestration"), timeout=timeout) as client:
            response = client.post(f"/v1/scheduled_tasks/{task_id}/trigger")
        response.raise_for_status()
        return response.json()
    except (httpx.ConnectError, httpx.TimeoutException, OSError, httpx.HTTPStatusError) as exc:
        raise DaemonUnavailable("Orchestration daemon unreachable — triggering a schedule needs the daemon") from exc


def cancel_scheduled_task(paths: Paths, task_id: str, *, timeout: float = 10.0) -> dict:
    try:
        with client_for_socket(paths.socket_path("orchestration"), timeout=timeout) as client:
            response = client.post(f"/v1/scheduled_tasks/{task_id}/cancel")
        response.raise_for_status()
        return response.json()
    except (httpx.ConnectError, httpx.TimeoutException, OSError, httpx.HTTPStatusError) as exc:
        raise DaemonUnavailable("Orchestration daemon unreachable — cancelling a schedule needs the daemon") from exc


def postpone_scheduled_task(paths: Paths, task_id: str, at: str, *, timeout: float = 10.0) -> dict:
    try:
        with client_for_socket(paths.socket_path("orchestration"), timeout=timeout) as client:
            response = client.post(f"/v1/scheduled_tasks/{task_id}/postpone", json={"at": at})
        response.raise_for_status()
        return response.json()
    except (httpx.ConnectError, httpx.TimeoutException, OSError, httpx.HTTPStatusError) as exc:
        raise DaemonUnavailable("Orchestration daemon unreachable — postponing a schedule needs the daemon") from exc


def get_queue_capacity(paths: Paths, queue_key: str, *, timeout: float = 5.0) -> dict:
    try:
        with client_for_socket(paths.socket_path("orchestration"), timeout=timeout) as client:
            response = client.get(f"/v1/queue_keys/{queue_key}/capacity")
        response.raise_for_status()
        return {"source": "daemon", **response.json()}
    except (httpx.ConnectError, httpx.TimeoutException, OSError, httpx.HTTPStatusError):
        conn = orchestration_db.connect(paths.pillar_db_path("orchestration"))
        capacity = orchestration_db.get_queue_capacity(conn, queue_key)
        conn.close()
        return {"source": "fallback", "queue_key": queue_key, "capacity": capacity}


def set_queue_capacity(paths: Paths, queue_key: str, capacity: int, *, timeout: float = 10.0) -> dict:
    try:
        with client_for_socket(paths.socket_path("orchestration"), timeout=timeout) as client:
            response = client.post(f"/v1/queue_keys/{queue_key}/capacity", json={"capacity": capacity})
        response.raise_for_status()
        return response.json()
    except (httpx.ConnectError, httpx.TimeoutException, OSError, httpx.HTTPStatusError) as exc:
        raise DaemonUnavailable("Orchestration daemon unreachable — setting queue capacity needs the daemon") from exc


def get_job(paths: Paths, job_id: str, *, timeout: float = 5.0) -> dict:
    """The Job with its checklist, from the daemon only (no fallback to the database file)."""
    try:
        with client_for_socket(paths.socket_path("orchestration"), timeout=timeout) as client:
            response = client.get(f"/v1/jobs/{job_id}")
        response.raise_for_status()
        return response.json()
    except (httpx.ConnectError, httpx.TimeoutException, OSError, httpx.HTTPStatusError) as exc:
        raise DaemonUnavailable(f"could not read job {job_id} from the Orchestration daemon: {exc}") from exc


def answer_step(paths: Paths, job_id: str, key: str, choice: str, *, timeout: float = 10.0) -> dict:
    try:
        with client_for_socket(paths.socket_path("orchestration"), timeout=timeout) as client:
            response = client.post(f"/v1/jobs/{job_id}/steps/{key}/answer", json={"choice": choice})
        if response.status_code in (404, 409, 422):
            raise DaemonUnavailable(f"answer refused: {response.json().get('detail', response.text)}")
        response.raise_for_status()
        return response.json()
    except (httpx.ConnectError, httpx.TimeoutException, OSError, httpx.HTTPStatusError) as exc:
        raise DaemonUnavailable(f"could not send the answer to the Orchestration daemon: {exc}") from exc


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


def job_wait(paths: Paths, job_id: str, timeout_seconds: float | None = None) -> dict:
    """Orchestration's long poll: the Job once it settled, is held for a confirmation or asks a question,
    else as it is when the timeout passed."""
    params = {"timeout": timeout_seconds} if timeout_seconds is not None else {}
    try:
        with client_for_socket(paths.socket_path("orchestration"), timeout=(timeout_seconds or 120.0) + 15.0) as client:
            response = client.get(f"/v1/jobs/{job_id}/wait", params=params)
        if response.status_code == 404:
            return {"source": "daemon", "job": None}
        response.raise_for_status()
        return {"source": "daemon", "job": response.json()}
    except (httpx.ConnectError, httpx.TimeoutException, OSError, httpx.HTTPStatusError) as exc:
        raise DaemonUnavailable(f"could not wait for the Job: the Orchestration daemon is unreachable: {exc}") from exc


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


def version_issues(paths: Paths, project_id: str, *, timeout: float = 5.0) -> dict:
    try:
        with client_for_socket(paths.socket_path("version"), timeout=timeout) as client:
            response = client.get("/v1/issues", params={"project_id": project_id})
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


class RequirementsPillarUnavailable(Exception):
    pass


def requirements_list_test_suites(paths: Paths, project_id: str | None = None, *, timeout: float = 5.0) -> dict:
    try:
        with client_for_socket(paths.socket_path("requirements"), timeout=timeout) as client:
            response = client.get("/v1/test_suites", params={"project_id": project_id} if project_id else {})
        response.raise_for_status()
        return {"source": "daemon", "test_suites": response.json()}
    except (httpx.ConnectError, httpx.TimeoutException, OSError, httpx.HTTPStatusError):
        from requirements import db as requirements_db

        conn = requirements_db.connect(paths.pillar_db_path("requirements"))
        suites = requirements_db.list_test_suites(conn, project_id=project_id)
        conn.close()
        return {"source": "fallback", "test_suites": [s.model_dump(mode="json") for s in suites]}


def requirements_get_test_suite(paths: Paths, suite_id: str, *, timeout: float = 5.0) -> dict:
    try:
        with client_for_socket(paths.socket_path("requirements"), timeout=timeout) as client:
            response = client.get(f"/v1/test_suites/{suite_id}")
        response.raise_for_status()
        return {"source": "daemon", "test_suite": response.json()}
    except (httpx.ConnectError, httpx.TimeoutException, OSError, httpx.HTTPStatusError):
        from requirements import db as requirements_db

        conn = requirements_db.connect(paths.pillar_db_path("requirements"))
        suite = requirements_db.get_test_suite(conn, suite_id)
        conn.close()
        return {"source": "fallback", "test_suite": suite.model_dump(mode="json") if suite else None}


def requirements_list_requirements(
    paths: Paths, project_id: str | None = None, status: str | None = None, *, timeout: float = 5.0
) -> dict:
    params = {k: v for k, v in {"project_id": project_id, "status": status}.items() if v}
    try:
        with client_for_socket(paths.socket_path("requirements"), timeout=timeout) as client:
            response = client.get("/v1/requirements", params=params)
        response.raise_for_status()
        return {"source": "daemon", "requirements": response.json()}
    except (httpx.ConnectError, httpx.TimeoutException, OSError, httpx.HTTPStatusError):
        from requirements import db as requirements_db
        from requirements.models import RequirementStatus

        conn = requirements_db.connect(paths.pillar_db_path("requirements"))
        items = requirements_db.list_requirements(
            conn, project_id=project_id, status=RequirementStatus(status) if status else None
        )
        conn.close()
        return {"source": "fallback", "requirements": [r.model_dump(mode="json") for r in items]}


def requirements_get_requirement(paths: Paths, requirement_id: str, *, timeout: float = 5.0) -> dict:
    try:
        with client_for_socket(paths.socket_path("requirements"), timeout=timeout) as client:
            response = client.get(f"/v1/requirements/{requirement_id}")
        response.raise_for_status()
        return {"source": "daemon", "requirement": response.json()}
    except (httpx.ConnectError, httpx.TimeoutException, OSError, httpx.HTTPStatusError):
        from requirements import db as requirements_db

        conn = requirements_db.connect(paths.pillar_db_path("requirements"))
        requirement = requirements_db.get_requirement(conn, requirement_id)
        payload = None
        if requirement is not None:
            payload = requirement.model_dump(mode="json")
            payload["suite_ids"] = requirements_db.list_suites_for_requirement(conn, requirement_id)
        conn.close()
        return {"source": "fallback", "requirement": payload}


def requirements_list_test_results(
    paths: Paths, project_id: str | None = None, suite_id: str | None = None, *, timeout: float = 5.0
) -> dict:
    params = {k: v for k, v in {"project_id": project_id, "suite_id": suite_id}.items() if v}
    try:
        with client_for_socket(paths.socket_path("requirements"), timeout=timeout) as client:
            response = client.get("/v1/test_results", params=params)
        response.raise_for_status()
        return {"source": "daemon", "test_results": response.json()}
    except (httpx.ConnectError, httpx.TimeoutException, OSError, httpx.HTTPStatusError):
        from requirements import db as requirements_db

        conn = requirements_db.connect(paths.pillar_db_path("requirements"))
        results = requirements_db.list_test_results(conn, project_id=project_id, suite_id=suite_id)
        conn.close()
        return {"source": "fallback", "test_results": [r.model_dump(mode="json") for r in results]}


def requirements_list_use_cases(paths: Paths, project_id: str | None = None, *, timeout: float = 5.0) -> dict:
    try:
        with client_for_socket(paths.socket_path("requirements"), timeout=timeout) as client:
            response = client.get("/v1/use_cases", params={"project_id": project_id} if project_id else {})
        response.raise_for_status()
        return {"source": "daemon", "use_cases": response.json()}
    except (httpx.ConnectError, httpx.TimeoutException, OSError, httpx.HTTPStatusError):
        from requirements import db as requirements_db

        conn = requirements_db.connect(paths.pillar_db_path("requirements"))
        items = requirements_db.list_use_cases(conn, project_id=project_id)
        conn.close()
        return {"source": "fallback", "use_cases": [u.model_dump(mode="json") for u in items]}


def requirements_list_milestones(paths: Paths, project_id: str | None = None, *, timeout: float = 5.0) -> dict:
    try:
        with client_for_socket(paths.socket_path("requirements"), timeout=timeout) as client:
            response = client.get("/v1/milestones", params={"project_id": project_id} if project_id else {})
        response.raise_for_status()
        return {"source": "daemon", "milestones": response.json()}
    except (httpx.ConnectError, httpx.TimeoutException, OSError, httpx.HTTPStatusError):
        from requirements import db as requirements_db

        conn = requirements_db.connect(paths.pillar_db_path("requirements"))
        items = requirements_db.list_milestones(conn, project_id=project_id)
        conn.close()
        return {"source": "fallback", "milestones": [m.model_dump(mode="json") for m in items]}


def requirements_report(paths: Paths, project_id: str, *, timeout: float = 10.0) -> dict:
    try:
        with client_for_socket(paths.socket_path("requirements"), timeout=timeout) as client:
            response = client.get("/v1/report", params={"project_id": project_id})
        response.raise_for_status()
        return response.json()
    except (httpx.ConnectError, httpx.TimeoutException, OSError, httpx.HTTPStatusError) as exc:
        raise RequirementsPillarUnavailable(
            "Requirements & Quality daemon unreachable — the definition-of-done report is computed "
            "live and has no local fallback"
        ) from exc


def build_list_runs(paths: Paths, project_id: str | None = None, *, timeout: float = 5.0) -> dict:
    try:
        with client_for_socket(paths.socket_path("build"), timeout=timeout) as client:
            response = client.get("/v1/build_runs", params={"project_id": project_id} if project_id else {})
        response.raise_for_status()
        return {"source": "daemon", "build_runs": response.json()}
    except (httpx.ConnectError, httpx.TimeoutException, OSError, httpx.HTTPStatusError):
        from build import db as build_db

        conn = build_db.connect(paths.pillar_db_path("build"))
        runs = build_db.list_build_runs(conn, project_id=project_id)
        conn.close()
        return {"source": "fallback", "build_runs": [r.model_dump(mode="json") for r in runs]}


def build_get_run(paths: Paths, run_id: str, *, timeout: float = 5.0) -> dict:
    try:
        with client_for_socket(paths.socket_path("build"), timeout=timeout) as client:
            response = client.get(f"/v1/build_runs/{run_id}")
        response.raise_for_status()
        return {"source": "daemon", "build_run": response.json()}
    except (httpx.ConnectError, httpx.TimeoutException, OSError, httpx.HTTPStatusError):
        from build import db as build_db

        conn = build_db.connect(paths.pillar_db_path("build"))
        run = build_db.get_build_run(conn, run_id)
        conn.close()
        return {"source": "fallback", "build_run": run.model_dump(mode="json") if run else None}


def build_list_packages(
    paths: Paths, project_id: str | None = None, kind: str | None = None, *, timeout: float = 5.0
) -> dict:
    params = {k: v for k, v in {"project_id": project_id, "kind": kind}.items() if v}
    try:
        with client_for_socket(paths.socket_path("build"), timeout=timeout) as client:
            response = client.get("/v1/packages", params=params)
        response.raise_for_status()
        return {"source": "daemon", "packages": response.json()}
    except (httpx.ConnectError, httpx.TimeoutException, OSError, httpx.HTTPStatusError):
        from build import db as build_db
        from build.models import PackageKind

        conn = build_db.connect(paths.pillar_db_path("build"))
        packages = build_db.list_packages(conn, project_id=project_id, kind=PackageKind(kind) if kind else None)
        conn.close()
        return {"source": "fallback", "packages": [p.model_dump(mode="json") for p in packages]}


class PolicyPillarUnavailable(Exception):
    pass


def policy_list(paths: Paths, topic: str | None = None, *, timeout: float = 5.0) -> dict:
    try:
        with client_for_socket(paths.socket_path("policy"), timeout=timeout) as client:
            response = client.get("/v1/policies", params={"topic": topic} if topic else {})
        response.raise_for_status()
        return {"source": "daemon", "policies": response.json()}
    except (httpx.ConnectError, httpx.TimeoutException, OSError, httpx.HTTPStatusError):
        from policy import db as policy_db

        conn = policy_db.connect(paths.pillar_db_path("policy"))
        policies = policy_db.list_policies(conn, topic=topic)
        conn.close()
        return {"source": "fallback", "policies": [p.model_dump(mode="json") for p in policies]}


def policy_get(paths: Paths, policy_id: str, *, timeout: float = 5.0) -> dict:
    try:
        with client_for_socket(paths.socket_path("policy"), timeout=timeout) as client:
            response = client.get(f"/v1/policies/{policy_id}")
        response.raise_for_status()
        return {"source": "daemon", "policy": response.json()}
    except (httpx.ConnectError, httpx.TimeoutException, OSError, httpx.HTTPStatusError):
        from policy import db as policy_db

        conn = policy_db.connect(paths.pillar_db_path("policy"))
        policy = policy_db.get_policy(conn, policy_id)
        conn.close()
        return {"source": "fallback", "policy": policy.model_dump(mode="json") if policy else None}


def policy_winning(paths: Paths, topic: str, project_id: str, *, timeout: float = 10.0) -> dict:
    try:
        with client_for_socket(paths.socket_path("policy"), timeout=timeout) as client:
            response = client.get("/v1/policies/winning", params={"topic": topic, "project_id": project_id})
        response.raise_for_status()
        return response.json()
    except (httpx.ConnectError, httpx.TimeoutException, OSError, httpx.HTTPStatusError) as exc:
        raise PolicyPillarUnavailable(
            "Policy & Compliance daemon unreachable — winning-policy resolution needs project-set membership "
            "live from Registry & Portfolio, and has no local fallback"
        ) from exc


def policy_check_results(paths: Paths, policy_id: str | None = None, project_id: str | None = None, *, timeout: float = 5.0) -> dict:
    params = {k: v for k, v in {"policy_id": policy_id, "project_id": project_id}.items() if v}
    try:
        with client_for_socket(paths.socket_path("policy"), timeout=timeout) as client:
            response = client.get("/v1/policy_check_results", params=params)
        response.raise_for_status()
        return {"source": "daemon", "results": response.json()}
    except (httpx.ConnectError, httpx.TimeoutException, OSError, httpx.HTTPStatusError):
        from policy import db as policy_db

        conn = policy_db.connect(paths.pillar_db_path("policy"))
        results = policy_db.list_check_results(conn, policy_id=policy_id, project_id=project_id)
        conn.close()
        return {"source": "fallback", "results": [r.model_dump(mode="json") for r in results]}


def policy_list_projects(paths: Paths, policy_id: str | None = None, project_id: str | None = None, *, timeout: float = 5.0) -> dict:
    params = {k: v for k, v in {"policy_id": policy_id, "project_id": project_id}.items() if v}
    try:
        with client_for_socket(paths.socket_path("policy"), timeout=timeout) as client:
            response = client.get("/v1/policy_projects", params=params)
        response.raise_for_status()
        return {"source": "daemon", "projects": response.json()}
    except (httpx.ConnectError, httpx.TimeoutException, OSError, httpx.HTTPStatusError):
        from policy import db as policy_db

        conn = policy_db.connect(paths.pillar_db_path("policy"))
        rows = policy_db.list_policy_projects(conn, policy_id=policy_id, project_id=project_id)
        conn.close()
        return {"source": "fallback", "projects": rows}


def policy_list_drafts(paths: Paths, status: str | None = None, *, timeout: float = 5.0) -> dict:
    params = {"status": status} if status else {}
    try:
        with client_for_socket(paths.socket_path("policy"), timeout=timeout) as client:
            response = client.get("/v1/policy_drafts", params=params)
        response.raise_for_status()
        return {"source": "daemon", "drafts": response.json()}
    except (httpx.ConnectError, httpx.TimeoutException, OSError, httpx.HTTPStatusError):
        from policy import db as policy_db
        from policy.models import DraftStatus

        conn = policy_db.connect(paths.pillar_db_path("policy"))
        drafts = policy_db.list_drafts(conn, status=DraftStatus(status) if status else None)
        conn.close()
        return {"source": "fallback", "drafts": [d.model_dump(mode="json") for d in drafts]}


def policy_gating_for_job(paths: Paths, project_id: str, categories: list[str], *, timeout: float = 10.0) -> dict:
    try:
        with client_for_socket(paths.socket_path("policy"), timeout=timeout) as client:
            response = client.get("/v1/policies/gating-for-job", params={"project_id": project_id, "categories": ",".join(categories)})
        response.raise_for_status()
        return {"project_id": project_id, "categories": categories, "policies": response.json()}
    except (httpx.ConnectError, httpx.TimeoutException, OSError, httpx.HTTPStatusError) as exc:
        raise PolicyPillarUnavailable(
            "Policy & Compliance daemon unreachable — gating resolution needs project-set membership "
            "live from Registry & Portfolio, and has no local fallback"
        ) from exc


class PolicyEvaluationRefused(Exception):
    """Policy & Compliance refused the request itself (an unknown gate point, a checkout
    that is not the project's): nothing ran."""


def policy_evaluate(
    paths: Paths, project_id: str, gate_point: str, *, commit: dict | None = None, checkout: str | None = None,
    timeout: float = 1900.0,
) -> dict:
    """Run, now, every policy gating `gate_point` for the project, in the sandbox, and
    return each verdict. Nothing is recorded or held. Long timeout: a branch_finish
    preflight includes policies that run the project's own test suite."""
    body: dict = {"project_id": project_id, "gate_point": gate_point}
    if commit is not None:
        body["commit"] = commit
    if checkout is not None:
        body["checkout"] = checkout
    try:
        with client_for_socket(paths.socket_path("policy"), timeout=timeout) as client:
            response = client.post("/v1/policies/evaluate", json=body)
    except (httpx.ConnectError, httpx.TimeoutException, OSError) as exc:
        raise PolicyPillarUnavailable(
            "Policy & Compliance daemon unreachable -- policies only run in its sandbox"
        ) from exc
    if response.status_code == 400:
        try:
            detail = response.json().get("detail", response.text)
        except ValueError:
            detail = response.text
        raise PolicyEvaluationRefused(str(detail))
    if response.status_code in (404, 405):
        raise PolicyPillarUnavailable(
            "the running Policy & Compliance pillar predates policy evaluate; redeploy it "
            "(workshop_deployment_redeploy_pillar policy)"
        )
    if response.status_code != 200:
        raise PolicyPillarUnavailable(f"Policy & Compliance answered HTTP {response.status_code}")
    return response.json()


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


def complete_orchestration_job(
    paths: Paths, job_id: str, *, succeeded: bool, result: dict | None = None, error: str | None = None,
    timeout: float = 10.0,
) -> bool:
    """Reports a long-running ("accepted") Job's outcome back to
    Orchestration — used by M5's dispatched self-redeploy worker, which
    runs entirely outside the normal request/response of the Job that
    triggered it. Best-effort: a failure here is logged by the caller,
    never raised, since the redeploy itself already happened either way."""
    from common.pillar_socket import resolve_pillar_socket

    socket_path = resolve_pillar_socket(paths, "orchestration", timeout=timeout)
    if socket_path is None:
        return False
    try:
        with client_for_socket(socket_path, timeout=timeout) as client:
            response = client.post(
                f"/v1/jobs/{job_id}/complete", json={"succeeded": succeeded, "result": result, "error": error}
            )
        return response.status_code == 200
    except (httpx.ConnectError, httpx.TimeoutException, OSError):
        return False


class AgentPillarUnavailable(Exception):
    pass


def usage_candidates(paths: Paths, *, state: str = "open", limit: int | None = None, timeout: float = 10.0) -> list:
    try:
        with client_for_socket(paths.socket_path("usage"), timeout=timeout) as client:
            response = client.get("/v1/candidates", params={"state": state, **({"limit": limit} if limit is not None else {})})
        response.raise_for_status()
        return response.json()
    except (httpx.ConnectError, httpx.TimeoutException, OSError, httpx.HTTPStatusError) as exc:
        raise UsagePillarUnavailable(f"the Usage & Efficiency daemon is unreachable: {exc}") from exc


def trigger_request(paths: Paths, method: str, path: str = "", *, json: dict | None = None, timeout: float = 10.0):
    """One call to Orchestration's `/v1/triggers` routes; a refusal's reason is in the error."""
    try:
        with client_for_socket(paths.socket_path("orchestration"), timeout=timeout) as client:
            response = client.request(method, f"/v1/triggers{path}", json=json)
        if response.status_code in (404, 422):
            raise DaemonUnavailable(f"refused: {response.json().get('detail', response.text)}")
        response.raise_for_status()
        return response.json()
    except (httpx.ConnectError, httpx.TimeoutException, OSError, httpx.HTTPStatusError) as exc:
        raise DaemonUnavailable(f"the Orchestration daemon is unreachable: {exc}") from exc


def usage_findings(paths: Paths, *, project: str | None = None, state: str = "open", timeout: float = 10.0) -> list:
    params = {"state": state, **({"project": project} if project else {})}
    try:
        with client_for_socket(paths.socket_path("usage"), timeout=timeout) as client:
            response = client.get("/v1/findings", params=params)
        response.raise_for_status()
        return response.json()
    except (httpx.ConnectError, httpx.TimeoutException, OSError, httpx.HTTPStatusError) as exc:
        raise UsagePillarUnavailable(f"the Usage & Efficiency daemon is unreachable: {exc}") from exc


class UsagePillarUnavailable(Exception):
    pass


def usage_summary(paths: Paths, *, project: str | None = None, week: str | None = None, origin: str | None = None,
                  timeout: float = 20.0) -> dict:
    params = {key: value for key, value in (("project", project), ("week", week), ("origin", origin)) if value}
    try:
        with client_for_socket(paths.socket_path("usage"), timeout=timeout) as client:
            response = client.get("/v1/summary", params=params)
        response.raise_for_status()
        return response.json()
    except (httpx.ConnectError, httpx.TimeoutException, OSError, httpx.HTTPStatusError) as exc:
        raise UsagePillarUnavailable(f"the Usage & Efficiency daemon is unreachable: {exc}") from exc


def agent_runs(paths: Paths, project_id: str, *, status: str | None = None, timeout: float = 5.0) -> dict:
    try:
        with client_for_socket(paths.socket_path("agent"), timeout=timeout) as client:
            response = client.get(f"/v1/runs/{project_id}", params={"status": status} if status else {})
        response.raise_for_status()
        return response.json()
    except (httpx.ConnectError, httpx.TimeoutException, OSError, httpx.HTTPStatusError) as exc:
        raise AgentPillarUnavailable("Agentic Development daemon unreachable") from exc


def agent_run_status(paths: Paths, project_id: str, run_id: str, *, timeout: float = 5.0) -> dict:
    try:
        with client_for_socket(paths.socket_path("agent"), timeout=timeout) as client:
            response = client.get(f"/v1/runs/{project_id}/{run_id}")
        response.raise_for_status()
        return response.json()
    except (httpx.ConnectError, httpx.TimeoutException, OSError, httpx.HTTPStatusError) as exc:
        raise AgentPillarUnavailable("Agentic Development daemon unreachable") from exc


def agent_ci_checks(paths: Paths, project_id: str, *, timeout: float = 5.0) -> dict:
    try:
        with client_for_socket(paths.socket_path("agent"), timeout=timeout) as client:
            response = client.get(f"/v1/ci_checks/{project_id}")
        response.raise_for_status()
        return {"ci_checks": response.json()}
    except (httpx.ConnectError, httpx.TimeoutException, OSError, httpx.HTTPStatusError) as exc:
        raise AgentPillarUnavailable("Agentic Development daemon unreachable") from exc


def agent_latest_ci_check(paths: Paths, project_id: str, *, branch: str | None = None, timeout: float = 5.0) -> dict:
    try:
        with client_for_socket(paths.socket_path("agent"), timeout=timeout) as client:
            response = client.get(f"/v1/ci_checks/{project_id}/latest", params={"branch": branch} if branch else {})
        if response.status_code == 404:
            return {"ci_check": None}
        response.raise_for_status()
        return {"ci_check": response.json()}
    except (httpx.ConnectError, httpx.TimeoutException, OSError, httpx.HTTPStatusError) as exc:
        raise AgentPillarUnavailable("Agentic Development daemon unreachable") from exc


def agent_session_status(paths: Paths, project_id: str, *, timeout: float = 5.0) -> dict:
    try:
        with client_for_socket(paths.socket_path("agent"), timeout=timeout) as client:
            response = client.get(f"/v1/sessions/{project_id}")
        response.raise_for_status()
        return response.json()
    except (httpx.ConnectError, httpx.TimeoutException, OSError, httpx.HTTPStatusError) as exc:
        raise AgentPillarUnavailable("Agentic Development daemon unreachable") from exc


def pump(paths: Paths, *, timeout: float = 10.0) -> dict:
    try:
        with client_for_socket(paths.socket_path("orchestration"), timeout=timeout) as client:
            response = client.post("/v1/pump")
        response.raise_for_status()
        return {"dispatched": response.json()}
    except (httpx.ConnectError, httpx.TimeoutException, OSError, httpx.HTTPStatusError) as exc:
        raise DaemonUnavailable("Orchestration daemon unreachable") from exc


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
