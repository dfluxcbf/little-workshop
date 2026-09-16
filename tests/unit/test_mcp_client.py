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


def test_stop_job_success(paths, monkeypatch):
    app = FastAPI()

    @app.post("/v1/jobs/{job_id}/stop")
    def stop(job_id: str) -> dict:
        return {"stopped": True, "reason": None}

    _rig(paths, monkeypatch, "orchestration", app)
    assert client.stop_job(paths, "j1") == {"stopped": True, "reason": None}


def test_create_scheduled_task_success(paths, monkeypatch):
    app = FastAPI()

    @app.post("/v1/scheduled_tasks")
    def create(body: dict) -> dict:
        return {"id": "s1", "name": body["name"]}

    _rig(paths, monkeypatch, "orchestration", app)
    result = client.create_scheduled_task(
        paths, name="nightly", job_template={"type": "noop", "target_pillar": "reference"},
        schedule={"kind": "once", "at": "2030-01-01T00:00:00"},
    )
    assert result == {"id": "s1", "name": "nightly"}


def test_list_scheduled_tasks_success(paths, monkeypatch):
    app = FastAPI()

    @app.get("/v1/scheduled_tasks")
    def list_tasks() -> list[dict]:
        return [{"id": "s1"}]

    _rig(paths, monkeypatch, "orchestration", app)
    assert client.list_scheduled_tasks(paths) == [{"id": "s1"}]


def test_get_scheduled_task_success(paths, monkeypatch):
    app = FastAPI()

    @app.get("/v1/scheduled_tasks/{task_id}")
    def get(task_id: str) -> dict:
        return {"id": task_id}

    _rig(paths, monkeypatch, "orchestration", app)
    assert client.get_scheduled_task(paths, "s1") == {"id": "s1"}


def test_edit_scheduled_task_success(paths, monkeypatch):
    app = FastAPI()

    @app.patch("/v1/scheduled_tasks/{task_id}")
    def edit(task_id: str, body: dict) -> dict:
        return {"id": task_id, **body}

    _rig(paths, monkeypatch, "orchestration", app)
    assert client.edit_scheduled_task(paths, "s1", name="renamed") == {"id": "s1", "name": "renamed"}


def test_remove_scheduled_task_success(paths, monkeypatch):
    app = FastAPI()

    @app.delete("/v1/scheduled_tasks/{task_id}")
    def remove(task_id: str) -> dict:
        return {"id": task_id, "removed": True}

    _rig(paths, monkeypatch, "orchestration", app)
    assert client.remove_scheduled_task(paths, "s1") == {"id": "s1", "removed": True}


def test_trigger_scheduled_task_success(paths, monkeypatch):
    app = FastAPI()

    @app.post("/v1/scheduled_tasks/{task_id}/trigger")
    def trigger(task_id: str) -> dict:
        return {"id": "j1", "created_by": "scheduler"}

    _rig(paths, monkeypatch, "orchestration", app)
    assert client.trigger_scheduled_task(paths, "s1") == {"id": "j1", "created_by": "scheduler"}


def test_cancel_scheduled_task_success(paths, monkeypatch):
    app = FastAPI()

    @app.post("/v1/scheduled_tasks/{task_id}/cancel")
    def cancel(task_id: str) -> dict:
        return {"cancelled": "next_occurrence"}

    _rig(paths, monkeypatch, "orchestration", app)
    assert client.cancel_scheduled_task(paths, "s1") == {"cancelled": "next_occurrence"}


def test_postpone_scheduled_task_success(paths, monkeypatch):
    app = FastAPI()

    @app.post("/v1/scheduled_tasks/{task_id}/postpone")
    def postpone(task_id: str, body: dict) -> dict:
        return {"id": task_id, "next_run_at": body["at"]}

    _rig(paths, monkeypatch, "orchestration", app)
    result = client.postpone_scheduled_task(paths, "s1", "2030-06-01T00:00:00")
    assert result == {"id": "s1", "next_run_at": "2030-06-01T00:00:00"}


def test_get_queue_capacity_success(paths, monkeypatch):
    app = FastAPI()

    @app.get("/v1/queue_keys/{queue_key}/capacity")
    def get_capacity(queue_key: str) -> dict:
        return {"queue_key": queue_key, "capacity": 1}

    _rig(paths, monkeypatch, "orchestration", app)
    assert client.get_queue_capacity(paths, "little-meals") == {"queue_key": "little-meals", "capacity": 1}


