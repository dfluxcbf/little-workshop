"""mcp_server.client's HTTP glue, exercised against real (but unbound)
Unix sockets — the same "no daemon listening, real ECONNREFUSED" style
`tests/unit/test_cli.py` already uses for `cli.client`, since Core itself
is unreachable when nothing is listening on `paths.core_socket_path`."""

import pytest

from common.paths import Paths
from mcp_server import client


@pytest.fixture
def paths(tmp_path):
    return Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")


def test_core_status_raises_when_core_unreachable(paths):
    with pytest.raises(client.PillarUnavailable):
        client.core_status(paths)


def test_create_job_raises_when_orchestration_unreachable(paths):
    with pytest.raises(client.PillarUnavailable):
        client.create_job(paths, type="noop", target_pillar="reference")


def test_list_jobs_raises_when_orchestration_unreachable(paths):
    with pytest.raises(client.PillarUnavailable):
        client.list_jobs(paths)


def test_job_status_raises_when_orchestration_unreachable(paths):
    with pytest.raises(client.PillarUnavailable):
        client.job_status(paths, "some-id")


def test_list_projects_raises_when_registry_unreachable(paths):
    with pytest.raises(client.PillarUnavailable):
        client.list_projects(paths)


def test_get_project_raises_when_registry_unreachable(paths):
    with pytest.raises(client.PillarUnavailable):
        client.get_project(paths, "little-widget")


def test_version_status_raises_when_version_unreachable(paths):
    with pytest.raises(client.PillarUnavailable):
        client.version_status(paths, "feature", "little-widget")


def test_deployment_status_raises_when_deployment_unreachable(paths):
    with pytest.raises(client.PillarUnavailable):
        client.deployment_status(paths, "little-widget")


def test_agent_runs_raises_when_agent_unreachable(paths):
    with pytest.raises(client.PillarUnavailable):
        client.agent_runs(paths, "little-widget")


# ---------------------------------------------------------------------------
# Success paths — daemon reachable, real HTTP-over-socket round trip via
# socket_test_helpers' ASGI substitution (real Core discovery + a real
# target-pillar app, not a mocked transport).
# ---------------------------------------------------------------------------

from fastapi import FastAPI

from socket_test_helpers import build_fake_core_app, patch_core_discovery, patch_socket_apps


def test_core_status_success_returns_daemon_response(paths, monkeypatch):
    core_app = build_fake_core_app({"registry": paths.socket_path("registry")})
    patch_socket_apps(monkeypatch, client, {paths.core_socket_path: core_app})
    result = client.core_status(paths)
    assert result["pillars"]["registry"]["status"] == "healthy"


def test_create_job_success_returns_daemon_response(paths, monkeypatch):
    orch_app = FastAPI()

    @orch_app.post("/v1/jobs")
    def create(request: dict) -> dict:
        return {"id": "j1", "status": "succeeded", "type": request["type"]}

    patch_core_discovery(monkeypatch, paths, {"orchestration": paths.socket_path("orchestration")})
    patch_socket_apps(monkeypatch, client, {paths.socket_path("orchestration"): orch_app})

    result = client.create_job(paths, type="project.bootstrap", target_pillar="registry")
    assert result == {"id": "j1", "status": "succeeded", "type": "project.bootstrap"}


def _rig(paths, monkeypatch, pillar: str, app: FastAPI):
    patch_core_discovery(monkeypatch, paths, {pillar: paths.socket_path(pillar)})
    patch_socket_apps(monkeypatch, client, {paths.socket_path(pillar): app})


def test_list_jobs_success(paths, monkeypatch):
    app = FastAPI()

    @app.get("/v1/jobs")
    def list_jobs(status: str | None = None) -> list[dict]:
        return [{"id": "j1", "status": status}]

    _rig(paths, monkeypatch, "orchestration", app)
    assert client.list_jobs(paths, status="queued") == [{"id": "j1", "status": "queued"}]


def test_job_status_success(paths, monkeypatch):
    app = FastAPI()

    @app.get("/v1/jobs/{job_id}")
    def get_job(job_id: str) -> dict:
        return {"id": job_id}

    _rig(paths, monkeypatch, "orchestration", app)
    assert client.job_status(paths, "j1") == {"id": "j1"}


