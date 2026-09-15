import pytest
from fastapi.testclient import TestClient

from orchestration import db
from orchestration.app import build_app


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "orchestration.db")
    yield connection
    connection.close()


def always_unavailable(pillar: str):
    return None


@pytest.fixture
def client(conn):
    return TestClient(build_app(conn, socket_lookup=always_unavailable))


def test_health_endpoint(client):
    response = client.get("/v1/health")
    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_create_job_dispatches_and_fails_when_pillar_unavailable(client):
    response = client.post(
        "/v1/jobs",
        json={"type": "noop", "target_pillar": "reference", "params": {}},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert body["error"] == "pillar disabled"


def test_get_job_not_found(client):
    response = client.get("/v1/jobs/does-not-exist")
    assert response.status_code == 404


def test_list_jobs_filters_by_status(client):
    client.post("/v1/jobs", json={"type": "noop", "target_pillar": "reference"})
    response = client.get("/v1/jobs", params={"status": "failed"})
    assert response.status_code == 200
    assert len(response.json()) == 1


def test_pipeline_save_get_and_trigger(client):
    pipeline = {
        "name": "release",
        "version": 1,
        "nodes": [
            {"node_id": "build", "job_type": "build.run", "target_pillar": "build"},
            {
                "node_id": "deploy",
                "job_type": "deployment.deploy",
                "target_pillar": "deployment",
                "depends_on": ["build"],
            },
        ],
    }
    save_response = client.post("/v1/pipelines", json=pipeline)
    assert save_response.status_code == 200

    get_response = client.get("/v1/pipelines/release")
    assert get_response.status_code == 200
    assert len(get_response.json()["nodes"]) == 2

    trigger_response = client.post(
        "/v1/pipelines/release/trigger", json={"project_id": "little-meals", "created_by": "lws"}
    )
    assert trigger_response.status_code == 200
    run = trigger_response.json()
    assert set(run["job_ids"]) == {"build", "deploy"}

    run_get = client.get(f"/v1/pipeline_runs/{run['id']}")
    assert run_get.status_code == 200


def test_trigger_unknown_pipeline_404s(client):
    response = client.post("/v1/pipelines/ghost/trigger", json={})
    assert response.status_code == 404


def test_complete_job_callback(conn):
    def available(pillar):
        return None

    app_client = TestClient(build_app(conn, socket_lookup=available))
    create = app_client.post(
        "/v1/jobs",
        json={"type": "agent.run_task", "target_pillar": "agent", "params": {}},
    )
    job_id = create.json()["id"]

    response = app_client.post(
        f"/v1/jobs/{job_id}/complete",
        json={"succeeded": True, "result": {"pr": 1}, "error": None},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "succeeded"