def test_set_queue_capacity_success(paths, monkeypatch):
    app = FastAPI()

    @app.post("/v1/queue_keys/{queue_key}/capacity")
    def set_capacity(queue_key: str, body: dict) -> dict:
        return {"queue_key": queue_key, "capacity": body["capacity"]}

    _rig(paths, monkeypatch, "orchestration", app)
    assert client.set_queue_capacity(paths, "little-meals", 3) == {"queue_key": "little-meals", "capacity": 3}


def test_get_raises_pillar_unavailable_on_http_error_status(paths, monkeypatch):
    from fastapi import HTTPException

    app = FastAPI()

    @app.get("/v1/projects/{name}")
    def get_project(name: str):
        raise HTTPException(status_code=404, detail="not found")

    _rig(paths, monkeypatch, "registry", app)
    with pytest.raises(client.PillarUnavailable):
        client.get_project(paths, "ghost")


def test_requirements_list_test_suites_raises_when_requirements_unreachable(paths):
    with pytest.raises(client.PillarUnavailable):
        client.requirements_list_test_suites(paths)


def test_requirements_list_requirements_raises_when_requirements_unreachable(paths):
    with pytest.raises(client.PillarUnavailable):
        client.requirements_list_requirements(paths)


def test_requirements_get_requirement_raises_when_requirements_unreachable(paths):
    with pytest.raises(client.PillarUnavailable):
        client.requirements_get_requirement(paths, "r1")


def test_requirements_report_raises_when_requirements_unreachable(paths):
    with pytest.raises(client.PillarUnavailable):
        client.requirements_report(paths, "little-widget")


def test_requirements_list_test_suites_success(paths, monkeypatch):
    app = FastAPI()

    @app.get("/v1/test_suites")
    def list_suites(project_id: str | None = None) -> list[dict]:
        return [{"id": "s1", "project_id": project_id}]

    _rig(paths, monkeypatch, "requirements", app)
    assert client.requirements_list_test_suites(paths, "p1") == [{"id": "s1", "project_id": "p1"}]


def test_requirements_list_requirements_success(paths, monkeypatch):
    app = FastAPI()

    @app.get("/v1/requirements")
    def list_requirements(project_id: str | None = None, status: str | None = None) -> list[dict]:
        return [{"id": "r1", "status": status}]

    _rig(paths, monkeypatch, "requirements", app)
    assert client.requirements_list_requirements(paths, "p1", "published") == [{"id": "r1", "status": "published"}]


def test_requirements_get_requirement_success(paths, monkeypatch):
    app = FastAPI()

    @app.get("/v1/requirements/{requirement_id}")
    def get_requirement(requirement_id: str) -> dict:
        return {"id": requirement_id}

    _rig(paths, monkeypatch, "requirements", app)
    assert client.requirements_get_requirement(paths, "r1") == {"id": "r1"}


def test_requirements_report_success(paths, monkeypatch):
    app = FastAPI()

    @app.get("/v1/report")
    def report(project_id: str) -> dict:
        return {"project_id": project_id, "done_count": 0}

    _rig(paths, monkeypatch, "requirements", app)
    assert client.requirements_report(paths, "p1") == {"project_id": "p1", "done_count": 0}


def test_build_list_runs_raises_when_build_unreachable(paths):
    with pytest.raises(client.PillarUnavailable):
        client.build_list_runs(paths)


def test_build_get_run_raises_when_build_unreachable(paths):
    with pytest.raises(client.PillarUnavailable):
        client.build_get_run(paths, "r1")


def test_build_list_packages_raises_when_build_unreachable(paths):
    with pytest.raises(client.PillarUnavailable):
        client.build_list_packages(paths)


def test_build_list_runs_success(paths, monkeypatch):
    app = FastAPI()

    @app.get("/v1/build_runs")
    def list_runs(project_id: str | None = None) -> list[dict]:
        return [{"id": "r1", "project_id": project_id}]

    _rig(paths, monkeypatch, "build", app)
    assert client.build_list_runs(paths, "p1") == [{"id": "r1", "project_id": "p1"}]


def test_build_get_run_success(paths, monkeypatch):
    app = FastAPI()

    @app.get("/v1/build_runs/{run_id}")
    def get_run(run_id: str) -> dict:
        return {"id": run_id}

    _rig(paths, monkeypatch, "build", app)
    assert client.build_get_run(paths, "r1") == {"id": "r1"}


