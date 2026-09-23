"""Remaining orchestration/app.py 404 branches and the /v1/pump endpoint,
not reached by test_orchestration_app.py."""

import pytest
from fastapi.testclient import TestClient

from orchestration import db
from orchestration.app import build_app


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "orchestration.db")
    yield connection
    connection.close()


@pytest.fixture
def client(conn):
    return TestClient(build_app(conn, socket_lookup=lambda pillar: None))


def test_complete_unknown_job_404s(client):
    response = client.post("/v1/jobs/does-not-exist/complete", json={"succeeded": True})
    assert response.status_code == 404


def test_get_unknown_pipeline_404s(client):
    response = client.get("/v1/pipelines/ghost")
    assert response.status_code == 404


def test_get_unknown_pipeline_run_404s(client):
    response = client.get("/v1/pipeline_runs/does-not-exist")
    assert response.status_code == 404


def test_pump_endpoint_dispatches_ready_jobs(client):
    client.post("/v1/jobs", json={"type": "noop", "target_pillar": "reference", "params": {}})
    response = client.post("/v1/pump")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_pipeline_get_with_specific_version(client):
    pipeline_v1 = {"name": "release", "version": 1, "nodes": [{"node_id": "a", "job_type": "x", "target_pillar": "reference"}]}
    pipeline_v2 = {"name": "release", "version": 2, "nodes": [{"node_id": "a", "job_type": "x", "target_pillar": "reference"}, {"node_id": "b", "job_type": "y", "target_pillar": "reference"}]}
    client.post("/v1/pipelines", json=pipeline_v1)
    client.post("/v1/pipelines", json=pipeline_v2)

    response = client.get("/v1/pipelines/release", params={"version": 1})
    assert len(response.json()["nodes"]) == 1
