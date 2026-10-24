"""The real `ReleaseServices` for `version.release`: every other pillar it needs,
reached over each one's own Pillar Interface (never a Python import of another
pillar's code). Sub-work — test runs, policy checks, relaunches — goes through
Orchestration as ordinary Jobs, so it shows on the Jobs page like any other."""

from __future__ import annotations

import time

import httpx

from common.paths import Paths
from common.pillar_socket import resolve_pillar_socket
from common.uds_client import client_for_socket

_TERMINAL = ("succeeded", "failed", "cancelled", "stopped")
POLL_SECONDS = 5.0


class ServiceUnavailable(Exception):
    pass


class PillarReleaseServices:
    def __init__(self, paths: Paths, *, created_by: str = "version.release", poll_seconds: float = POLL_SECONDS) -> None:
        self.paths = paths
        self.created_by = created_by
        self.poll_seconds = poll_seconds

    def _call(self, pillar: str, method: str, path: str, *, timeout: float = 30.0, **kwargs) -> httpx.Response:
        socket_path = resolve_pillar_socket(self.paths, pillar, timeout=5.0)
        if socket_path is None:
            raise ServiceUnavailable(f"the {pillar} pillar is unavailable")
        try:
            with client_for_socket(socket_path, timeout=timeout) as client:
                return client.request(method, path, **kwargs)
        except (httpx.ConnectError, httpx.TimeoutException, OSError) as exc:
            raise ServiceUnavailable(f"could not reach the {pillar} pillar: {exc}") from exc

    def _json(self, pillar: str, method: str, path: str, **kwargs):
        response = self._call(pillar, method, path, **kwargs)
        if response.status_code >= 400:
            raise ServiceUnavailable(f"{pillar} {method} {path}: HTTP {response.status_code} {response.text[:300]}")
        return response.json()

    def create_job(self, template: dict) -> str:
        body = {"depends_on": [], "queue_key": None, "created_by": self.created_by, **template}
        return self._json("orchestration", "POST", "/v1/jobs", json=body)["id"]

    def wait_for_job(self, job_id: str, timeout: float) -> dict:
        deadline = time.monotonic() + timeout
        while True:
            try:
                job = self._json("orchestration", "GET", f"/v1/jobs/{job_id}")
            except ServiceUnavailable:
                job = None  # a blip while waiting is not an answer; keep asking until the deadline
            if job is not None and job.get("status") in _TERMINAL:
                return job
            if time.monotonic() >= deadline:
                return {"id": job_id, "status": (job or {}).get("status", "unknown"), "error": f"still not finished after {timeout:.0f}s"}
            time.sleep(self.poll_seconds)

    def test_suites(self, project_id: str) -> list[dict]:
        return self._json("requirements", "GET", "/v1/test_suites", params={"project_id": project_id})

    def requirements_report(self, project_id: str) -> dict:
        return self._json("requirements", "GET", "/v1/report", params={"project_id": project_id})

    def policies(self) -> list[dict]:
        return self._json("policy", "GET", "/v1/policies")

    def deployment_status(self, project_id: str) -> dict | None:
        response = self._call("deployment", "GET", f"/v1/status/{project_id}")
        if response.status_code == 404:
            return None  # no server manifest: nothing to relaunch
        if response.status_code >= 400:
            raise ServiceUnavailable(f"deployment status for {project_id}: HTTP {response.status_code}")
        return response.json()

    def pillars(self) -> dict[str, dict]:
        try:
            with client_for_socket(self.paths.core_socket_path, timeout=10.0) as client:
                response = client.get("/v1/status")
            response.raise_for_status()
        except (httpx.ConnectError, httpx.TimeoutException, OSError, httpx.HTTPStatusError) as exc:
            raise ServiceUnavailable(f"could not reach Core: {exc}") from exc
        return response.json().get("pillars", {})

    def complete(self, job_id: str, *, succeeded: bool, result: dict | None, error: str | None) -> bool:
        """Best-effort, like every other async Job's completion report; True once Orchestration answered
        (a Job it no longer has, or already settled, counts: there is nothing more to tell it)."""
        try:
            response = self._call("orchestration", "POST", f"/v1/jobs/{job_id}/complete",
                                  json={"succeeded": succeeded, "result": result, "error": error})
        except ServiceUnavailable:
            return False
        return response.status_code < 500
