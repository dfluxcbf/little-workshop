import pytest
from fastapi.testclient import TestClient

from requirements.app import build_app
from requirements.ears import is_ears


@pytest.mark.parametrize("text", [
    "The Dashboard shall list every project.",
    "When the user taps Redeploy, the Dashboard shall submit one job per selected pillar.",
    "While a session is running, the Control page shall show it as running.",
    "Where a project has a server, the Dashboard shall show its version.",
    "If the pillar is down, then the Dashboard shall show 'unavailable'.",
    "While the plan is finalized, when the user taps Cancel, the Plan page shall ask for confirmation.",
    "Each requirement shall carry a key.",
])
def test_ears_forms_are_accepted(text):
    assert is_ears(text)


@pytest.mark.parametrize("text", [
    "", None, "The dashboard lists projects.", "Recipes can be classified as Vegan.",
    "Documented gap, not implemented this pass.", "shall do things", "When the user taps, do something.",
])
def test_non_ears_text_is_refused(text):
    assert not is_ears(text)


@pytest.fixture
def client(tmp_path):
    from common.paths import Paths
    from requirements import db
    conn = db.connect(tmp_path / "requirements.db")
    yield TestClient(build_app(conn, Paths(config_home=tmp_path / "c", state_home=tmp_path / "s")))
    conn.close()


def _create(client, description, **extra):
    return client.post("/v1/jobs/requirements.create_requirement", json={
        "job_id": "j", "project_id": "p1", "params": {"key": "REQ-1", "title": "t", "description": description, **extra},
    }).json()


def test_create_refuses_a_non_ears_description_and_says_how_to_fix_it(client):
    response = _create(client, "The dashboard lists projects.")
    assert response["outcome"] == "failed" and "EARS" in response["error"] and "shall" in response["error"]


def test_create_accepts_ears_and_import_bypasses_the_check(client):
    assert _create(client, "The Dashboard shall list projects.")["outcome"] == "succeeded"
    assert _create(client, "old prose from another tool", imported=True, key="REQ-2")["outcome"] == "succeeded"


def test_update_is_checked_too(client):
    created = _create(client, "The Dashboard shall list projects.")["result"]
    bad = client.post("/v1/jobs/requirements.update_requirement", json={
        "job_id": "j", "project_id": "p1", "params": {"requirement_id": created["id"], "description": "lists projects"}}).json()
    assert bad["outcome"] == "failed" and "EARS" in bad["error"]
    good = client.post("/v1/jobs/requirements.update_requirement", json={
        "job_id": "j", "project_id": "p1", "params": {"requirement_id": created["id"], "description": "The Dashboard shall list every project."}}).json()
    assert good["outcome"] == "succeeded"
