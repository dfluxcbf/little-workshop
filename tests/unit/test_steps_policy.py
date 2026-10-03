"""The Policy & Compliance pillar reports the steps its step spec declares."""

import pytest
from fastapi.testclient import TestClient

from common import job_steps
from common.paths import Paths
from common.step_specs import steps_for
from policy import app as app_module
from policy import db
from policy.app import build_app


# --- REQ-000000310: Every job has a checklist of its actions ---

@pytest.fixture
def paths(tmp_path):
    p = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    p.ensure_layout()
    return p


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "policy.db")
    yield connection
    connection.close()


@pytest.fixture
def reports(monkeypatch):
    """(step key, state, detail) for every step report the pillar makes."""
    seen = []

    def fake_socket_call(paths):
        def call(method, path, json=None):
            if method == "POST" and "/steps/" in path:
                seen.append((path.rsplit("/", 1)[1], json["state"], json["detail"]))
            return {}
        return call

    monkeypatch.setattr(job_steps, "_socket_call", fake_socket_call)
    return seen


@pytest.fixture
def client(conn, paths, monkeypatch):
    def fake_query(paths, prompt, **kw):
        if "ENFORCEABLE:" in prompt:
            return "ENFORCEABLE: yes"
        if "NO_CONFLICT" in prompt:
            return "NO_CONFLICT"
        if "pytest test script" in prompt:
            return "def test_generated():\n    assert True\n"
        if "EARS_OK" in prompt:
            return "EARS_OK: WHEN x, the system shall y."
        return "unexpected prompt shape"

    monkeypatch.setattr(app_module.agent_client, "query", fake_query)
    monkeypatch.setattr(app_module.registry_client, "list_active_project_ids", lambda paths, **kw: ["proj-a"])
    monkeypatch.setattr(app_module.registry_client, "project_set_members", lambda paths, name, **kw: [])
    return TestClient(build_app(conn, paths))


def _keys(reports, state):
    return [key for key, s, _ in reports if s == state]


def _declared(job_type):
    return [s.key for s in steps_for(job_type)]


def test_author_reports_every_declared_step_in_order(client, reports):
    response = client.post("/v1/jobs/policy.author", json={"job_id": "j", "params": {
        "ears_text": "when x happens, do y", "topic": "t", "target_pillar": "deployment",
        "scope_level": "all_projects", "created_by": "user",
    }})
    assert response.json()["outcome"] == "succeeded"
    assert _keys(reports, "succeeded") == _declared("policy.author")


def test_author_without_a_draft_still_completes_the_save_draft_step(client, reports):
    client.post("/v1/jobs/policy.author", json={"job_id": "j", "params": {
        "ears_text": "x", "topic": "t", "target_pillar": "deployment", "scope_level": "all_projects", "created_by": "user",
    }})
    assert ("save_draft", "succeeded", "no draft requested") in reports


def test_activate_reports_its_steps(client, reports):
    params = {
        "ears_text": "WHEN x, the system shall y.", "test_script": "def test_a(): pass", "topic": "t",
        "target_pillar": "deployment", "scope_level": "all_projects", "created_by": "user", "approved": True,
    }
    assert client.post("/v1/jobs/policy.activate", json={"job_id": "j", "params": params}).json()["outcome"] == "succeeded"
    assert _keys(reports, "succeeded") == _declared("policy.activate")


def test_a_refused_request_fails_its_step_with_the_message(client, reports):
    response = client.post("/v1/jobs/policy.activate", json={"job_id": "j", "params": {
        "ears_text": "e", "test_script": "s", "topic": "t", "target_pillar": "deployment", "created_by": "user",
    }})
    assert response.json()["outcome"] == "failed"
    failed = [(key, detail) for key, state, detail in reports if state == "failed"]
    assert failed == [("check_approval", response.json()["error"])]
    assert "approval" in response.json()["error"]


def test_draft_import_reports_its_step(client, reports):
    response = client.post("/v1/jobs/policy.draft_import", json={"job_id": "j", "params": {"drafts": []}})
    assert response.json()["outcome"] == "failed"
    assert [(k, s) for k, s, _ in reports if s == "failed"] == [("import_drafts", "failed")]
    assert _declared("policy.draft_import") == ["import_drafts"]


def test_check_all_reports_when_the_policy_is_missing(client, reports):
    response = client.post("/v1/jobs/policy.check_all", json={"job_id": "j", "params": {"policy_id": "ghost"}})
    assert response.json()["outcome"] == "failed"
    assert [(k, s) for k, s, _ in reports if s == "failed"] == [("find_policy", "failed")]
    assert _declared("policy.check_all") == ["find_policy", "dispatch_checks"]


def test_check_project_declares_the_steps_its_code_reports():
    assert _declared("policy.check_project") == ["find_policy", "check_changes", "prepare_sandbox", "run_check", "record_result"]
