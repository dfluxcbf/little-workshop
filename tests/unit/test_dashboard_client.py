"""dashboard.client's HTTP glue — the same "real (but unbound) Unix
socket, real ECONNREFUSED" style test_mcp_client.py already uses for
mcp_server.client, since dashboard.client is a deliberate duplicate of
that exact pattern for a third thin proxy."""

import pytest
from fastapi import FastAPI

from common.paths import Paths
from dashboard import client
from socket_test_helpers import build_fake_core_app, patch_core_discovery, patch_socket_apps


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


def test_list_projects_raises_when_registry_unreachable(paths):
    with pytest.raises(client.PillarUnavailable):
        client.list_projects(paths)


def test_policy_list_raises_when_policy_unreachable(paths):
    with pytest.raises(client.PillarUnavailable):
        client.policy_list(paths)


def test_policy_get_raises_when_policy_unreachable(paths):
    with pytest.raises(client.PillarUnavailable):
        client.policy_get(paths, "pol1")


def test_policy_list_waivers_raises_when_policy_unreachable(paths):
    with pytest.raises(client.PillarUnavailable):
        client.policy_list_waivers(paths)


def test_deployment_status_raises_when_deployment_unreachable(paths):
    with pytest.raises(client.PillarUnavailable):
        client.deployment_status(paths, "little-widget")


def test_get_raises_when_core_knows_the_pillar_but_its_socket_is_dead(paths, monkeypatch):
    """Core's discovery succeeds (the pillar is registered healthy), but
    the actual Pillar Interface socket has nothing listening on it — a
    distinct failure mode from Core itself being unreachable."""
    patch_core_discovery(monkeypatch, paths, {"policy": paths.socket_path("policy")})
    with pytest.raises(client.PillarUnavailable):
        client.policy_list(paths)


def test_create_job_raises_when_orchestration_is_registered_but_its_socket_is_dead(paths, monkeypatch):
    patch_core_discovery(monkeypatch, paths, {"orchestration": paths.socket_path("orchestration")})
    with pytest.raises(client.PillarUnavailable):
        client.create_job(paths, type="noop", target_pillar="reference")


def test_core_status_success_returns_daemon_response(paths, monkeypatch):
    core_app = build_fake_core_app({"registry": paths.socket_path("registry")})
    patch_socket_apps(monkeypatch, client, {paths.core_socket_path: core_app})
    result = client.core_status(paths)
    assert result["pillars"]["registry"]["status"] == "healthy"


def _rig(paths, monkeypatch, pillar: str, app: FastAPI):
    patch_core_discovery(monkeypatch, paths, {pillar: paths.socket_path(pillar)})
    patch_socket_apps(monkeypatch, client, {paths.socket_path(pillar): app})


def test_create_job_success_returns_daemon_response(paths, monkeypatch):
    app = FastAPI()

    @app.post("/v1/jobs")
    def create(request: dict) -> dict:
        return {"id": "j1", "status": "succeeded", "type": request["type"]}

    _rig(paths, monkeypatch, "orchestration", app)
    result = client.create_job(paths, type="policy.configure", target_pillar="policy")
    assert result == {"id": "j1", "status": "succeeded", "type": "policy.configure"}


def test_list_jobs_success_passes_through_status_filter(paths, monkeypatch):
    app = FastAPI()

    @app.get("/v1/jobs")
    def list_jobs(status: str | None = None) -> list[dict]:
        return [{"id": "j1", "status": status}]

    _rig(paths, monkeypatch, "orchestration", app)
    result = client.list_jobs(paths, status="failed")
    assert result == [{"id": "j1", "status": "failed"}]


def test_list_projects_success(paths, monkeypatch):
    app = FastAPI()

    @app.get("/v1/projects")
    def list_projects(status: str | None = None) -> list[dict]:
        return [{"id": "p1", "name": "little-widget"}]

    _rig(paths, monkeypatch, "registry", app)
    assert client.list_projects(paths) == [{"id": "p1", "name": "little-widget"}]


