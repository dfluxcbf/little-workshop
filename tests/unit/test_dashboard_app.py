"""dashboard.app's HTML routes, exercised via a real FastAPI TestClient
against a real Jinja2-rendered response — `dashboard.client` itself is
monkeypatched here (its own HTTP glue is covered for real in
test_dashboard_client.py), so these tests are about what the app does
with a client response/failure, not the socket transport again."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from common.paths import Paths
from dashboard import app as app_module
from dashboard import client


@pytest.fixture
def paths(tmp_path):
    return Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")


@pytest.fixture
def test_client(paths):
    return TestClient(app_module.build_app(paths))


def test_health(test_client):
    response = test_client.get("/v1/health")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_index_renders_pillar_table(test_client, monkeypatch):
    monkeypatch.setattr(client, "core_status", lambda paths: {
        "pillars": {"registry": {"status": "active", "crash_count": 0}}
    })
    response = test_client.get("/")
    assert response.status_code == 200
    assert "registry" in response.text
    assert "UNREACHABLE" not in response.text


def test_index_renders_error_banner_when_core_unreachable(test_client, monkeypatch):
    def raise_unavailable(paths):
        raise client.PillarUnavailable("Core daemon unreachable")

    monkeypatch.setattr(client, "core_status", raise_unavailable)
    response = test_client.get("/")
    assert response.status_code == 200
    assert "UNREACHABLE" in response.text
    assert "Core daemon unreachable" in response.text


def test_projects_renders_rows(test_client, monkeypatch):
    monkeypatch.setattr(client, "list_projects", lambda paths, status=None: [{"id": "p1", "name": "little-widget", "status": "active"}])
    response = test_client.get("/projects")
    assert response.status_code == 200
    assert "little-widget" in response.text


def test_projects_renders_empty_state(test_client, monkeypatch):
    monkeypatch.setattr(client, "list_projects", lambda paths, status=None: [])
    response = test_client.get("/projects")
    assert "No projects registered" in response.text


def test_projects_renders_error_banner_when_registry_unreachable(test_client, monkeypatch):
    def raise_unavailable(paths, status=None):
        raise client.PillarUnavailable("registry daemon unreachable")

    monkeypatch.setattr(client, "list_projects", raise_unavailable)
    response = test_client.get("/projects")
    assert response.status_code == 200
    assert "UNREACHABLE" in response.text


def test_jobs_renders_error_banner_when_orchestration_unreachable(test_client, monkeypatch):
    def raise_unavailable(paths, status=None):
        raise client.PillarUnavailable("orchestration daemon unreachable")

    monkeypatch.setattr(client, "list_jobs", raise_unavailable)
    response = test_client.get("/jobs")
    assert response.status_code == 200
    assert "UNREACHABLE" in response.text


def test_policies_renders_error_banner_when_policy_unreachable(test_client, monkeypatch):
    def raise_unavailable(paths):
        raise client.PillarUnavailable("policy daemon unreachable")

    monkeypatch.setattr(client, "policy_list", raise_unavailable)
    response = test_client.get("/policies")
    assert response.status_code == 200
    assert "UNREACHABLE" in response.text


def test_jobs_passes_through_status_filter(test_client, monkeypatch):
    captured = {}

    def fake_list_jobs(paths, status=None):
        captured["status"] = status
        return [{"id": "j1", "type": "policy.configure", "target_pillar": "policy", "project_id": None, "status": "succeeded"}]

    monkeypatch.setattr(client, "list_jobs", fake_list_jobs)
    response = test_client.get("/jobs", params={"status": "failed"})
    assert response.status_code == 200
    assert captured["status"] == "failed"
    assert "policy.configure" in response.text


def test_policies_renders_ears_text_and_waivers(test_client, monkeypatch):
    monkeypatch.setattr(client, "policy_list", lambda paths: [
        {"id": "pol1", "ears_text": "every project shall have a name", "enabled": True,
         "gate_points": ["server_launch"], "scope_level": "all_projects", "scope_target": None}
    ])
    monkeypatch.setattr(client, "policy_list_waivers", lambda paths: [
        {"policy_id": "pol1", "project_id": "proj-a", "reason": "cannot comply yet"}
    ])
    response = test_client.get("/policies")
    assert response.status_code == 200
    assert "every project shall have a name" in response.text
    assert "cannot comply yet" in response.text


def test_policy_detail_success_renders_script_and_waivers(test_client, monkeypatch):
    monkeypatch.setattr(client, "policy_get", lambda paths, policy_id: {
        "id": policy_id, "ears_text": "every project shall have a name", "topic": "t",
        "target_pillar": "registry", "enabled": True, "gate_points": ["server_launch"],
        "test_script": "def test_x():\n    assert True\n",
    })
    monkeypatch.setattr(client, "policy_list_waivers", lambda paths, policy_id=None, project_id=None: [
        {"id": "w1", "project_id": "proj-a", "reason": "cannot comply yet"}
    ])
    response = test_client.get("/policies/pol1")
    assert response.status_code == 200
    assert "def test_x" in response.text
    assert "cannot comply yet" in response.text
    assert "UNREACHABLE" not in response.text


def test_policy_detail_not_found_renders_a_message_not_a_500(test_client, monkeypatch):
    monkeypatch.setattr(client, "policy_get", lambda paths, policy_id: (_ for _ in ()).throw(client.PillarUnavailable("policy daemon unreachable")))
    monkeypatch.setattr(client, "policy_list_waivers", lambda paths, policy_id=None, project_id=None: [])
    response = test_client.get("/policies/nonexistent")
    assert response.status_code == 200
    assert "UNREACHABLE" in response.text


def test_policy_configure_absent_checkbox_means_disabled(test_client, monkeypatch):
    captured = {}

    def fake_create_job(paths, **fields):
        captured.update(fields)
        return {"status": "succeeded"}

    monkeypatch.setattr(client, "create_job", fake_create_job)
    response = test_client.post("/policies/pol1/configure", data={"gate_points": ["server_launch"]}, follow_redirects=False)
    assert response.status_code == 303
    assert captured["params"]["enabled"] is False
    assert captured["params"]["gate_points"] == ["server_launch"]
    assert captured["type"] == "policy.configure"


def test_policy_configure_checked_checkbox_means_enabled(test_client, monkeypatch):
    captured = {}

    def fake_create_job(paths, **fields):
        captured.update(fields)
        return {"status": "succeeded"}

    monkeypatch.setattr(client, "create_job", fake_create_job)
    response = test_client.post("/policies/pol1/configure", data={"enabled": "true"}, follow_redirects=False)
    assert response.status_code == 303
    assert captured["params"]["enabled"] is True
    assert captured["params"]["gate_points"] == []


def test_waiver_grant_posts_reason_and_project(test_client, monkeypatch):
    captured = {}

    def fake_create_job(paths, **fields):
        captured.update(fields)
        return {"status": "succeeded"}

    monkeypatch.setattr(client, "create_job", fake_create_job)
    response = test_client.post(
        "/policies/pol1/waivers/grant", data={"project_id": "proj-a", "reason": "cannot comply yet"}, follow_redirects=False
    )
    assert response.status_code == 303
    assert captured["type"] == "policy.waiver_grant"
    assert captured["project_id"] == "proj-a"
    assert captured["params"] == {"policy_id": "pol1", "reason": "cannot comply yet"}


def test_waiver_revoke_posts_waiver_id(test_client, monkeypatch):
    captured = {}

    def fake_create_job(paths, **fields):
        captured.update(fields)
        return {"status": "succeeded"}

    monkeypatch.setattr(client, "create_job", fake_create_job)
    response = test_client.post("/policies/pol1/waivers/waiver-1/revoke", follow_redirects=False)
    assert response.status_code == 303
    assert captured["type"] == "policy.waiver_revoke"
    assert captured["params"] == {"waiver_id": "waiver-1"}
