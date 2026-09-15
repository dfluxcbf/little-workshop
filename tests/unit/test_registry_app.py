import pytest
from fastapi.testclient import TestClient

from common.paths import Paths
from registry import db
from registry.app import build_app


@pytest.fixture
def paths(tmp_path):
    p = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    p.ensure_layout()
    return p


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "registry.db")
    yield connection
    connection.close()


@pytest.fixture
def client(conn, paths, tmp_path):
    (tmp_path / "workdir").mkdir()
    return TestClient(build_app(conn, paths)), tmp_path / "workdir"


def test_health_endpoint(client):
    app_client, _ = client
    response = app_client.get("/v1/health")
    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_job_bootstrap_creates_project_and_registers(client):
    app_client, workdir = client
    response = app_client.post(
        "/v1/jobs/project.bootstrap",
        json={"job_id": "j1", "params": {"name": "little-widget", "dest_dir": str(workdir)}},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] == "succeeded"
    assert body["result"]["name"] == "little-widget"

    listed = app_client.get("/v1/projects").json()
    assert [p["name"] for p in listed] == ["little-widget"]


def test_job_bootstrap_duplicate_fails_with_outcome_not_http_error(client):
    app_client, workdir = client
    params = {"name": "little-widget", "dest_dir": str(workdir)}
    app_client.post("/v1/jobs/project.bootstrap", json={"job_id": "j1", "params": params})
    response = app_client.post("/v1/jobs/project.bootstrap", json={"job_id": "j2", "params": params})

    assert response.status_code == 200
    assert response.json()["outcome"] == "failed"


def test_job_remove_deletes_project(client):
    app_client, workdir = client
    params = {"name": "little-widget", "dest_dir": str(workdir)}
    app_client.post("/v1/jobs/project.bootstrap", json={"job_id": "j1", "params": params})

    response = app_client.post(
        "/v1/jobs/project.remove", json={"job_id": "j2", "params": {"name": "little-widget"}}
    )
    assert response.json()["outcome"] == "succeeded"
    assert app_client.get("/v1/projects/little-widget").status_code == 404


def test_job_remove_unknown_project_fails_gracefully(client):
    app_client, _ = client
    response = app_client.post(
        "/v1/jobs/project.remove", json={"job_id": "j1", "params": {"name": "ghost"}}
    )
    assert response.json()["outcome"] == "failed"


def test_project_set_lifecycle(client):
    app_client, workdir = client
    app_client.post(
        "/v1/jobs/project.bootstrap",
        json={"job_id": "j1", "params": {"name": "a", "dest_dir": str(workdir)}},
    )
    app_client.post(
        "/v1/jobs/project.bootstrap",
        json={"job_id": "j2", "params": {"name": "b", "dest_dir": str(workdir)}},
    )

    create = app_client.post("/v1/project_sets", json={"name": "household", "project_names": ["a"]})
    assert create.status_code == 200

    added = app_client.post("/v1/project_sets/household/members/b")
    assert set(added.json()["project_names"]) == {"a", "b"}

    removed = app_client.delete("/v1/project_sets/household/members/a")
    assert removed.json()["project_names"] == ["b"]

    listed = app_client.get("/v1/project_sets").json()
    assert [s["name"] for s in listed] == ["household"]


def test_ticket_lifecycle(client):
    app_client, workdir = client
    app_client.post(
        "/v1/jobs/project.bootstrap",
        json={"job_id": "j1", "params": {"name": "a", "dest_dir": str(workdir)}},
    )
    created = app_client.post("/v1/tickets", json={"project_name": "a", "title": "fix the thing"})
    assert created.status_code == 200

    listed = app_client.get("/v1/tickets", params={"project_name": "a"}).json()
    assert len(listed) == 1
    assert listed[0]["title"] == "fix the thing"
