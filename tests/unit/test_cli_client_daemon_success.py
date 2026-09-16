"""cli.client's real-daemon success paths — the "source": "daemon" /
real-response branch of every function, exercised against a real FastAPI
app on a real Unix domain socket (an in-process uvicorn thread, not
systemd — the same technique tests/integration's real end-to-end proofs
use, scaled down for a unit test). The daemon-*unreachable* branch is
already covered for real (a genuinely unbound socket, real ECONNREFUSED)
by tests/unit/test_cli.py; this file is the other half.

One generic stub app is bound to every pillar's socket path in turn —
its routes don't care which pillar "role" a given socket represents, so
one small app can stand in for Core/Orchestration/Registry/Version/
Deployment/Agent's Pillar Interfaces for the purposes of proving
cli.client's own HTTP-call/response-shaping logic runs the real path.
"""

from __future__ import annotations

import threading
import time

import pytest
import uvicorn
from fastapi import FastAPI

from cli import client
from common.paths import Paths


def _make_stub_app(paths: Paths | None = None) -> FastAPI:
    app = FastAPI()

    @app.get("/v1/status")
    def core_status():
        if paths is None:
            return {"pillars": {"registry": {"status": "active"}}}
        return {
            "pillars": {
                pillar: {"status": "active", "socket_path": str(paths.socket_path(pillar))}
                for pillar in ("orchestration", "registry", "version", "deployment", "agent")
            }
        }

    @app.post("/v1/jobs")
    def create_job(body: dict):
        return {"id": "j1", "type": body["type"], "status": "succeeded"}

    @app.get("/v1/jobs")
    def list_jobs():
        return [{"id": "j1", "status": "succeeded"}]

    @app.get("/v1/jobs/{job_id}")
    def job_status(job_id: str):
        return {"id": job_id, "status": "succeeded"}

    @app.post("/v1/jobs/{job_id}/complete")
    def complete_job(job_id: str, body: dict):
        return {"id": job_id, "status": "succeeded" if body.get("succeeded") else "failed"}

    @app.get("/v1/projects")
    def list_projects():
        return [{"name": "little-widget"}]

    @app.get("/v1/status/{segment}")
    def status_by_segment(segment: str):
        return {"success": True, "message": "", "data": {"segment": segment}, "name": segment, "listening": True}

    @app.get("/v1/config")
    def config():
        return {"production_branch": "master", "integration_branch": "dev"}

    @app.get("/v1/release/candidates")
    def release_candidates():
        return {"candidates": [1, 2]}

    @app.get("/v1/deployments/{project_id}")
    def deployments(project_id: str):
        return [{"project_id": project_id}]

    @app.get("/v1/confirmations/{project_id}")
    def confirmations(project_id: str):
        return {"project_id": project_id, "state": "confirmed"}

    @app.get("/v1/branches/{project_id}")
    def branches(project_id: str):
        return ["dev", "master"]

    @app.post("/v1/worktrees")
    def provision_worktree(body: dict):
        return {"worktree_id": "w1", "path": "/tmp/w1"}

    @app.delete("/v1/worktrees/{worktree_id}")
    def release_worktree(worktree_id: str, project_id: str):
        return {"released": worktree_id}

    @app.post("/v1/rollback-timeout/{project_id}")
    def rollback_timeout(project_id: str, body: dict):
        return {"rolled_back": False, "message": "not due yet"}

    @app.get("/v1/runs/{project_id}")
    def list_runs(project_id: str):
        return [{"run_id": "r1"}]

    @app.get("/v1/runs/{project_id}/{run_id}")
    def get_run(project_id: str, run_id: str):
        return {"run_id": run_id, "status": "finished"}

    return app


class _UvicornThread:
    def __init__(self, app, socket_path):
        self._config = uvicorn.Config(app, uds=str(socket_path), log_level="warning")
        self._server = uvicorn.Server(self._config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)

    def start(self) -> None:
        self._thread.start()
        for _ in range(100):
            if getattr(self._server, "started", False):
                return
            time.sleep(0.05)
        raise RuntimeError("test server did not start in time")

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=5)


@pytest.fixture
def paths_with_live_daemons(tmp_path):
    import tempfile
    import shutil
    from pathlib import Path

    xdg_state = Path(tempfile.mkdtemp(prefix="lwsut-"))
    paths = Paths(config_home=tmp_path / "config", state_home=xdg_state / "little-workshop")
    paths.ensure_layout()

    app = _make_stub_app(paths)
    servers = []
    for pillar in ("core", "orchestration", "registry", "version", "deployment", "agent"):
        socket_path = paths.core_socket_path if pillar == "core" else paths.socket_path(pillar)
        server = _UvicornThread(app, socket_path)
        server.start()
        servers.append(server)

    try:
        yield paths
    finally:
        for server in servers:
            server.stop()
        shutil.rmtree(xdg_state, ignore_errors=True)


def test_core_status_reports_daemon_source(paths_with_live_daemons):
    result = client.core_status(paths_with_live_daemons)
    assert result["source"] == "daemon"
    assert result["pillars"]["registry"]["status"] == "active"


def test_create_job_hits_the_real_orchestration_socket(paths_with_live_daemons):
    result = client.create_job(paths_with_live_daemons, type="noop", target_pillar="reference")
    assert result["status"] == "succeeded"


