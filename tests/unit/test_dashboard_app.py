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


def _stub_project_panels(monkeypatch, *, server=None, session=None, runs=(), report=None, ci=None, names=("little-widget",)):
    monkeypatch.setattr(client, "list_projects", lambda paths, status=None: [{"name": n, "status": "active"} for n in names])
    monkeypatch.setattr(client, "deployment_status_optional", lambda paths, name: server)
    monkeypatch.setattr(client, "deployment_branches", lambda paths, name: ["dev", "master"])
    monkeypatch.setattr(client, "agent_session_status", lambda paths, name: session)
    monkeypatch.setattr(client, "agent_runs", lambda paths, name: list(runs))
    monkeypatch.setattr(client, "requirements_report", lambda paths, name: report)
    monkeypatch.setattr(client, "agent_latest_ci_check", lambda paths, name: ci)
    monkeypatch.setattr(client, "core_status", lambda paths: {"pillars": {"registry": {"status": "healthy", "crash_count": 0}}})


def test_index_is_a_project_dashboard(test_client, monkeypatch):
    _stub_project_panels(
        monkeypatch,
        server={"listening": True, "version": "0.3.1", "unit": "active"},
        session={"running": True, "trusted": True},
        runs=[{"status": "awaiting_finish"}, {"status": "running"}],
        report={"requirements": [{"done": True}, {"done": False}]},
        ci={"state": "success"},
    )
    text = test_client.get("/").text
    assert "little-widget" in text
    assert "1 awaiting finish" in text and "1 running" in text
    assert "1/2 done" in text
    assert "success" in text
    assert "Pillars healthy" in text


def test_index_shows_unknown_panels_when_a_pillar_is_down(test_client, monkeypatch):
    _stub_project_panels(monkeypatch)

    def down(paths, name):
        raise client.PillarUnavailable("agent daemon unreachable")

    monkeypatch.setattr(client, "agent_session_status", down)
    monkeypatch.setattr(client, "agent_runs", down)
    response = test_client.get("/")
    assert response.status_code == 200 and "little-widget" in response.text


def test_index_renders_error_banner_when_registry_unreachable(test_client, monkeypatch):
    def raise_unavailable(paths, status=None):
        raise client.PillarUnavailable("registry daemon unreachable")

    monkeypatch.setattr(client, "list_projects", raise_unavailable)
    monkeypatch.setattr(client, "core_status", lambda paths: {"pillars": {}})
    response = test_client.get("/")
    assert response.status_code == 200
    assert "UNREACHABLE" in response.text


def test_index_flags_unhealthy_pillars(test_client, monkeypatch):
    _stub_project_panels(monkeypatch)
    monkeypatch.setattr(client, "core_status", lambda paths: {"pillars": {"agent": {"status": "crashed", "crash_count": 2}}})
    assert "not healthy" in test_client.get("/").text


def test_pillars_page_lists_pillars_separately(test_client, monkeypatch):
    monkeypatch.setattr(client, "core_status", lambda paths: {"pillars": {
        "registry": {"status": "healthy", "crash_count": 0, "interface_version": "v1"},
        "build": {"status": "disabled", "crash_count": 0, "interface_version": "v1"},
    }})
    text = test_client.get("/pillars").text
    assert "registry" in text and "healthy" in text and "disabled" in text


def test_pillars_page_renders_error_banner_when_core_unreachable(test_client, monkeypatch):
    def raise_unavailable(paths):
        raise client.PillarUnavailable("Core daemon unreachable")

    monkeypatch.setattr(client, "core_status", raise_unavailable)
    text = test_client.get("/pillars").text
    assert "UNREACHABLE" in text and "Core daemon unreachable" in text


def test_control_offers_launch_and_session_start(test_client, monkeypatch):
    _stub_project_panels(
        monkeypatch, server={"listening": False, "version": "0.3.1", "unit": "inactive", "branch": "dev", "tailscale": "off"},
        session={"running": False, "trusted": True},
    )
    text = test_client.get("/control").text
    assert "/projects/little-widget/deploy" in text and "Launch" in text
    assert "/projects/little-widget/session/start" in text and "Start session" in text