def test_build_list_packages_success(paths, monkeypatch):
    app = FastAPI()

    @app.get("/v1/packages")
    def list_packages(project_id: str | None = None, kind: str | None = None) -> list[dict]:
        return [{"id": "pkg1", "kind": kind}]

    _rig(paths, monkeypatch, "build", app)
    assert client.build_list_packages(paths, "p1", "pypi") == [{"id": "pkg1", "kind": "pypi"}]


def test_policy_list_raises_when_policy_unreachable(paths):
    with pytest.raises(client.PillarUnavailable):
        client.policy_list(paths)


def test_policy_get_raises_when_policy_unreachable(paths):
    with pytest.raises(client.PillarUnavailable):
        client.policy_get(paths, "pol1")


def test_policy_winning_raises_when_policy_unreachable(paths):
    with pytest.raises(client.PillarUnavailable):
        client.policy_winning(paths, "t", "proj-a")


def test_policy_check_results_raises_when_policy_unreachable(paths):
    with pytest.raises(client.PillarUnavailable):
        client.policy_check_results(paths)


def test_policy_confirm_job_raises_when_orchestration_unreachable(paths):
    with pytest.raises(client.PillarUnavailable):
        client.policy_confirm_job(paths, "j1")


def test_policy_list_success(paths, monkeypatch):
    app = FastAPI()

    @app.get("/v1/policies")
    def list_policies(topic: str | None = None) -> list[dict]:
        return [{"id": "pol1", "topic": topic}]

    _rig(paths, monkeypatch, "policy", app)
    assert client.policy_list(paths, "t") == [{"id": "pol1", "topic": "t"}]


def test_policy_get_success(paths, monkeypatch):
    app = FastAPI()

    @app.get("/v1/policies/{policy_id}")
    def get_policy(policy_id: str) -> dict:
        return {"id": policy_id}

    _rig(paths, monkeypatch, "policy", app)
    assert client.policy_get(paths, "pol1") == {"id": "pol1"}


def test_policy_winning_success(paths, monkeypatch):
    app = FastAPI()

    @app.get("/v1/policies/winning")
    def winning(topic: str, project_id: str) -> dict:
        return {"topic": topic, "project_id": project_id, "policy": None}

    _rig(paths, monkeypatch, "policy", app)
    assert client.policy_winning(paths, "t", "proj-a") == {"topic": "t", "project_id": "proj-a", "policy": None}


def test_policy_check_results_success(paths, monkeypatch):
    app = FastAPI()

    @app.get("/v1/policy_check_results")
    def results(policy_id: str | None = None, project_id: str | None = None) -> list[dict]:
        return [{"policy_id": policy_id}]

    _rig(paths, monkeypatch, "policy", app)
    assert client.policy_check_results(paths, "pol1") == [{"policy_id": "pol1"}]


def test_policy_confirm_job_success(paths, monkeypatch):
    app = FastAPI()

    @app.post("/v1/jobs/{job_id}/policy_confirm")
    def confirm(job_id: str) -> dict:
        return {"confirmed": True, "job_id": job_id}

    _rig(paths, monkeypatch, "orchestration", app)
    assert client.policy_confirm_job(paths, "j1") == {"confirmed": True, "job_id": "j1"}


def test_policy_list_waivers_raises_when_policy_unreachable(paths):
    with pytest.raises(client.PillarUnavailable):
        client.policy_list_waivers(paths)


def test_policy_gating_for_job_raises_when_policy_unreachable(paths):
    with pytest.raises(client.PillarUnavailable):
        client.policy_gating_for_job(paths, "proj-a", ["server_launch"])


def test_policy_list_waivers_success(paths, monkeypatch):
    app = FastAPI()

    @app.get("/v1/policy_waivers")
    def list_waivers(policy_id: str | None = None, project_id: str | None = None) -> list[dict]:
        return [{"id": "w1", "policy_id": policy_id}]

    _rig(paths, monkeypatch, "policy", app)
    assert client.policy_list_waivers(paths, "pol1") == [{"id": "w1", "policy_id": "pol1"}]


def test_policy_gating_for_job_success(paths, monkeypatch):
    app = FastAPI()

    @app.get("/v1/policies/gating-for-job")
    def gating(project_id: str, categories: str) -> list[dict]:
        return [{"id": "pol1", "categories": categories}]

    _rig(paths, monkeypatch, "policy", app)
    assert client.policy_gating_for_job(paths, "proj-a", ["server_launch"]) == [{"id": "pol1", "categories": "server_launch"}]