def test_list_jobs_reports_daemon_source(paths_with_live_daemons):
    result = client.list_jobs(paths_with_live_daemons)
    assert result["source"] == "daemon"
    assert result["jobs"][0]["id"] == "j1"


def test_job_status_reports_daemon_source(paths_with_live_daemons):
    result = client.job_status(paths_with_live_daemons, "j1")
    assert result["source"] == "daemon"
    assert result["job"]["id"] == "j1"


def test_list_projects_reports_daemon_source(paths_with_live_daemons):
    result = client.list_projects(paths_with_live_daemons)
    assert result["source"] == "daemon"
    assert result["projects"][0]["name"] == "little-widget"


def test_version_status_hits_the_real_version_socket(paths_with_live_daemons):
    result = client.version_status(paths_with_live_daemons, "feature", "little-widget")
    assert result["data"]["segment"] == "feature"


def test_version_config_hits_the_real_version_socket(paths_with_live_daemons):
    result = client.version_config(paths_with_live_daemons, "little-widget")
    assert result["production_branch"] == "master"


def test_version_release_candidates_hits_the_real_version_socket(paths_with_live_daemons):
    result = client.version_release_candidates(paths_with_live_daemons, "little-widget")
    assert result["candidates"] == [1, 2]


def test_deployment_status_hits_the_real_deployment_socket(paths_with_live_daemons):
    result = client.deployment_status(paths_with_live_daemons, "little-widget")
    assert result["listening"] is True


def test_deployment_history_hits_the_real_deployment_socket(paths_with_live_daemons):
    result = client.deployment_history(paths_with_live_daemons, "little-widget")
    assert result[0]["project_id"] == "little-widget"


def test_deployment_confirmation_hits_the_real_deployment_socket(paths_with_live_daemons):
    result = client.deployment_confirmation(paths_with_live_daemons, "little-widget")
    assert result["state"] == "confirmed"


def test_deployment_branches_hits_the_real_deployment_socket(paths_with_live_daemons):
    result = client.deployment_branches(paths_with_live_daemons, "little-widget")
    assert result == ["dev", "master"]


def test_provision_worktree_hits_the_real_deployment_socket(paths_with_live_daemons):
    result = client.provision_worktree(paths_with_live_daemons, "little-widget", "HEAD")
    assert result["worktree_id"] == "w1"


def test_release_worktree_hits_the_real_deployment_socket(paths_with_live_daemons):
    result = client.release_worktree(paths_with_live_daemons, "little-widget", "w1")
    assert result["released"] == "w1"


def test_deployment_rollback_timeout_hits_the_real_deployment_socket(paths_with_live_daemons):
    result = client.deployment_rollback_timeout(paths_with_live_daemons, "little-widget", "d1")
    assert result["rolled_back"] is False


def test_agent_runs_hits_the_real_agent_socket(paths_with_live_daemons):
    result = client.agent_runs(paths_with_live_daemons, "little-widget")
    assert result[0]["run_id"] == "r1"


def test_agent_run_status_hits_the_real_agent_socket(paths_with_live_daemons):
    result = client.agent_run_status(paths_with_live_daemons, "little-widget", "r1")
    assert result["status"] == "finished"


def test_complete_orchestration_job_reports_true_on_success(paths_with_live_daemons):
    assert client.complete_orchestration_job(paths_with_live_daemons, "j1", succeeded=True, result={"ok": True}) is True


def test_complete_orchestration_job_reports_false_when_core_cannot_resolve_orchestration(tmp_path):
    """No daemon at all is listening here (not even Core) — resolve_pillar_socket
    returns None, and the function degrades to a quiet `False`, never raising:
    a best-effort callback, per its own docstring."""
    paths = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    assert client.complete_orchestration_job(paths, "j1", succeeded=True) is False


# ---------------------------------------------------------------------------
# _core_status_fallback: pure logic, no socket needed at all
# ---------------------------------------------------------------------------

def test_core_status_fallback_reports_disabled_for_a_disabled_pillar(tmp_path):
    paths = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    result = client._core_status_fallback(paths)
    assert result["build"]["status"] == "disabled"


def test_core_status_fallback_reports_not_installed_for_an_enabled_pillar_with_no_command(tmp_path):
    paths = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    result = client._core_status_fallback(paths)
    assert result["orchestration"]["status"] == "not_installed"


def test_core_status_fallback_reports_inactive_for_an_enabled_pillar_whose_unit_is_down(tmp_path, monkeypatch):
    from common.manifest import Manifest, PillarEntry

    paths = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    manifest = Manifest(pillars={"orchestration": PillarEntry(name="orchestration", enabled=True, command=["python3", "-m", "orchestration"])})
    monkeypatch.setattr(client, "load_manifest", lambda path: manifest)
    monkeypatch.setattr(client.systemd, "is_active", lambda name: False)
    result = client._core_status_fallback(paths)
    assert result["orchestration"]["status"] == "inactive"


def test_core_status_fallback_reports_active_unverified_for_an_enabled_pillar_whose_unit_is_up(tmp_path, monkeypatch):
    from common.manifest import Manifest, PillarEntry

    paths = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    manifest = Manifest(pillars={"orchestration": PillarEntry(name="orchestration", enabled=True, command=["python3", "-m", "orchestration"])})
    monkeypatch.setattr(client, "load_manifest", lambda path: manifest)
    monkeypatch.setattr(client.systemd, "is_active", lambda name: True)
    result = client._core_status_fallback(paths)
    assert "active" in result["orchestration"]["status"]