def test_control_explains_an_untrusted_project(test_client, monkeypatch):
    _stub_project_panels(monkeypatch, session={"running": False, "trusted": False})
    text = test_client.get("/control").text
    assert "not trusted" in text.lower() and "/session/start" not in text


def test_control_shows_stop_for_a_running_session_and_server(test_client, monkeypatch):
    _stub_project_panels(
        monkeypatch, server={"listening": True, "version": "1", "unit": "active", "branch": "dev", "tailscale": "mapped"},
        session={"running": True, "trusted": True},
    )
    text = test_client.get("/control").text
    assert "/projects/little-widget/stop" in text and "/projects/little-widget/session/stop" in text
    assert "lws-little-widget-session" in text


def test_control_without_a_manifest_says_how_to_add_one(test_client, monkeypatch):
    _stub_project_panels(monkeypatch, server=None, session={"running": False, "trusted": True})
    assert "set-manifest" in test_client.get("/control").text


def test_projects_redirects_to_control(test_client):
    response = test_client.get("/projects", follow_redirects=False)
    assert response.status_code == 307 and response.headers["location"] == "/control"


def test_deploy_submits_the_job_without_blocking(test_client, monkeypatch):
    import threading

    submitted = threading.Event()
    captured = {}

    def fake_create_job(paths, timeout=30.0, **fields):
        captured.update(fields, timeout=timeout)
        submitted.set()
        return {"status": "succeeded"}

    monkeypatch.setattr(client, "create_job", fake_create_job)
    response = test_client.post("/projects/little-widget/deploy", data={"branch": "dev"}, follow_redirects=False)
    assert response.status_code == 303 and "started" in response.headers["location"]
    assert submitted.wait(timeout=5)
    assert captured["type"] == "deployment.deploy" and captured["params"] == {"branch": "dev"} and captured["timeout"] == 320.0


def test_stop_reports_success_and_failure(test_client, monkeypatch):
    monkeypatch.setattr(client, "create_job", lambda paths, **f: {"status": "succeeded"})
    assert "stopped" in test_client.post("/projects/little-widget/stop", follow_redirects=False).headers["location"]
    monkeypatch.setattr(client, "create_job", lambda paths, **f: {"status": "failed", "error": "no manifest"})
    assert "no%20manifest" in test_client.post("/projects/little-widget/stop", follow_redirects=False).headers["location"]


def test_session_start_and_stop_submit_agent_jobs(test_client, monkeypatch):
    seen = []

    def fake_create_job(paths, **f):
        seen.append(f["type"])
        return {"status": "succeeded", "result": {"session_name": "lws-little-widget-session"}}

    monkeypatch.setattr(client, "create_job", fake_create_job)
    started = test_client.post("/projects/little-widget/session/start", follow_redirects=False)
    assert "lws-little-widget-session" in started.headers["location"]
    test_client.post("/projects/little-widget/session/stop", follow_redirects=False)
    assert seen == ["agent.session_start", "agent.session_stop"]


def test_session_start_failure_is_reported(test_client, monkeypatch):
    monkeypatch.setattr(client, "create_job", lambda paths, **f: {"status": "failed", "error": "not trusted"})
    assert "not%20trusted" in test_client.post("/projects/x/session/start", follow_redirects=False).headers["location"]


def test_tasks_lists_runs_across_projects_newest_first(test_client, monkeypatch):
    _stub_project_panels(monkeypatch, names=("a", "b"), runs=[])
    monkeypatch.setattr(client, "agent_runs", lambda paths, name: [
        {"id": f"r-{name}", "project_id": name, "title": f"task on {name}", "status": "awaiting_finish", "branch": "feature/x",
         "created_at": "2026-09-21T10:00:00" if name == "a" else "2026-09-21T11:00:00"},
    ])
    text = test_client.get("/tasks").text
    assert text.index("task on b") < text.index("task on a")