def test_policy_list_success(paths, monkeypatch):
    app = FastAPI()

    @app.get("/v1/policies")
    def list_policies(topic: str | None = None) -> list[dict]:
        return [{"id": "pol1", "topic": topic}]

    _rig(paths, monkeypatch, "policy", app)
    assert client.policy_list(paths, topic="t") == [{"id": "pol1", "topic": "t"}]


def test_policy_get_success(paths, monkeypatch):
    app = FastAPI()

    @app.get("/v1/policies/{policy_id}")
    def get_policy(policy_id: str) -> dict:
        return {"id": policy_id}

    _rig(paths, monkeypatch, "policy", app)
    assert client.policy_get(paths, "pol1") == {"id": "pol1"}


def test_policy_list_waivers_success_filters_by_policy_and_project(paths, monkeypatch):
    app = FastAPI()

    @app.get("/v1/policy_waivers")
    def list_waivers(policy_id: str | None = None, project_id: str | None = None) -> list[dict]:
        return [{"policy_id": policy_id, "project_id": project_id}]

    _rig(paths, monkeypatch, "policy", app)
    result = client.policy_list_waivers(paths, policy_id="pol1", project_id="proj-a")
    assert result == [{"policy_id": "pol1", "project_id": "proj-a"}]


def test_deployment_status_success(paths, monkeypatch):
    app = FastAPI()

    @app.get("/v1/status/{project_id}")
    def status(project_id: str) -> dict:
        return {"project_id": project_id, "listening": True}

    _rig(paths, monkeypatch, "deployment", app)
    assert client.deployment_status(paths, "little-widget") == {"project_id": "little-widget", "listening": True}


# -- per-project panels (mission-control Dashboard) --------------------------


def test_optional_reads_treat_404_as_nothing_there_not_an_outage(paths, monkeypatch):
    from fastapi import HTTPException

    app = FastAPI()

    @app.get("/v1/status/{project_id}")
    def status(project_id: str) -> dict:
        raise HTTPException(status_code=404, detail="no manifest")

    _rig(paths, monkeypatch, "deployment", app)
    assert client.deployment_status_optional(paths, "little-widget") is None


def test_optional_reads_return_the_body_when_present(paths, monkeypatch):
    app = FastAPI()

    @app.get("/v1/status/{project_id}")
    def status(project_id: str) -> dict:
        return {"name": project_id, "listening": True}

    _rig(paths, monkeypatch, "deployment", app)
    assert client.deployment_status_optional(paths, "little-widget") == {"name": "little-widget", "listening": True}


def test_optional_reads_still_raise_when_the_pillar_is_down(paths):
    with pytest.raises(client.PillarUnavailable):
        client.deployment_status_optional(paths, "little-widget")
    with pytest.raises(client.PillarUnavailable):
        client.agent_run(paths, "little-widget", "r1")
    with pytest.raises(client.PillarUnavailable):
        client.agent_latest_ci_check(paths, "little-widget")


def test_deployment_branches_degrades_to_empty_when_deployment_is_down(paths):
    assert client.deployment_branches(paths, "little-widget") == []


def test_agent_reads_and_requirements_report(paths, monkeypatch):
    agent_app = FastAPI()

    @agent_app.get("/v1/sessions/{project_id}")
    def session(project_id: str) -> dict:
        return {"project_id": project_id, "running": False, "trusted": True}

    @agent_app.get("/v1/runs/{project_id}")
    def runs(project_id: str) -> list:
        return [{"id": "r1"}]

    _rig(paths, monkeypatch, "agent", agent_app)
    assert client.agent_session_status(paths, "p")["trusted"] is True
    assert client.agent_runs(paths, "p") == [{"id": "r1"}]

    req_app = FastAPI()

    @req_app.get("/v1/report")
    def report(project_id: str) -> dict:
        return {"requirements": [], "done_count": 0}

    _rig(paths, monkeypatch, "requirements", req_app)
    assert client.requirements_report(paths, "p")["done_count"] == 0


def test_session_name_matches_the_agent_pillars_naming():
    assert client.session_name_for("little-widget") == "lws-little-widget-session"
    assert client.session_name_for("dfluxcbf/little-widget") == "lws-little-widget-session"