def test_list_projects_success(paths, monkeypatch):
    app = FastAPI()

    @app.get("/v1/projects")
    def list_projects(status: str | None = None) -> list[dict]:
        return [{"name": "little-widget"}]

    _rig(paths, monkeypatch, "registry", app)
    assert client.list_projects(paths) == [{"name": "little-widget"}]


def test_get_project_success(paths, monkeypatch):
    app = FastAPI()

    @app.get("/v1/projects/{name}")
    def get_project(name: str) -> dict:
        return {"name": name}

    _rig(paths, monkeypatch, "registry", app)
    assert client.get_project(paths, "little-widget") == {"name": "little-widget"}


def test_version_status_success(paths, monkeypatch):
    app = FastAPI()

    @app.get("/v1/status/{kind}")
    def status(kind: str, project_id: str) -> dict:
        return {"kind": kind, "project_id": project_id}

    _rig(paths, monkeypatch, "version", app)
    assert client.version_status(paths, "feature", "little-widget") == {"kind": "feature", "project_id": "little-widget"}


def test_version_config_success(paths, monkeypatch):
    app = FastAPI()

    @app.get("/v1/config")
    def config(project_id: str) -> dict:
        return {"production_branch": "master"}

    _rig(paths, monkeypatch, "version", app)
    assert client.version_config(paths, "little-widget") == {"production_branch": "master"}


def test_version_release_candidates_success(paths, monkeypatch):
    app = FastAPI()

    @app.get("/v1/release/candidates")
    def candidates(project_id: str) -> dict:
        return {"candidates": [1, 2]}

    _rig(paths, monkeypatch, "version", app)
    assert client.version_release_candidates(paths, "little-widget") == {"candidates": [1, 2]}


def test_deployment_status_success(paths, monkeypatch):
    app = FastAPI()

    @app.get("/v1/status/{project_id}")
    def status(project_id: str) -> dict:
        return {"name": project_id}

    _rig(paths, monkeypatch, "deployment", app)
    assert client.deployment_status(paths, "little-meals") == {"name": "little-meals"}


def test_deployment_history_success(paths, monkeypatch):
    app = FastAPI()

    @app.get("/v1/deployments/{project_id}")
    def deployments(project_id: str) -> list[dict]:
        return [{"deploy_id": "d1"}]

    _rig(paths, monkeypatch, "deployment", app)
    assert client.deployment_history(paths, "little-meals") == [{"deploy_id": "d1"}]


def test_deployment_confirmation_success(paths, monkeypatch):
    app = FastAPI()

    @app.get("/v1/confirmations/{project_id}")
    def confirmation(project_id: str) -> dict:
        return {"confirmed": False}

    _rig(paths, monkeypatch, "deployment", app)
    assert client.deployment_confirmation(paths, "little-meals") == {"confirmed": False}


def test_deployment_branches_success(paths, monkeypatch):
    app = FastAPI()

    @app.get("/v1/branches/{project_id}")
    def branches(project_id: str) -> list[str]:
        return ["master", "dev"]

    _rig(paths, monkeypatch, "deployment", app)
    assert client.deployment_branches(paths, "little-meals") == ["master", "dev"]


def test_agent_runs_success(paths, monkeypatch):
    app = FastAPI()

    @app.get("/v1/runs/{project_id}")
    def runs(project_id: str, status: str | None = None) -> list[dict]:
        return [{"run_id": "r1", "status": status}]

    _rig(paths, monkeypatch, "agent", app)
    assert client.agent_runs(paths, "little-meals", status="running") == [{"run_id": "r1", "status": "running"}]


def test_agent_run_status_success(paths, monkeypatch):
    app = FastAPI()

    @app.get("/v1/runs/{project_id}/{run_id}")
    def run(project_id: str, run_id: str) -> dict:
        return {"run_id": run_id}

    _rig(paths, monkeypatch, "agent", app)
    assert client.agent_run_status(paths, "little-meals", "r1") == {"run_id": "r1"}


def test_get_raises_pillar_unavailable_on_http_error_status(paths, monkeypatch):
    from fastapi import HTTPException

    app = FastAPI()

    @app.get("/v1/projects/{name}")
    def get_project(name: str):
        raise HTTPException(status_code=404, detail="not found")

    _rig(paths, monkeypatch, "registry", app)
    with pytest.raises(client.PillarUnavailable):
        client.get_project(paths, "ghost")