def test_new_task_form_and_submit(test_client, monkeypatch):
    _stub_project_panels(monkeypatch)
    assert "little-widget" in test_client.get("/tasks/new?project=little-widget").text
    captured = {}
    monkeypatch.setattr(client, "create_job", lambda paths, **f: (captured.update(f), {"status": "running"})[1])
    response = test_client.post(
        "/tasks/new", data={"project": "little-widget", "title": "fix it", "description": "d", "intent": "bug", "review_mode": "manual"},
        follow_redirects=False,
    )
    assert response.status_code == 303 and response.headers["location"].startswith("/tasks")
    assert captured["type"] == "agent.run_task" and captured["queue_key"] == "little-widget"
    assert captured["params"]["intent"] == "bug"


def test_new_task_failure_is_reported(test_client, monkeypatch):
    monkeypatch.setattr(client, "create_job", lambda paths, **f: {"status": "failed", "error": "dirty checkout"})
    location = test_client.post("/tasks/new", data={"project": "p", "title": "t"}, follow_redirects=False).headers["location"]
    assert location.startswith("/tasks/new") and "dirty%20checkout" in location


def test_task_detail_offers_finish_and_skip_when_awaiting(test_client, monkeypatch):
    monkeypatch.setattr(client, "agent_run", lambda paths, project, run_id: {
        "id": run_id, "project_id": project, "title": "fix it", "status": "awaiting_finish", "branch": "feature/x",
        "completion_status": "done", "pr_url": None, "summary": "did it", "report": "# report",
    })
    text = test_client.get("/tasks/little-widget/r1").text
    assert "/tasks/little-widget/r1/finish" in text and "/tasks/little-widget/r1/skip" in text and "# report" in text


def test_task_detail_for_a_missing_run_is_not_a_500(test_client, monkeypatch):
    monkeypatch.setattr(client, "agent_run", lambda paths, project, run_id: None)
    response = test_client.get("/tasks/p/nope")
    assert response.status_code == 200 and "no such task" in response.text


def test_finish_and_skip_submit_agent_jobs(test_client, monkeypatch):
    seen = []
    monkeypatch.setattr(client, "create_job", lambda paths, **f: (seen.append((f["type"], f["params"])), {"status": "succeeded"})[1])
    test_client.post("/tasks/p/r1/finish", follow_redirects=False)
    test_client.post("/tasks/p/r1/skip", follow_redirects=False)
    assert seen == [("agent.finish_task", {"run_id": "r1"}), ("agent.skip_task", {"run_id": "r1"})]
    assert "Unknown" in test_client.post("/tasks/p/r1/bogus", follow_redirects=False).headers["location"]


# -- installable on Android (PWA) --------------------------------------------


def test_manifest_makes_it_installable(test_client):
    import json

    manifest = json.loads(test_client.get("/static/manifest.webmanifest").text)
    assert manifest["display"] == "standalone" and manifest["start_url"] == "/"
    sizes = {(i["sizes"], i["purpose"]) for i in manifest["icons"]}
    assert {("192x192", "any"), ("512x512", "any"), ("512x512", "maskable")} <= sizes
    for icon in manifest["icons"]:
        assert test_client.get(icon["src"]).status_code == 200


def test_pages_link_the_manifest_and_a_mobile_viewport(test_client, monkeypatch):
    _stub_project_panels(monkeypatch)
    text = test_client.get("/").text
    assert 'rel="manifest"' in text and "viewport-fit=cover" in text and "width=device-width" in text
    assert 'class="tabbar"' in text  # the phone's bottom navigation


def test_service_worker_is_served_from_the_root_scope(test_client):
    response = test_client.get("/sw.js")
    assert response.status_code == 200 and "javascript" in response.headers["content-type"]
    assert response.headers["service-worker-allowed"] == "/"
    assert "fetch" in response.text


def test_offline_page_renders(test_client):
    assert "Offline" in test_client.get("/offline").text


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
