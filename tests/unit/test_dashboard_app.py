"""dashboard.app's HTML routes, exercised via a real FastAPI TestClient
against a real Jinja2-rendered response — `dashboard.client` itself is
monkeypatched here (its own HTTP glue is covered for real in
test_dashboard_client.py), so these tests are about what the app does
with a client response/failure, not the socket transport again."""

from __future__ import annotations

import re
import threading
from urllib.parse import unquote

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


def test_index_fetches_each_projects_row_concurrently_not_one_at_a_time(test_client, monkeypatch):
    # 2026-09-23 finding: this loop was the largest remaining cost of the
    # home page after the report's own N+1 query got fixed — verifying
    # overlap (not just that the page still renders) is what would catch
    # a future change that accidentally serializes it again.
    import time

    names = ["proj-a", "proj-b", "proj-c"]
    monkeypatch.setattr(client, "list_projects", lambda paths, status=None: [{"name": n, "status": "active"} for n in names])
    monkeypatch.setattr(client, "core_status", lambda paths: {"pillars": {}})
    monkeypatch.setattr(client, "deployment_branches", lambda paths, name: [])
    monkeypatch.setattr(client, "agent_session_status", lambda paths, name: None)
    monkeypatch.setattr(client, "agent_runs", lambda paths, name: [])
    monkeypatch.setattr(client, "requirements_report", lambda paths, name: None)
    monkeypatch.setattr(client, "agent_latest_ci_check", lambda paths, name: None)

    intervals = []
    lock = threading.Lock()

    def slow_deployment_status(paths, name):
        start = time.monotonic()
        time.sleep(0.1)
        with lock:
            intervals.append((start, time.monotonic()))
        return None

    monkeypatch.setattr(client, "deployment_status_optional", slow_deployment_status)

    response = test_client.get("/")

    assert response.status_code == 200
    assert len(intervals) == len(names)
    # If these ran serially, each one's start would be at or after the
    # previous one's own end; running concurrently, the latest start is
    # still before the earliest end.
    latest_start = max(s for s, _ in intervals)
    earliest_end = min(e for _, e in intervals)
    assert latest_start < earliest_end


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


def test_dashboard_card_offers_launch_and_session_start_when_expanded(test_client, monkeypatch):
    _stub_project_panels(
        monkeypatch, server={"listening": False, "version": "0.3.1", "unit": "inactive", "branch": "dev", "tailscale": "off"},
        session={"running": False, "trusted": True},
    )
    text = test_client.get("/").text
    assert "/projects/little-widget/deploy" in text and "Launch" in text
    assert "/projects/little-widget/session/start" in text and "Start session" in text


def test_dashboard_card_explains_an_untrusted_project(test_client, monkeypatch):
    _stub_project_panels(monkeypatch, session={"running": False, "trusted": False})
    text = test_client.get("/").text
    assert "not trusted" in text.lower() and "/session/start" not in text


def test_dashboard_card_shows_stop_for_a_running_session_and_server(test_client, monkeypatch):
    _stub_project_panels(
        monkeypatch, server={"listening": True, "version": "1", "unit": "active", "branch": "dev", "tailscale": "mapped"},
        session={"running": True, "trusted": True},
    )
    text = test_client.get("/").text
    assert "/projects/little-widget/stop" in text and "/projects/little-widget/session/stop" in text
    assert "lws-little-widget-session" in text


def test_dashboard_card_without_a_manifest_says_how_to_add_one(test_client, monkeypatch):
    _stub_project_panels(monkeypatch, server=None, session={"running": False, "trusted": True})
    assert "set-manifest" in test_client.get("/").text


def test_projects_redirects_to_dashboard(test_client):
    response = test_client.get("/projects", follow_redirects=False)
    assert response.status_code == 307 and response.headers["location"] == "/"


def test_dashboard_card_is_a_details_element_that_expands_to_show_controls(test_client, monkeypatch):
    _stub_project_panels(monkeypatch, server={"listening": True, "version": "1", "unit": "active", "branch": "dev", "tailscale": "off"})
    text = test_client.get("/").text
    assert '<details class="proj-card" id="little-widget"' in text
    assert text.index('id="little-widget"') < text.index("/projects/little-widget/deploy")


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


def _stub_in_flight_jobs(monkeypatch, jobs):
    monkeypatch.setattr(client, "list_jobs", lambda paths, status=None: [j for j in jobs if status is None or j["status"] == status])


def test_dashboard_card_shows_a_disabled_launching_button_while_a_deploy_is_under_way(test_client, monkeypatch):
    _stub_project_panels(
        monkeypatch, server={"listening": False, "version": "1", "unit": "inactive", "branch": "dev", "tailscale": "off"},
        session={"running": False, "trusted": True},
    )
    _stub_in_flight_jobs(monkeypatch, [{"type": "deployment.deploy", "project_id": "little-widget", "status": "running"}])
    text = test_client.get("/").text
    assert 'disabled data-starting="launch" data-project="little-widget">Launching…' in text
    assert 'data-busy="Launching…"' not in text
    assert "/projects/little-widget/session/start" in text  # only the launch is under way


def test_dashboard_card_shows_a_disabled_starting_button_while_a_session_starts(test_client, monkeypatch):
    _stub_project_panels(monkeypatch, session={"running": False, "trusted": True})
    _stub_in_flight_jobs(monkeypatch, [{"type": "agent.session_start", "project_id": "little-widget", "status": "queued"}])
    text = test_client.get("/").text
    assert 'disabled data-starting="session" data-project="little-widget">Starting session…' in text
    assert "/projects/little-widget/session/start" not in text


def test_the_page_after_a_launch_tap_already_shows_it_under_way(test_client, monkeypatch):
    """The deploy is submitted from a thread, so Orchestration may not list it yet when the redirect lands."""
    _stub_project_panels(monkeypatch, server={"listening": False, "version": "1", "unit": "inactive", "branch": "dev", "tailscale": "off"})
    _stub_in_flight_jobs(monkeypatch, [])
    release, finished = threading.Event(), threading.Event()

    def slow_create_job(paths, timeout=30.0, **fields):
        release.wait(timeout=5)
        return {"status": "succeeded"}

    monkeypatch.setattr(client, "create_job", slow_create_job)
    test_client.post("/projects/little-widget/deploy", data={"branch": "dev"}, follow_redirects=False)
    assert "Launching…</button>" in test_client.get("/").text
    assert test_client.get("/starting.json").json() == {"little-widget": ["launch"]}
    again = test_client.post("/projects/little-widget/deploy", data={"branch": "dev"}, follow_redirects=False)
    assert "already%20launching" in again.headers["location"]
    release.set()
    for _ in range(100):
        if test_client.get("/starting.json").json() == {}:
            finished.set()
            break
        threading.Event().wait(0.02)
    assert finished.is_set()


def test_deploy_is_refused_while_one_is_already_under_way(test_client, monkeypatch):
    _stub_in_flight_jobs(monkeypatch, [{"type": "deployment.deploy", "project_id": "little-widget", "status": "pending_confirmation"}])
    submitted = []
    monkeypatch.setattr(client, "create_job", lambda paths, **f: submitted.append(f) or {"status": "succeeded"})
    response = test_client.post("/projects/little-widget/deploy", data={"branch": "dev"}, follow_redirects=False)
    assert "already%20launching" in response.headers["location"] and submitted == []


def test_session_start_is_refused_while_one_is_already_under_way(test_client, monkeypatch):
    _stub_in_flight_jobs(monkeypatch, [{"type": "agent.session_start", "project_id": "little-widget", "status": "running"}])
    submitted = []
    monkeypatch.setattr(client, "create_job", lambda paths, **f: submitted.append(f) or {"status": "succeeded"})
    response = test_client.post("/projects/little-widget/session/start", follow_redirects=False)
    assert "already%20starting" in response.headers["location"] and submitted == []


def test_starting_json_ignores_finished_and_unrelated_jobs(test_client, monkeypatch):
    _stub_in_flight_jobs(monkeypatch, [
        {"type": "deployment.deploy", "project_id": "a", "status": "succeeded"},
        {"type": "build.run", "project_id": "b", "status": "running"},
        {"type": "agent.session_start", "project_id": "c", "status": "running"},
    ])
    assert test_client.get("/starting.json").json() == {"c": ["session"]}


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


def _job(i, **overrides):
    base = {
        "id": f"{i:08d}aaaabbbbccccdddd", "type": "deployment.redeploy_pillar", "target_pillar": "deployment", "project_id": "dashboard",
        "params": {"pillar": "dashboard", "commit": "abc123"}, "status": "succeeded", "queue_key": "dashboard", "depends_on": [],
        "created_by": "mcp", "result": {"version": "0.1.1"}, "error": None,
        "created_at": f"2026-09-21T10:{i:02d}:00.000000Z", "updated_at": f"2026-09-21T10:{i:02d}:02.500000Z",
    }
    base.update(overrides)
    return base


def _stub_jobs(monkeypatch, jobs):
    calls = []

    def fake_list_jobs(paths, status=None):
        calls.append(status)
        return list(jobs)

    monkeypatch.setattr(client, "list_jobs", fake_list_jobs)
    return calls


def test_jobs_lists_newest_first_as_expandable_cards(test_client, monkeypatch):
    _stub_jobs(monkeypatch, [_job(1, type="policy.configure"), _job(2, type="version.feature_finish"), _job(3, type="build.run")])
    text = test_client.get("/jobs").text
    assert text.count('class="jobcard ') == 3 and "<table" not in text and "<h1" not in text
    assert text.index("build.") < text.index("version.") < text.index("policy.")  # newest created_at first
    assert '<span class="job-ns">policy.</span>configure' in text
    assert "dashboard</span>" in text and "mcp" in text and "2.5s" in text


def test_a_job_card_expands_to_ids_timestamps_params_and_result(test_client, monkeypatch):
    _stub_jobs(monkeypatch, [_job(7, depends_on=["1234567890abcdef"], queue_key="dashboard")])
    text = test_client.get("/jobs").text
    assert "00000007aaaabbbbccccdddd" in text and "2026-09-21T10:07:00.000000Z" in text
    assert "Waits on" in text and "<code>12345678</code>" in text
    assert "&#34;commit&#34;: &#34;abc123&#34;" in text and "&#34;version&#34;: &#34;0.1.1&#34;" in text
    assert ">Params<" in text and ">Result<" in text


def test_failed_jobs_show_their_error_on_the_card_and_in_full_inside(test_client, monkeypatch):
    _stub_jobs(monkeypatch, [_job(1, status="failed", result=None, error="Gate 2 rejected: not enforceable <b>")])
    text = test_client.get("/jobs").text
    assert text.count("Gate 2 rejected: not enforceable &lt;b&gt;") == 2  # card summary + the Error block, escaped
    assert 'pill fail">failed' in text and ">Error<" in text


def test_status_chips_carry_counts_and_the_active_one_is_marked(test_client, monkeypatch):
    _stub_jobs(monkeypatch, [_job(1), _job(2), _job(3, status="failed"), _job(4, status="running", result=None), _job(5, status="pending_confirmation", result=None)])
    text = test_client.get("/jobs", params={"status": "failed"}).text
    assert 'class="fchip"' in text and 'class="fchip on" href="/jobs?status=failed"' in text
    for label, n in (("All", 5), ("Running", 1), ("Needs confirmation", 1), ("Failed", 1), ("Succeeded", 2)):
        assert re.search(rf'{label}<span class="fchip-n">{n}</span>', text), (label, n)
    assert "Queued" not in text  # no chip for a status nothing has
    assert text.count('class="jobcard ') == 1 and "pill fail" in text


def test_the_status_filter_is_applied_locally_so_counts_stay_whole(test_client, monkeypatch):
    calls = _stub_jobs(monkeypatch, [_job(1), _job(2, status="failed")])
    test_client.get("/jobs", params={"status": "failed"})
    assert [c for c in calls if c != "pending_confirmation"] == [None]  # the banner's own confirmation read is separate


def test_jobs_search_matches_type_project_pillar_creator_and_id(test_client, monkeypatch):
    _stub_jobs(monkeypatch, [
        _job(1, type="policy.configure", project_id=None, target_pillar="policy", created_by="dashboard"),
        _job(2, type="build.run", project_id="little-meals", target_pillar="build", created_by="lws"),
    ])
    def cards(query):
        return re.findall(r'class="jobcard s-\w+"', test_client.get("/jobs", params={"q": query}).text)
    assert len(cards("MEALS")) == 1 and len(cards("policy")) == 1 and len(cards("lws")) == 1 and len(cards("00000002")) == 1
    assert len(cards("nothing-like-this")) == 0
    assert "No jobs match." in test_client.get("/jobs", params={"q": "nothing-like-this"}).text


def test_search_and_status_combine_and_survive_in_the_chip_links(test_client, monkeypatch):
    _stub_jobs(monkeypatch, [_job(1, type="a.x", status="failed"), _job(2, type="a.y"), _job(3, type="b.z", status="failed")])
    text = test_client.get("/jobs", params={"status": "failed", "q": "a."}).text
    assert len(re.findall(r'class="jobcard ', text)) == 1
    assert '<span class="job-ns">a.</span>x' in text and '<span class="job-ns">a.</span>y' not in text and '<span class="job-ns">b.</span>' not in text
    assert 'href="/jobs?status=failed&amp;q=a."' in text or 'href="/jobs?status=failed&q=a."' in text
    assert 'name="status" value="failed"' in text and 'value="a."' in text


def test_jobs_are_paged_with_a_show_more_link(test_client, monkeypatch):
    _stub_jobs(monkeypatch, [_job(i % 60, id=f"id{i:04d}") for i in range(120)])
    first = test_client.get("/jobs").text
    assert first.count('class="jobcard ') == 50 and "Showing 50 of 120" in first and "limit=100" in first
    more = test_client.get("/jobs", params={"limit": 100}).text
    assert more.count('class="jobcard ') == 100 and "Showing 100 of 120" in more
    everything = test_client.get("/jobs", params={"limit": 150}).text
    assert everything.count('class="jobcard ') == 120 and "Show more" not in everything and "120 jobs" in everything


def test_show_more_keeps_the_filters(test_client, monkeypatch):
    _stub_jobs(monkeypatch, [_job(i % 60, id=f"id{i:04d}", status="failed", result=None) for i in range(80)])
    text = test_client.get("/jobs", params={"status": "failed", "q": "redeploy"}).text
    assert "limit=100&amp;status=failed&amp;q=redeploy" in text or "limit=100&status=failed&q=redeploy" in text


def test_the_page_refreshes_itself_only_while_jobs_are_active(test_client, monkeypatch):
    _stub_jobs(monkeypatch, [_job(1, status="running", result=None)])
    busy = test_client.get("/jobs").text
    assert "setInterval" in busy and 'class="live"' in busy and 'id="jobs-live"' in busy and "pill run" in busy

    _stub_jobs(monkeypatch, [_job(1), _job(2, status="failed")])
    idle = test_client.get("/jobs").text
    assert "setInterval" not in idle and 'class="live"' not in idle


def test_a_queued_and_a_pending_confirmation_job_count_as_active(test_client, monkeypatch):
    for status in ("queued", "pending_confirmation"):
        _stub_jobs(monkeypatch, [_job(1, status=status, result=None)])
        assert "setInterval" in test_client.get("/jobs").text


def test_empty_states(test_client, monkeypatch):
    _stub_jobs(monkeypatch, [])
    assert "No jobs yet." in test_client.get("/jobs").text


def test_a_job_with_no_dotted_type_still_renders(test_client, monkeypatch):
    _stub_jobs(monkeypatch, [_job(1, type="ping", params={}, result=None)])
    text = test_client.get("/jobs").text
    assert '<span class="job-type">ping</span>' in text and ">Params<" not in text and ">Result<" not in text


def test_the_meta_line_skips_missing_parts_instead_of_showing_stray_dots(test_client, monkeypatch):
    _stub_jobs(monkeypatch, [_job(1, status="cancelled", created_at="not-a-time", updated_at="also-not", created_by="mcp")])
    text = test_client.get("/jobs").text
    assert '<span class="job-meta">mcp</span>' in text and "· ·" not in text


def test_relative_time_and_duration_helpers():
    from datetime import datetime, timezone
    from dashboard.app import job_duration, relative_time

    now = datetime(2026, 9, 21, 12, 0, 0, tzinfo=timezone.utc)
    assert relative_time("2026-09-21T11:59:50Z", now=now) == "just now"
    assert relative_time("2026-09-21T11:55:00Z", now=now) == "5m ago"
    assert relative_time("2026-09-21T09:00:00Z", now=now) == "3h ago"
    assert relative_time("2026-09-19T09:00:00Z", now=now) == "2d ago"
    assert relative_time(None) == "" and relative_time("garbage") == ""
    assert job_duration("2026-09-21T10:00:00Z", "2026-09-21T10:00:00.4Z") == "0.4s"
    assert job_duration("2026-09-21T10:00:00Z", "2026-09-21T10:00:12Z") == "12s"
    assert job_duration("2026-09-21T10:00:00Z", "2026-09-21T10:03:05Z") == "3m 05s"
    assert job_duration("2026-09-21T10:00:00Z", "2026-09-21T11:02:00Z") == "1h 02m"
    assert job_duration(None, "2026-09-21T11:02:00Z") == "" and job_duration("2026-09-21T11:02:00Z", "2026-09-21T10:00:00Z") == "0.0s"


def _policy(**overrides):
    base = {
        "id": "pol1", "topic": "project-has-name", "ears_text": "every project shall have a name", "enabled": True,
        "gate_points": ["server_launch", "release"], "scope_level": "all_projects", "scope_target": None,
        "target_pillar": "registry", "created_by": "framework",
        "projects": [{"project_id": "proj-a", "gating_enabled": True}, {"project_id": "proj-b", "gating_enabled": False}],
    }
    base.update(overrides)
    return base


def test_policies_hides_the_gating_chip_when_nothing_is_gating_on(test_client, monkeypatch):
    monkeypatch.setattr(client, "policy_list", lambda paths: [
        _policy(projects=[{"project_id": "proj-a", "gating_enabled": False}, {"project_id": "proj-b", "gating_enabled": False}]),
    ])
    assert "gating on" not in test_client.get("/policies").text


def test_policies_renders_one_card_per_policy_with_chips(test_client, monkeypatch):
    monkeypatch.setattr(client, "policy_list", lambda paths: [
        _policy(),
        _policy(id="pol2", topic="keep-docs", ears_text="docs shall exist", enabled=False, gate_points=[],
                created_by="user", projects=[{"project_id": "little-widget", "gating_enabled": True}]),
    ])
    response = test_client.get("/policies")
    text = response.text
    assert response.status_code == 200 and "<h1" not in text
    assert len(re.findall(r'class="polcard[ "]', text)) == 2
    assert 'href="/policies/pol1"' in text and 'href="/policies/pol2"' in text
    assert "project-has-name" in text and "every project shall have a name" in text
    assert '<span class="chip gate">server_launch</span>' in text and '<span class="chip gate">release</span>' in text
    assert ">no gate<" in text
    assert "2 projects" in text and "gating on: 1" in text and "gating off" not in text
    assert "1 project: little-widget" in text
    assert "polcard is-off" in text and "disabled" in text
    assert "aiver" not in text and "Waivers" not in text


def test_policies_with_no_projects_selected_says_zero(test_client, monkeypatch):
    monkeypatch.setattr(client, "policy_list", lambda paths: [_policy(projects=[])])
    assert "0 projects" in test_client.get("/policies").text


def test_policies_empty_state(test_client, monkeypatch):
    monkeypatch.setattr(client, "policy_list", lambda paths: [])
    assert "No policies authored yet." in test_client.get("/policies").text


def test_policies_card_shows_failing_count_from_associated_projects_only(test_client, monkeypatch):
    monkeypatch.setattr(client, "policy_list", lambda paths: [
        _policy(projects=[{"project_id": "proj-a", "gating_enabled": True}, {"project_id": "proj-b", "gating_enabled": True}]),
    ])
    monkeypatch.setattr(client, "policy_check_results", lambda paths: [
        {"policy_id": "pol1", "project_id": "proj-a", "outcome": "fail", "detail": "boom", "checked_at": "2026-09-23T10:00:00Z"},
        {"policy_id": "pol1", "project_id": "proj-b", "outcome": "pass", "detail": "", "checked_at": "2026-09-23T10:00:00Z"},
        # Not associated with pol1's own projects list -- must not inflate the count.
        {"policy_id": "pol1", "project_id": "proj-c", "outcome": "fail", "detail": "", "checked_at": "2026-09-23T10:00:00Z"},
    ])
    text = test_client.get("/policies").text
    assert "1 failing" in text
    assert "2 failing" not in text


def test_policies_card_shows_enabled_when_nothing_is_failing(test_client, monkeypatch):
    monkeypatch.setattr(client, "policy_list", lambda paths: [_policy()])
    monkeypatch.setattr(client, "policy_check_results", lambda paths: [])
    text = test_client.get("/policies").text
    assert "failing" not in text and ">enabled<" in text


def _stub_detail(monkeypatch, *, policy=None, registered=("proj-a", "proj-b", "proj-c"), link=None):
    detail = {**_policy(id="0123456789abcdef", topic="commit-bumps-version", ears_text="a commit shall bump the version",
                        created_by="user", enabled=False, gate_points=["branch_finish", "release"],
                        created_at="2026-09-16T10:00:00Z", amended_at="2026-09-21T15:09:42Z",
                        test_script="line1\nline2\nline3\n"), **(policy or {})}
    monkeypatch.setattr(client, "policy_get", lambda paths, policy_id: detail)
    monkeypatch.setattr(client, "list_projects", lambda paths, status=None: [{"name": n, "status": "active"} for n in registered])
    monkeypatch.setattr(client, "policy_script_link", lambda paths, script: link)


def test_policy_detail_is_built_from_cards_not_fieldsets(test_client, monkeypatch):
    _stub_detail(monkeypatch)
    text = test_client.get("/policies/pol1").text
    assert "<fieldset" not in text and "<legend" not in text and "<h1" not in text
    assert text.count('class="dcard') >= 4  # summary, gate points, test script, projects
    assert "commit-bumps-version" in text and "disabled" in text
    assert "01234567" in text and "amended 2026-09-21" in text
    assert 'action="/policies/0123456789abcdef/configure"' in text
    assert 'name="enabled" value="true"' in text and 'name="gate_points" value="branch_finish" checked' in text
    assert 'name="gate_points" value="release" checked' in text and 'name="gate_points" value="server_stop" checked' not in text
    assert "aiver" not in text


def test_policy_detail_lists_every_registered_project_with_selection_and_gating(test_client, monkeypatch):
    _stub_detail(monkeypatch)
    text = test_client.get("/policies/pol1").text
    assert 'action="/policies/0123456789abcdef/projects"' in text
    for name in ("proj-a", "proj-b", "proj-c"):
        assert f'name="project" value="{name}"' in text
    assert 'name="project" value="proj-a" checked' in text and 'name="project" value="proj-b" checked' in text
    assert 'name="project" value="proj-c" checked' not in text
    assert 'name="gating" value="proj-a" checked' in text and 'name="gating" value="proj-b" checked' not in text
    assert "2 of 3" in text
    assert "unregistered" not in text
    assert "Project governance" in text and ">Projects<" not in text


def test_policy_detail_panels_are_collapsible_in_order(test_client, monkeypatch):
    _stub_detail(monkeypatch)
    text = test_client.get("/policies/pol1").text
    titles_in_order = re.findall(r'class="dcard-title">([^<]+)<', text)
    assert titles_in_order == ["Policy description", "Policy status", "Gate points", "Test script", "Project governance"]
    assert text.count('class="dcard collapsible"') >= 4  # description, status, gate points, project governance


def test_policy_detail_status_panel_shows_pass_fail_and_stdout_on_failure(test_client, monkeypatch):
    _stub_detail(monkeypatch)
    monkeypatch.setattr(client, "policy_check_results", lambda paths: [
        {"policy_id": "0123456789abcdef", "project_id": "proj-a", "outcome": "fail",
         "detail": "AssertionError: boom", "checked_at": "2026-09-23T10:00:00Z"},
        {"policy_id": "0123456789abcdef", "project_id": "proj-b", "outcome": "pass",
         "detail": "", "checked_at": "2026-09-23T10:00:00Z"},
    ])
    text = test_client.get("/policies/pol1").text
    assert "1 failing" in text
    assert "AssertionError: boom" in text
    assert '<pre class="code err">' in text
    assert "proj-a" in text and "proj-b" in text


def test_policy_detail_status_panel_says_all_passing_when_nothing_fails(test_client, monkeypatch):
    _stub_detail(monkeypatch)
    monkeypatch.setattr(client, "policy_check_results", lambda paths: [
        {"policy_id": "0123456789abcdef", "project_id": "proj-a", "outcome": "pass", "detail": "", "checked_at": "2026-09-23T10:00:00Z"},
        {"policy_id": "0123456789abcdef", "project_id": "proj-b", "outcome": "pass", "detail": "", "checked_at": "2026-09-23T10:00:00Z"},
    ])
    text = test_client.get("/policies/pol1").text
    assert "all passing" in text
    assert "failing" not in text
    assert "<pre class=\"code err\">" not in text


def test_policy_detail_status_panel_survives_no_check_results_yet(test_client, monkeypatch):
    _stub_detail(monkeypatch)
    monkeypatch.setattr(client, "policy_check_results", lambda paths: [])
    text = test_client.get("/policies/pol1").text
    assert "no data yet" in text


def test_policy_detail_keeps_a_selected_project_that_is_no_longer_registered(test_client, monkeypatch):
    _stub_detail(monkeypatch, registered=("proj-a",))
    text = test_client.get("/policies/pol1").text
    assert 'name="project" value="proj-b" checked' in text and "unregistered" in text


def test_policy_detail_script_is_a_github_link_when_a_committed_file_matches(test_client, monkeypatch):
    link = {"found": True, "path": "tools/policy_scripts/commit_bumps_version.py", "version": "0.1.1-dev.2",
            "commit": "ad943c6e1fcfc66482282a2ec9c2b81162d5c38a",
            "url": "https://github.com/dfluxcbf/little-workshop/blob/ad943c6e/tools/policy_scripts/commit_bumps_version.py"}
    _stub_detail(monkeypatch, link=link)
    text = test_client.get("/policies/pol1").text
    assert f'href="{link["url"]}"' in text and 'target="_blank"' in text and 'rel="noopener noreferrer"' in text
    assert "v0.1.1-dev.2" in text and "ad943c6" in text and "tools/policy_scripts/commit_bumps_version.py" in text
    assert "<pre" not in text  # the code itself is not inlined


def test_policy_detail_script_falls_back_to_inline_code_when_no_file_matches(test_client, monkeypatch):
    _stub_detail(monkeypatch, link=None)
    text = test_client.get("/policies/pol1").text
    assert "<pre" in text and "line2" in text and "db only" in text and "3 lines" in text


def test_policy_detail_survives_deployment_being_down(test_client, monkeypatch):
    _stub_detail(monkeypatch)

    def down(paths, script):
        raise client.PillarUnavailable("deployment daemon unreachable")

    monkeypatch.setattr(client, "policy_script_link", down)
    response = test_client.get("/policies/pol1")
    assert response.status_code == 200 and "<pre" in response.text


def test_policy_detail_without_projects_selected(test_client, monkeypatch):
    _stub_detail(monkeypatch, policy={"projects": [], "scope_level": "all_projects"})
    text = test_client.get("/policies/pol1").text
    assert "0 of 3" in text and 'name="project" value="proj-a" checked' not in text


def test_policy_detail_not_found_renders_a_message_not_a_500(test_client, monkeypatch):
    monkeypatch.setattr(client, "policy_get", lambda paths, policy_id: (_ for _ in ()).throw(client.PillarUnavailable("policy daemon unreachable")))
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


def test_policy_projects_post_sends_the_whole_selection(test_client, monkeypatch):
    captured = {}

    def fake_create_job(paths, **fields):
        captured.update(fields)
        return {"status": "succeeded"}

    monkeypatch.setattr(client, "create_job", fake_create_job)
    response = test_client.post(
        "/policies/pol1/projects", data={"project": ["proj-a", "proj-b", "proj-a"], "gating": ["proj-a", "proj-z"]},
        follow_redirects=False,
    )
    assert response.status_code == 303 and response.headers["location"] == "/policies/pol1"
    assert captured["type"] == "policy.set_projects"
    assert captured["params"] == {"policy_id": "pol1", "projects": [
        {"project_id": "proj-a", "gating_enabled": True}, {"project_id": "proj-b", "gating_enabled": False},
    ]}


def test_policy_projects_post_with_nothing_selected_deselects_everything(test_client, monkeypatch):
    captured = {}
    monkeypatch.setattr(client, "create_job", lambda paths, **fields: (captured.update(fields), {"status": "succeeded"})[1])
    test_client.post("/policies/pol1/projects", data={}, follow_redirects=False)
    assert captured["params"] == {"policy_id": "pol1", "projects": []}


def test_waiver_routes_are_gone(test_client):
    assert test_client.post("/policies/pol1/waivers/grant", data={"project_id": "p", "reason": "r"}).status_code == 404


def test_stylesheet_carries_no_violet(test_client):
    """presentations/06-visual-identity.md: cyan is the only accent; Mission
    Control's violet (oklch hue ~320) is deliberately not part of this identity."""
    import re

    css = test_client.get("/static/app.css").text
    assert "--violet" not in css
    assert not re.search(r"oklch\([^)]*\s3[0-4]\d(\s|/|\))", css), "a violet-hued color crept back in"


def test_service_worker_cache_name_is_bumped_with_the_icons(test_client):
    assert "workshop-shell-v4" in test_client.get("/sw.js").text


def test_every_icon_fills_its_whole_canvas_with_the_dark_ground():
    """Regression: the 192/180/64px icons were once rendered with the
    background rect left at 512 units, leaving most of the canvas white."""
    import importlib.resources
    import io

    Image = pytest.importorskip("PIL.Image")  # not a project dependency; skips where absent

    icons = importlib.resources.files("dashboard") / "static" / "icons"
    for name in ("icon-192", "icon-512", "icon-192-maskable", "icon-512-maskable", "apple-touch-icon", "favicon-64"):
        image = Image.open(io.BytesIO((icons / f"{name}.png").read_bytes())).convert("RGB")
        w, h = image.size
        for corner in ((2, 2), (w - 3, 2), (2, h - 3), (w - 3, h - 3)):
            r, g, b = image.getpixel(corner)
            assert max(r, g, b) < 60, f"{name} corner {corner} is not the dark ground: {(r, g, b)}"


_TWO_PILLARS = {
    "registry": {"status": "healthy", "crash_count": 0, "interface_version": "v1"},
    "policy": {"status": "healthy", "crash_count": 0, "interface_version": "v1"},
    "build": {"status": "disabled", "crash_count": 0, "interface_version": "v1"},
}


def test_pillars_page_shows_cards_with_sha_version_and_a_commit_dropdown(test_client, monkeypatch):
    sha = "0123456789abcdef0123456789abcdef01234567"
    newer = "f" * 40
    monkeypatch.setattr(client, "core_status", lambda paths: {"pillars": _TWO_PILLARS})
    monkeypatch.setattr(client, "deployment_pillars", lambda paths: {
        "pillars": {"policy": {"commit_sha": sha, "updated_at": "2026-09-21"}}, "repo_head": newer,
        "commits": [
            {"sha": newer, "subject": "Add the picker", "version": "0.2.0"},
            {"sha": sha, "subject": "Older work", "version": "0.1.0"},
        ],
    })
    text = test_client.get("/pillars").text
    assert "<h1" not in text
    assert sha[:7] in text and "v0.1.0" in text and ">behind<" in text
    assert "initial install" in text
    assert 'value="registry"' in text and 'value="policy"' in text and 'value="build"' not in text
    assert f'<option value="{newer}">' in text and "v0.2.0" in text and "(HEAD)" in text
    assert f'<option value="{sha}">' in text
    assert "Redeploy" in text and "<input type=\"text\"" not in text


def test_pillars_page_falls_back_to_head_when_no_commit_list(test_client, monkeypatch):
    monkeypatch.setattr(client, "core_status", lambda paths: {"pillars": _TWO_PILLARS})
    monkeypatch.setattr(client, "deployment_pillars", lambda paths: {"pillars": {}, "repo_head": None})
    assert '<option value="HEAD">' in test_client.get("/pillars").text


def test_pillars_page_survives_deployment_being_down(test_client, monkeypatch):
    monkeypatch.setattr(client, "core_status", lambda paths: {"pillars": _TWO_PILLARS})

    def down(paths):
        raise client.PillarUnavailable("deployment daemon unreachable")

    monkeypatch.setattr(client, "deployment_pillars", down)
    response = test_client.get("/pillars")
    assert response.status_code == 200 and "registry" in response.text


def _capture_jobs(monkeypatch):
    jobs: list[dict] = []
    done = threading.Event()

    def fake_create_job(paths, timeout=30.0, **fields):
        jobs.append(fields)
        if len(jobs) >= 3:
            done.set()
        return {"status": "succeeded"}

    monkeypatch.setattr(client, "create_job", fake_create_job)
    return jobs, done


def test_redeploy_submits_one_job_per_selected_pillar_self_affecting_last(test_client, monkeypatch):
    pillars = {**_TWO_PILLARS, "deployment": {"status": "healthy", "crash_count": 0}}
    monkeypatch.setattr(client, "core_status", lambda paths: {"pillars": pillars})
    jobs, done = _capture_jobs(monkeypatch)
    response = test_client.post(
        "/pillars/redeploy", data={"pillar": ["deployment", "registry", "policy", "build", "nope"], "commit": "abc123"},
        follow_redirects=False,
    )
    assert response.status_code == 303 and "started" in response.headers["location"]
    assert done.wait(5)
    assert [j["params"]["pillar"] for j in jobs] == ["policy", "registry", "deployment"]
    assert all(j["type"] == "deployment.redeploy_pillar" and j["params"]["commit"] == "abc123" for j in jobs)
    assert [j["queue_key"] for j in jobs] == ["policy", "registry", "deployment"]


def test_redeploy_requires_a_selection(test_client, monkeypatch):
    monkeypatch.setattr(client, "core_status", lambda paths: {"pillars": _TWO_PILLARS})
    jobs, _ = _capture_jobs(monkeypatch)
    response = test_client.post("/pillars/redeploy", data={}, follow_redirects=False)
    assert response.status_code == 303 and "Select" in unquote(response.headers["location"])
    assert jobs == []


def test_redeploy_reports_core_being_unreachable(test_client, monkeypatch):
    def down(paths):
        raise client.PillarUnavailable("Core daemon unreachable")

    monkeypatch.setattr(client, "core_status", down)
    response = test_client.post("/pillars/redeploy", data={"pillar": "registry"}, follow_redirects=False)
    assert response.status_code == 303 and "Core daemon unreachable" in unquote(response.headers["location"])


# -- drafts awaiting approval ------------------------------------------------------


def _draft_row(**overrides):
    base = {
        "id": "d1", "topic": "version-format", "ears_text": "The VERSION file shall be semver.", "source": "little-versions",
        "target_pillar": "version", "concerns": False, "ears_refined": True, "notes": "Straightforward.", "status": "pending",
        "reject_reason": "", "test_script": "def test_x():\n    assert True\n", "gate_points": [], "created_by": "framework",
        "scope_level": "all_projects", "policy_id": None, "decided_at": None,
    }
    base.update(overrides)
    return base


def _stub_drafts(monkeypatch, drafts):
    monkeypatch.setattr(client, "policy_drafts", lambda paths, status=None: [d for d in drafts if status is None or d["status"] == status])
    monkeypatch.setattr(client, "policy_draft", lambda paths, draft_id: next((d for d in drafts if d["id"] == draft_id), None))
    monkeypatch.setattr(client, "list_projects", lambda paths, status=None: [{"name": n, "status": "active"} for n in ("proj-a", "proj-b")])


def test_policies_page_announces_drafts_awaiting_approval(test_client, monkeypatch):
    monkeypatch.setattr(client, "policy_list", lambda paths: [])
    _stub_drafts(monkeypatch, [_draft_row(id="d1"), _draft_row(id="d2", topic="b"), _draft_row(id="d3", topic="c", status="rejected")])
    text = test_client.get("/policies").text
    assert 'href="/policies/drafts"' in text and "2</span>" in text and "policies awaiting your approval" in text


def test_policies_page_has_no_banner_when_nothing_is_pending(test_client, monkeypatch):
    monkeypatch.setattr(client, "policy_list", lambda paths: [])
    _stub_drafts(monkeypatch, [])
    assert "awaiting your approval" not in test_client.get("/policies").text


def test_policies_page_survives_the_draft_lookup_failing(test_client, monkeypatch):
    monkeypatch.setattr(client, "policy_list", lambda paths: [])

    def down(paths, status=None):
        raise client.PillarUnavailable("policy down")

    monkeypatch.setattr(client, "policy_drafts", down)
    response = test_client.get("/policies")
    assert response.status_code == 200 and "awaiting your approval" not in response.text


def test_drafts_list_shows_pending_cards_and_a_rejected_section(test_client, monkeypatch):
    _stub_drafts(monkeypatch, [
        _draft_row(id="d1"), _draft_row(id="d2", topic="production-tag-format", concerns=True),
        _draft_row(id="d3", topic="old-idea", status="rejected", reject_reason="not needed"),
    ])
    text = test_client.get("/policies/drafts").text
    assert 'href="/policies/drafts/d1"' in text and 'href="/policies/drafts/d2"' in text
    assert "check notes" in text and "to review" in text and "little-versions" in text and "refined" in text
    assert "Rejected (1)" in text and "not needed" in text and "/policies/drafts/d3/restore" in text
    assert 'href="/policies/drafts/d3"' not in text


def test_drafts_list_empty_state(test_client, monkeypatch):
    _stub_drafts(monkeypatch, [])
    assert "Nothing is waiting for approval." in test_client.get("/policies/drafts").text


def test_drafts_route_is_not_mistaken_for_a_policy_id(test_client, monkeypatch):
    _stub_drafts(monkeypatch, [])
    monkeypatch.setattr(client, "policy_get", lambda paths, policy_id: (_ for _ in ()).throw(AssertionError("read as a policy id")))
    assert test_client.get("/policies/drafts").status_code == 200


def test_draft_review_page_shows_everything_a_reviewer_needs_and_the_confirmation(test_client, monkeypatch):
    _stub_drafts(monkeypatch, [_draft_row(concerns=True, notes="Audits every tag in history.")])
    text = test_client.get("/policies/drafts/d1").text
    assert "version-format" in text and "The VERSION file shall be semver." in text and "little-versions" in text
    assert "Audits every tag in history." in text and "needs attention" in text
    assert "def test_x" in text and "2 lines" in text
    for gate_point in ("branch_finish", "release", "build_install"):
        assert f'name="gate_points" value="{gate_point}"' in text
    assert 'name="project" value="proj-a"' in text and 'name="project" value="proj-b"' in text
    assert 'name="gating" value="proj-a" checked' in text
    assert 'name="reviewed"' not in text and 'action="/policies/drafts/d1/approve"' in text
    assert 'formaction="/policies/drafts/d1/reject"' in text
    assert "<fieldset" not in text and "<h1" not in text


def test_draft_review_page_preselects_the_drafts_own_gate_points(test_client, monkeypatch):
    _stub_drafts(monkeypatch, [_draft_row(gate_points=["release"])])
    text = test_client.get("/policies/drafts/d1").text
    assert 'name="gate_points" value="release" checked' in text and 'name="gate_points" value="build_install" checked' not in text


def _checked(text, name, value):
    match = re.search(rf'name="{name}" value="{value}"( checked)?', text)
    assert match, f"{name}={value} not found"
    return bool(match.group(1))


def test_draft_review_page_preselects_the_drafts_own_project_selection(test_client, monkeypatch):
    _stub_drafts(monkeypatch, [_draft_row(projects=[{"project_id": "proj-a", "gating_enabled": False}])])
    text = test_client.get("/policies/drafts/d1").text
    assert _checked(text, "project", "proj-a") is True
    assert _checked(text, "project", "proj-b") is False  # not pre-checked: absent from the draft's own selection
    assert _checked(text, "gating", "proj-a") is False
    assert _checked(text, "gating", "proj-b") is True  # no recommendation for proj-b: defaults to gating on
    assert "pre-filled from the draft's own recommendation" in text


def test_draft_review_page_with_no_recommendation_leaves_projects_unchecked(test_client, monkeypatch):
    _stub_drafts(monkeypatch, [_draft_row()])
    text = test_client.get("/policies/drafts/d1").text
    assert _checked(text, "project", "proj-a") is False and _checked(text, "project", "proj-b") is False
    assert "pre-filled from the draft's own recommendation" not in text


def test_a_decided_draft_offers_no_approve_form(test_client, monkeypatch):
    _stub_drafts(monkeypatch, [
        _draft_row(id="a", status="approved", policy_id="pol9"), _draft_row(id="r", status="rejected", reject_reason="too vague"),
    ])
    approved = test_client.get("/policies/drafts/a").text
    assert 'href="/policies/pol9"' in approved and "/approve" not in approved and "approved" in approved
    rejected = test_client.get("/policies/drafts/r").text
    assert "too vague" in rejected and "/policies/drafts/r/restore" in rejected and "/approve" not in rejected


def test_unknown_draft_says_so_and_unreachable_policy_shows_the_banner(test_client, monkeypatch):
    _stub_drafts(monkeypatch, [])
    assert "Draft not found." in test_client.get("/policies/drafts/ghost").text

    def down(paths, draft_id):
        raise client.PillarUnavailable("policy daemon unreachable")

    monkeypatch.setattr(client, "policy_draft", down)
    response = test_client.get("/policies/drafts/d1")
    assert response.status_code == 200 and "UNREACHABLE" in response.text


def test_approve_needs_only_the_button_and_advances_to_the_next_pending_draft(test_client, monkeypatch):
    captured = {}

    def fake_create_job(paths, **fields):
        captured.update(fields)
        return {"status": "succeeded", "result": {"id": "pol-new"}}

    monkeypatch.setattr(client, "create_job", fake_create_job)
    monkeypatch.setattr(client, "policy_drafts", lambda paths, status=None: [_draft_row(id="d1"), _draft_row(id="d2", topic="next-one")])
    response = test_client.post(
        "/policies/drafts/d1/approve",
        data={"gate_points": ["branch_finish"], "project": ["proj-a", "proj-b", "proj-a"], "gating": ["proj-a"]},
        follow_redirects=False,
    )
    assert response.status_code == 303 and response.headers["location"].startswith("/policies/drafts/d2?message=")
    assert captured["type"] == "policy.approve_draft"
    assert captured["params"] == {
        "draft_id": "d1", "reviewed": True, "gate_points": ["branch_finish"],
        "projects": [{"project_id": "proj-a", "gating_enabled": True}, {"project_id": "proj-b", "gating_enabled": False}],
    }


def test_approve_with_nothing_chosen_sends_no_gate_points_and_no_projects(test_client, monkeypatch):
    captured = {}
    monkeypatch.setattr(client, "create_job", lambda paths, **fields: (captured.update(fields), {"status": "succeeded", "result": {"id": "p"}})[1])
    monkeypatch.setattr(client, "policy_drafts", lambda paths, status=None: [])
    test_client.post("/policies/drafts/d1/approve", data={}, follow_redirects=False)
    assert captured["params"]["gate_points"] == [] and captured["params"]["projects"] == []


def test_approving_the_last_pending_draft_returns_to_the_policies_page(test_client, monkeypatch):
    monkeypatch.setattr(client, "create_job", lambda paths, **fields: {"status": "succeeded", "result": {"id": "pol-new"}})
    monkeypatch.setattr(client, "policy_drafts", lambda paths, status=None: [])
    response = test_client.post("/policies/drafts/d1/approve", data={}, follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"].startswith("/policies?message=")
    assert "No more drafts" in unquote(response.headers["location"])


def test_a_refused_approval_returns_to_the_draft_with_the_reason(test_client, monkeypatch):
    monkeypatch.setattr(client, "create_job", lambda paths, **fields: {"status": "failed", "error": "Gate 3a rejected: duplicate"})
    response = test_client.post("/policies/drafts/d1/approve", data={}, follow_redirects=False)
    location = unquote(response.headers["location"])
    assert location.startswith("/policies/drafts/d1?message=") and "Gate 3a rejected: duplicate" in location


def test_approve_reports_the_policy_pillar_being_unreachable(test_client, monkeypatch):
    def down(paths, **fields):
        raise client.PillarUnavailable("orchestration daemon unreachable")

    monkeypatch.setattr(client, "create_job", down)
    response = test_client.post("/policies/drafts/d1/approve", data={}, follow_redirects=False)
    assert "Could not approve" in unquote(response.headers["location"])


def test_reject_and_restore_post_the_right_jobs(test_client, monkeypatch):
    seen = []
    monkeypatch.setattr(client, "create_job", lambda paths, **fields: (seen.append(fields), {"status": "succeeded"})[1])
    rejected = test_client.post("/policies/drafts/d1/reject", data={"reason": "too vague"}, follow_redirects=False)
    restored = test_client.post("/policies/drafts/d1/restore", follow_redirects=False)
    assert rejected.status_code == 303 and rejected.headers["location"].startswith("/policies/drafts?message=")
    assert restored.status_code == 303 and restored.headers["location"].startswith("/policies/drafts?message=")
    assert (seen[0]["type"], seen[0]["params"]) == ("policy.reject_draft", {"draft_id": "d1", "reason": "too vague"})
    assert (seen[1]["type"], seen[1]["params"]) == ("policy.restore_draft", {"draft_id": "d1"})


def test_reject_and_restore_report_failures(test_client, monkeypatch):
    monkeypatch.setattr(client, "create_job", lambda paths, **fields: {"status": "failed", "error": "draft is approved, not pending"})
    reject = test_client.post("/policies/drafts/d1/reject", data={}, follow_redirects=False)
    restore = test_client.post("/policies/drafts/d1/restore", follow_redirects=False)
    assert "not pending" in unquote(reject.headers["location"]) and "Could not restore" in unquote(restore.headers["location"])


def test_body_has_no_viewport_height_minimum_that_makes_short_pages_scroll(test_client):
    css = test_client.get("/static/app.css").text
    body_rule = css[css.index("\nbody {"):css.index("}", css.index("\nbody {"))]
    assert "min-height" not in body_rule.replace("/* No min-height", "")
    assert "background-attachment: fixed" in body_rule  # what keeps the whole screen painted


# -- delete old jobs -------------------------------------------------------------------


def _stub_cleanup(monkeypatch, counts):
    monkeypatch.setattr(client, "job_cleanup_preview", lambda paths, days: counts)


def test_jobs_page_offers_the_six_ages_with_how_many_each_would_delete(test_client, monkeypatch):
    _stub_jobs(monkeypatch, [_job(1)])
    _stub_cleanup(monkeypatch, {"12": 400, "48": 312, "168": 187, "672": 24, "1344": 1, "4032": 0})
    text = test_client.get("/jobs").text
    assert 'action="/jobs/cleanup"' in text and "Delete old jobs" in text
    for days, label, count in ((12, "12 hours", 400), (48, "2 days", 312), (168, "1 week", 187), (672, "4 weeks", 24), (1344, "8 weeks", 1), (4032, "24 weeks", 0)):
        assert f'name="hours" value="{days}" data-count="{count}" data-label="{label}"' in text
        assert f'<span>{label}</span><span class="age-n">{count}</span>' in text
    assert re.search(r'value="4032" data-count="0" data-label="24 weeks" disabled', text)  # nothing to delete: not selectable
    assert not re.search(r'value="168" data-count="187" data-label="1 week" disabled', text)
    assert "never deleted" in text and "confirm(" in text


def test_the_delete_panel_is_absent_when_there_are_no_jobs(test_client, monkeypatch):
    _stub_jobs(monkeypatch, [])
    assert "Delete old jobs" not in test_client.get("/jobs").text


def test_the_page_still_renders_when_the_cleanup_preview_fails(test_client, monkeypatch):
    _stub_jobs(monkeypatch, [_job(1)])

    def down(paths, days):
        raise client.PillarUnavailable("orchestration daemon unreachable")

    monkeypatch.setattr(client, "job_cleanup_preview", down)
    response = test_client.get("/jobs")
    assert response.status_code == 200 and 'data-count="0"' in response.text


def test_cleanup_deletes_and_reports_how_many(test_client, monkeypatch):
    seen = []
    monkeypatch.setattr(client, "job_cleanup", lambda paths, days: (seen.append(days), {"deleted": 187, "pipeline_runs_deleted": 2, "kept_for_safety": 0})[1])
    response = test_client.post("/jobs/cleanup", data={"hours": "168"}, follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"].startswith("/jobs?message=")
    assert seen == [168] and "Deleted 187 jobs older than 1 week." in unquote(response.headers["location"])


@pytest.mark.parametrize("days,label", [(12, "12 hours"), (48, "2 days"), (168, "1 week"), (672, "4 weeks"), (1344, "8 weeks"), (4032, "24 weeks")])
def test_every_offered_age_is_accepted(test_client, monkeypatch, days, label):
    monkeypatch.setattr(client, "job_cleanup", lambda paths, d: {"deleted": 1, "kept_for_safety": 0})
    location = unquote(test_client.post("/jobs/cleanup", data={"hours": str(days)}, follow_redirects=False).headers["location"])
    assert f"Deleted 1 job older than {label}." in location


def test_cleanup_mentions_jobs_kept_because_active_work_needs_them(test_client, monkeypatch):
    monkeypatch.setattr(client, "job_cleanup", lambda paths, days: {"deleted": 4, "kept_for_safety": 3})
    location = unquote(test_client.post("/jobs/cleanup", data={"hours": "672"}, follow_redirects=False).headers["location"])
    assert "Deleted 4 jobs older than 4 weeks." in location and "3 kept because active work still depends on them" in location


@pytest.mark.parametrize("data", [{}, {"hours": ""}, {"hours": "3"}, {"hours": "0"}, {"hours": "-7"}, {"hours": "9999"}])
def test_cleanup_refuses_anything_but_an_offered_age_and_deletes_nothing(test_client, monkeypatch, data):
    calls = []
    monkeypatch.setattr(client, "job_cleanup", lambda paths, days: calls.append(days))
    response = test_client.post("/jobs/cleanup", data=data, follow_redirects=False)
    assert response.status_code in (303, 422)
    if response.status_code == 303:
        assert "Choose how old" in unquote(response.headers["location"])
    assert calls == []


def test_cleanup_reports_orchestration_being_unreachable(test_client, monkeypatch):
    def down(paths, days):
        raise client.PillarUnavailable("orchestration daemon unreachable")

    monkeypatch.setattr(client, "job_cleanup", down)
    location = unquote(test_client.post("/jobs/cleanup", data={"hours": "168"}, follow_redirects=False).headers["location"])
    assert "Could not delete jobs" in location and "unreachable" in location


@pytest.mark.parametrize("selector", [".pcard", ".gp", ".prow-select", ".prow-gate", ".switch-row"])
def test_label_based_components_opt_out_of_the_global_form_label_style(test_client, selector):
    """These are <label>s (a tap selects them) but not form-field labels; without an explicit
    reset they inherit the global `label` rule's uppercase text and 16px top margin."""
    css = test_client.get("/static/app.css").text
    rule = re.search(rf"(?m)^{re.escape(selector)} \{{(.*?)\n\}}|^{re.escape(selector)} \{{([^}}]*)\}}", css, re.S)
    assert rule, f"no rule for {selector}"
    body = rule.group(1) or rule.group(2)
    assert "margin: 0" in body and "text-transform: none" in body and "letter-spacing: normal" in body


def test_hidden_selection_inputs_are_zero_sized_so_they_cannot_widen_the_page(test_client):
    css = test_client.get("/static/app.css").text
    for selector in (".pcard .pick", ".gp input", ".prow-select input", ".prow-gate input", ".switch-row input"):
        line = next(l for l in css.splitlines() if l.startswith(selector + " {"))
        assert "width: 0" in line and "padding: 0" in line, selector


# -- dashboard: compact project cards -----------------------------------------------------


def _one_project(monkeypatch, **overrides):
    _stub_project_panels(monkeypatch, **overrides)


def test_dashboard_shows_projects_as_cards_not_a_table(test_client, monkeypatch):
    _one_project(monkeypatch, names=("little-widget", "little-gadget"))
    text = test_client.get("/").text
    assert "<table" not in text and "<h2" not in text
    assert len(_names(text, r'<details class="proj-card(?: attn)?" id="(?!workspace)[\w-]+"')) == 2
    assert '<details class="proj-card" id="little-widget"' in text and 'class="proj-name"' in text


def test_a_project_card_only_lists_the_facts_that_exist(test_client, monkeypatch):
    _one_project(monkeypatch, server=None, report=None, ci=None, session={"running": False, "trusted": True})
    text = test_client.get("/").text
    assert '<span class="fact-k">Session</span>' in text and '<span class="fact-k">Tasks</span>' in text
    assert '<span class="fact-k">Server</span>' not in text and '<span class="fact-k">Requirements</span>' not in text and '<span class="fact-k">CI</span>' not in text
    summary = text[text.index('id="little-widget"'):text.index("proj-body")]
    assert "&mdash;" not in summary and "—" not in summary  # no placeholder dashes standing in for missing data


def test_a_project_card_shows_every_fact_when_present(test_client, monkeypatch):
    _one_project(
        monkeypatch, server={"listening": True, "version": "0.3.1", "unit": "active"},
        session={"running": True, "trusted": True}, runs=[{"status": "running"}],
        report={"requirements": [{"done": True}, {"done": False}]}, ci={"state": "failure"},
    )
    text = test_client.get("/").text
    for key in ("Server", "Session", "Tasks", "Requirements", "CI"):
        assert f'<span class="fact-k">{key}</span>' in text
    assert "v0.3.1" in text and "1 running" in text and "1/2 done" in text and 'pill fail">failure' in text


def test_a_down_server_and_an_unreachable_agent_are_labelled_not_blank(test_client, monkeypatch):
    _one_project(monkeypatch, server={"listening": False, "version": "1", "unit": "inactive"}, session=None)
    text = test_client.get("/").text
    assert 'pill idle">down' in text and ">unavailable<" in text


@pytest.mark.parametrize("overrides,expected", [
    ({"session": {"running": False, "trusted": False}}, True),
    ({"runs": [{"status": "awaiting_finish"}]}, True),
    ({"ci": {"state": "error"}}, True),
    ({"session": {"running": False, "trusted": True}}, False),
])
def test_a_project_needing_attention_is_marked(test_client, monkeypatch, overrides, expected):
    _one_project(monkeypatch, **overrides)
    assert ('class="proj-card attn"' in test_client.get("/").text) is expected


def test_stat_tiles_have_plain_readable_labels_and_pillars_link_to_the_pillars_page(test_client, monkeypatch):
    _one_project(monkeypatch)
    text = test_client.get("/").text
    for label in ("Projects", "Servers up", "Claude sessions", "Active tasks", "Pillars healthy"):
        assert f'<span class="stat-label">{label}</span>' in text
    assert re.search(r'<a class="stat-tile[^"]*" href="/pillars">', text)


def test_unhealthy_summary_is_singular_or_plural(test_client, monkeypatch):
    _stub_project_panels(monkeypatch)
    monkeypatch.setattr(client, "core_status", lambda paths: {"pillars": {"agent": {"status": "crashed"}}})
    assert "1 pillar not healthy" in test_client.get("/").text
    monkeypatch.setattr(client, "core_status", lambda paths: {"pillars": {"agent": {"status": "crashed"}, "policy": {"status": "faulted"}}})
    assert "2 pillars not healthy" in test_client.get("/").text


def test_dim_text_stays_legible_on_the_dark_background(test_client):
    css = test_client.get("/static/app.css").text
    faint = float(re.search(r"--ink-faint: oklch\((\d+(?:\.\d+)?)%", css).group(1))
    soft = float(re.search(r"--ink-soft: oklch\((\d+(?:\.\d+)?)%", css).group(1))
    assert faint >= 60 and soft >= 72
    labels = re.search(r"\.stat-label \{([^}]*)\}", css).group(1)
    assert "text-transform" not in labels and "font-size: 13px" in labels


# -- externally managed servers ---------------------------------------------------------------


def test_an_external_server_is_shown_as_up_on_the_dashboard(test_client, monkeypatch):
    _stub_project_panels(monkeypatch, server={"listening": True, "version": "?", "branch": "?", "unit": "active", "tailscale": "mapped", "external": True, "managed_unit": "x.service"})
    text = test_client.get("/").text
    assert 'fact-k">Server</span>' in text and 'pill ok">up' in text
    assert "v?" not in text  # an unknown version is not printed


def test_an_ordinary_running_server_still_shows_its_known_version(test_client, monkeypatch):
    _stub_project_panels(monkeypatch, server={"listening": True, "version": "0.3.1", "unit": "active"})
    assert "v0.3.1" in test_client.get("/").text


def test_dashboard_card_offers_no_launch_or_stop_for_an_external_server(test_client, monkeypatch):
    _stub_project_panels(
        monkeypatch, session={"running": False, "trusted": True},
        server={"listening": True, "version": "?", "branch": "?", "unit": "active", "tailscale": "mapped", "external": True, "managed_unit": "little-workshop-dashboard-bridge.service"},
    )
    text = test_client.get("/").text
    assert "Managed outside Deployment" in text and "little-workshop-dashboard-bridge.service" in text
    assert "/projects/little-widget/deploy" not in text and "/projects/little-widget/stop" not in text
    assert ">Launch<" not in text and ">Relaunch<" not in text and ">Stop<" not in text
    assert "<dt>Version</dt>" not in text and "<dt>Branch</dt>" not in text


def test_dashboard_card_still_offers_launch_and_stop_for_an_ordinary_server(test_client, monkeypatch):
    _stub_project_panels(
        monkeypatch, session={"running": False, "trusted": True},
        server={"listening": True, "version": "0.3.1", "unit": "active", "branch": "dev", "tailscale": "off"},
    )
    text = test_client.get("/").text
    assert "/projects/little-widget/deploy" in text and "/projects/little-widget/stop" in text
    assert "Managed outside Deployment" not in text and "<dt>Version</dt>" in text and "<dt>Branch</dt>" in text


# -- requirements pill: draft is not failing ----------------------------------------------------


def _req(status, done):
    return {"status": status, "done": done}


def _reqs_pill(test_client, monkeypatch, items):
    _one_project(monkeypatch, report={"requirements": items})
    text = test_client.get("/").text
    return re.search(r'<span class="pill (\w+)"[^>]*>(\d+/\d+) done</span>', text), text


def test_every_requirement_done_is_green(test_client, monkeypatch):
    match, text = _reqs_pill(test_client, monkeypatch, [_req("published", True)] * 3)
    assert match.group(1, 2) == ("ok", "3/3") and " draft" not in text


def test_a_published_requirement_that_is_not_passing_is_red(test_client, monkeypatch):
    match, _ = _reqs_pill(test_client, monkeypatch, [_req("published", True), _req("published", False)])
    assert match.group(1, 2) == ("fail", "1/2")


def test_requirements_that_are_only_drafts_are_neutral_and_say_so(test_client, monkeypatch):
    match, text = _reqs_pill(test_client, monkeypatch, [_req("published", True), _req("draft", False), _req("draft", False)])
    assert match.group(1, 2) == ("idle", "1/3")
    assert '<span class="fact-v">2 draft</span>' in text and "2 draft requirements awaiting review" in text


def test_a_regression_outranks_drafts(test_client, monkeypatch):
    match, _ = _reqs_pill(test_client, monkeypatch, [_req("published", False), _req("draft", False)])
    assert match.group(1) == "fail"


def test_a_single_draft_is_worded_in_the_singular(test_client, monkeypatch):
    _, text = _reqs_pill(test_client, monkeypatch, [_req("draft", False)])
    assert "1 draft requirement awaiting review" in text


# -- attention: failing checks and jobs awaiting confirmation, pushed to the page ----


def _stub_attention(monkeypatch, *, policies, results, jobs, incidents=()):
    monkeypatch.setattr(client, "policy_list", lambda paths, topic=None: policies)
    monkeypatch.setattr(client, "policy_check_results", lambda paths: results)
    monkeypatch.setattr(client, "list_jobs", lambda paths, status=None: jobs if status == "pending_confirmation" else [])
    monkeypatch.setattr(client, "list_incidents", lambda paths, status=None: list(incidents) if status == "open" else [])


def test_attention_lists_a_failing_check_and_a_job_awaiting_confirmation(test_client, monkeypatch):
    _stub_attention(
        monkeypatch,
        policies=[{"id": "p1", "topic": "commit-bumps-version", "enabled": True}],
        results=[{"policy_id": "p1", "project_id": "little-widget", "outcome": "fail", "detail": "VERSION not bumped", "checked_at": "2026-09-21T10:00:00Z"}],
        jobs=[{"type": "deployment.deploy", "project_id": "little-widget", "created_at": "2026-09-21T10:00:00Z"}],
    )
    body = test_client.get("/attention.json").json()
    assert body["count"] == 2
    assert {i["kind"] for i in body["items"]} == {"policy", "confirmation"}
    page = test_client.get("/settings").text
    assert "commit-bumps-version is failing" in page and "VERSION not bumped" in page
    assert "deployment.deploy awaits confirmation" in page


def test_attention_uses_only_the_latest_result_and_ignores_disabled_policies(test_client, monkeypatch):
    _stub_attention(
        monkeypatch,
        policies=[{"id": "p1", "topic": "fixed-now", "enabled": True}, {"id": "p2", "topic": "switched-off", "enabled": False}],
        results=[
            {"policy_id": "p1", "project_id": "a", "outcome": "fail", "detail": "old", "checked_at": "2026-09-21T09:00:00Z"},
            {"policy_id": "p1", "project_id": "a", "outcome": "pass", "detail": "", "checked_at": "2026-09-21T10:00:00Z"},
            {"policy_id": "p2", "project_id": "a", "outcome": "fail", "detail": "x", "checked_at": "2026-09-21T10:00:00Z"},
        ],
        jobs=[],
    )
    assert test_client.get("/attention.json").json() == {"count": 0, "items": []}
    assert "Nothing needs you right now" in test_client.get("/settings").text


def test_every_page_carries_the_attention_banner_only_when_something_needs_attention(test_client, monkeypatch):
    _stub_project_panels(monkeypatch)
    _stub_attention(monkeypatch, policies=[], results=[], jobs=[])
    assert 'id="attention-banner" href="/settings" hidden' in test_client.get("/").text
    _stub_attention(monkeypatch, policies=[], results=[], jobs=[{"type": "deployment.deploy", "project_id": "x", "created_at": None}])
    text = test_client.get("/").text
    assert 'id="attention-banner" href="/settings">' in text and '<strong id="attention-count">1</strong>' in text


def test_attention_degrades_to_nothing_when_the_pillars_are_down(test_client, monkeypatch):
    def down(*a, **kw):
        raise client.PillarUnavailable("down")

    monkeypatch.setattr(client, "policy_list", down)
    monkeypatch.setattr(client, "policy_check_results", down)
    monkeypatch.setattr(client, "list_jobs", down)
    monkeypatch.setattr(client, "list_incidents", down)
    assert test_client.get("/attention.json").json() == {"count": 0, "items": []}


def test_attention_lists_an_open_incident_as_a_bug(test_client, monkeypatch):
    _stub_attention(
        monkeypatch, policies=[], results=[], jobs=[],
        incidents=[{"id": "i1", "project_id": "little-widget", "pillar": "agent", "exception_type": "ValueError",
                    "message": "boom", "created_at": "2026-09-22T10:00:00Z"}],
    )
    body = test_client.get("/attention.json").json()
    assert body["count"] == 1 and body["items"][0]["kind"] == "incident"
    page = test_client.get("/settings").text
    assert "ValueError in agent" in page and "boom" in page and 'href="/incidents/i1"' in page and ">bug<" in page


# -- pillar names ---------------------------------------------------------------


def test_pillars_page_shows_each_pillar_under_its_full_name(test_client, monkeypatch):
    monkeypatch.setattr(client, "core_status", lambda paths: {"pillars": {
        "policy": {"status": "healthy", "crash_count": 0, "interface_version": "v1"},
        "requirements": {"status": "healthy", "crash_count": 0, "interface_version": "v1"},
        "version": {"status": "healthy", "crash_count": 0, "interface_version": "v1"},
        "mcp": {"status": "disabled", "crash_count": 0, "interface_version": "v1"},
    }})
    monkeypatch.setattr(client, "deployment_pillars", lambda paths: {"pillars": {}, "commits": [], "repo_head": None})
    text = test_client.get("/pillars").text
    for full in ("Policy &amp; Compliance", "Requirements &amp; Quality", "Version Control &amp; Release", "MCP Server"):
        assert f'>{full}</span>' in text
    assert '<span class="pcard-name" title="policy">' in text


def test_an_unknown_pillar_is_shown_as_given():
    assert app_module.pillar_name("brand-new") == "brand-new"
    assert app_module.pillar_name("agent") == "Agentic Development"


# -- grouped navigation ---------------------------------------------------------


def test_bottom_toolbar_groups_related_pages_under_one_button(test_client, monkeypatch):
    monkeypatch.setattr(client, "policy_list", lambda paths, topic=None: [])
    monkeypatch.setattr(client, "policy_drafts", lambda paths, status=None: [])
    text = test_client.get("/policies").text
    bar = text[text.index('<nav class="tabbar"'):]
    assert bar.count('data-open="actions"') == 1 and bar.count('data-open="policies"') == 1 and bar.count('data-open="system"') == 1
    assert bar.count('data-open="dashboard"') == 0
    actions_row = re.search(r'data-view="actions">(.*?)</div>', bar, re.S).group(1)
    for href, label in (("/tasks", "Task list"), ("/tasks/new", "New task"), ("/jobs", "Jobs"), ("/jobs/new", "New job"), ("/jobs/schedule", "Schedule job")):
        assert f'href="{href}"' in actions_row and f"<span>{label}</span>" in actions_row
    assert "/tasks/queue" not in actions_row and "/tasks/schedule" not in actions_row
    policies_row = re.search(r'data-view="policies">(.*?)</div>', bar, re.S).group(1)
    for href, label in (("/policies", "Policies"), ("/policies/new", "New policy")):
        assert f'href="{href}"' in policies_row and f"<span>{label}</span>" in policies_row
    system_row = re.search(r'data-view="system">(.*?)</div>', bar, re.S).group(1)
    for href, label in (("/pillars", "Pillars"), ("/incidents", "Incidents"), ("/settings", "Settings")):
        assert f'href="{href}"' in system_row and f"<span>{label}</span>" in system_row
    main_row = re.search(r'data-view="main"(.*?)</div>', bar, re.S).group(1)
    assert 'href="/"' in main_row
    # Report (requirements and test results, where drafts are approved) is its own one-tap tab
    assert 'href="/report"' in main_row and bar.count('data-open="report"') == 0
    assert "/projects/new" not in bar


def test_the_group_of_the_current_page_is_open_and_highlighted(test_client, monkeypatch):
    monkeypatch.setattr(client, "policy_list", lambda paths, topic=None: [])
    monkeypatch.setattr(client, "policy_drafts", lambda paths, status=None: [])
    text = test_client.get("/policies").text
    assert re.search(r'class="tabbar-row tabbar-row-active" data-view="policies"', text)
    assert re.search(r'class="tab tab-group tab-active" data-open="policies"', text)
    assert 'class="subnav-link is-active" href="/policies"' in text.replace('href="/policies" class="subnav-link is-active"', 'class="subnav-link is-active" href="/policies"')


# -- arranging project cards (Dashboard) -----------------------------------------


def _names(text, pattern):
    return re.findall(pattern, text)


_CARD_ID_RE = r'<details class="proj-card(?: attn)?" id="(?!workspace)([\w-]+)"'


def test_dashboard_cards_follow_the_saved_order_and_the_order_survives_visits(test_client, monkeypatch):
    _stub_project_panels(monkeypatch, names=("a-app", "b-app", "c-app"))
    assert _names(test_client.get("/").text, _CARD_ID_RE) == ["a-app", "b-app", "c-app"]
    response = test_client.post("/order/dashboard/c-app/up", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/?arrange=1"
    test_client.post("/order/dashboard/c-app/up", follow_redirects=False)
    assert _names(test_client.get("/").text, _CARD_ID_RE) == ["c-app", "a-app", "b-app"]


def test_arrange_mode_lists_projects_with_move_buttons(test_client, monkeypatch):
    _stub_project_panels(monkeypatch, names=("a-app", "b-app"))
    text = test_client.get("/?arrange=1").text
    assert 'action="/order/dashboard/a-app/down"' in text and 'action="/order/dashboard/b-app/up"' in text
    assert re.search(r'aria-label="Move a-app up" disabled', text) and re.search(r'aria-label="Move b-app down" disabled', text)


def test_a_new_project_appears_after_the_placed_ones_and_a_bad_move_is_refused(test_client, monkeypatch):
    _stub_project_panels(monkeypatch, names=("a-app", "b-app"))
    test_client.post("/order/dashboard/b-app/up")
    _stub_project_panels(monkeypatch, names=("a-app", "b-app", "new-app"))
    assert _names(test_client.get("/").text, _CARD_ID_RE) == ["b-app", "a-app", "new-app"]
    refused = test_client.post("/order/dashboard/ghost/up", follow_redirects=False).headers["location"]
    assert "Could not reorder" in unquote(refused)
    assert "Could not reorder" in unquote(test_client.post("/order/elsewhere/a-app/up", follow_redirects=False).headers["location"])


def test_a_damaged_preferences_file_means_the_default_order(test_client, monkeypatch, paths):
    _stub_project_panels(monkeypatch, names=("a-app", "b-app"))
    prefs_file = paths.state_home / "dashboard" / "prefs.json"
    prefs_file.parent.mkdir(parents=True)
    prefs_file.write_text("{not json")
    assert _names(test_client.get("/").text, _CARD_ID_RE) == ["a-app", "b-app"]


# -- report: requirements (description, verdict) and tests (name, verdict) ---------------


def _stub_report(monkeypatch):
    monkeypatch.setattr(client, "list_projects", lambda paths, status=None: [{"name": "little-widget", "status": "active"}, {"name": "other", "status": "active"}])
    monkeypatch.setattr(client, "requirements_report", lambda paths, project: {"requirements": [
        {"requirement_id": "r1", "status": "published", "tested": True, "passing": True, "done": True},
        {"requirement_id": "r2", "status": "published", "tested": True, "passing": False, "done": False},
        {"requirement_id": "r3", "status": "published", "tested": False, "passing": False, "done": False},
        {"requirement_id": "r4", "status": "draft", "tested": False, "passing": False, "done": False},
    ]})
    monkeypatch.setattr(client, "requirements_list", lambda paths, project: [
        {"id": "r1", "key": "REQ-1", "title": "Lists", "description": "The Widget shall list things.", "status": "published"},
        {"id": "r2", "key": "REQ-2", "title": "Sorts", "description": "The Widget shall sort things.", "status": "published"},
        {"id": "r3", "key": "REQ-3", "title": "Exports", "description": "The Widget shall export things.", "status": "published"},
        {"id": "r4", "key": "REQ-4", "title": "Prints", "description": "The Widget shall print things.", "status": "draft"},
    ])
    monkeypatch.setattr(client, "requirements_suites", lambda paths, project: [{"id": "s1", "name": "test_widget"}])
    monkeypatch.setattr(client, "requirements_test_results", lambda paths, project: [
        {"suite_id": "s1", "test_name": "t::a", "verdict": "failed", "executed_at": "2026-09-21T09:00:00Z"},
        {"suite_id": "s1", "test_name": "t::a", "verdict": "passed", "executed_at": "2026-09-21T10:00:00Z"},
        {"suite_id": "s1", "test_name": "t::b", "verdict": "failed", "executed_at": "2026-09-21T10:00:00Z"},
        {"suite_id": "s1", "test_name": "t::c", "verdict": "skipped", "executed_at": "2026-09-21T10:00:00Z"},
    ])


def test_report_loads_without_a_project_preselected(test_client, monkeypatch):
    _stub_report(monkeypatch)
    text = test_client.get("/report").text
    assert "REQ-1" not in text and "t::a" not in text
    assert 'value="little-widget"' in text and 'value="other"' in text


def test_report_lists_every_requirement_with_description_and_verdict(test_client, monkeypatch):
    _stub_report(monkeypatch)
    text = test_client.get("/report", params={"project": "little-widget"}).text
    for key, desc, verdict in (("REQ-1", "The Widget shall list things.", "passing"), ("REQ-2", "The Widget shall sort things.", "failing"),
                               ("REQ-3", "The Widget shall export things.", "no tests"), ("REQ-4", "The Widget shall print things.", "draft")):
        block = text[text.index(f"<code>{key}</code>"):]
        assert verdict in block[:200] and desc in block[:400]


def test_report_lists_every_test_with_its_latest_verdict(test_client, monkeypatch):
    _stub_report(monkeypatch)
    text = test_client.get("/report", params={"project": "little-widget"}).text
    assert re.search(r'report-test-name">t::a</span>\s*<span class="pill ok">passed', text)  # the later pass replaces the earlier failure
    assert re.search(r'report-test-name">t::b</span>\s*<span class="pill fail">failed', text)
    assert re.search(r'report-test-name">t::c</span>\s*<span class="pill idle">skipped', text)
    assert "test_widget" in text and text.count("report-test-name") == 3


def test_report_filters_by_search_and_verdict(test_client, monkeypatch):
    _stub_report(monkeypatch)
    text = test_client.get("/report", params={"project": "little-widget", "verdict": "failing"}).text
    assert "REQ-2" in text and "REQ-1" not in text and "REQ-4" not in text
    assert "REQ-3" in test_client.get("/report", params={"project": "little-widget", "q": "export"}).text
    assert "t::b" in test_client.get("/report", params={"project": "little-widget", "verdict": "failed"}).text
    assert "t::a" not in test_client.get("/report", params={"project": "little-widget", "verdict": "failed"}).text


def test_report_splits_requirements_and_tests_into_two_tabs(test_client, monkeypatch):
    _stub_report(monkeypatch)
    text = test_client.get("/report", params={"project": "little-widget"}).text
    assert len(re.findall(r'class="report-tab( is-active)?"', text)) == 2
    assert re.search(r'class="report-tab is-active"[^>]*data-tab="requirements"', text)
    assert re.search(r'class="report-tab"[^>]*data-tab="tests"', text)
    req_panel = re.search(r'data-panel="requirements">(.*?)<section class="report-panel" data-panel="tests"', text, re.S).group(1)
    tests_panel = text[text.index('data-panel="tests"'):]
    assert "REQ-1" in req_panel and "REQ-1" not in tests_panel
    assert "t::a" in tests_panel and "t::a" not in req_panel
    assert 'data-panel="tests" hidden' in text  # tests tab starts hidden; requirements tab starts visible


def _requirement_card(text, key):
    start = text.rindex('<details class="report-item">', 0, text.index(f"<code>{key}</code>"))
    end = text.index("</details>", start) + len("</details>")
    return text[start:end]


def test_report_requirement_cards_are_collapsible_and_hide_the_description_until_expanded(test_client, monkeypatch):
    _stub_report(monkeypatch)
    text = test_client.get("/report", params={"project": "little-widget"}).text
    details = _requirement_card(text, "REQ-1")
    assert "<summary>" in details and "</summary>" in details
    summary = details[details.index("<summary>"):details.index("</summary>")]
    body = details[details.index("</summary>") + len("</summary>"):]
    assert "REQ-1" in summary and "passing" in summary
    assert "The Widget shall list things." not in summary and "The Widget shall list things." in body


def test_report_tests_are_grouped_into_collapsible_suites_with_one_card_per_test(test_client, monkeypatch):
    _stub_report(monkeypatch)
    text = test_client.get("/report", params={"project": "little-widget"}).text
    tests_panel = text[text.index('data-panel="tests"'):]
    suite = tests_panel[tests_panel.index("<details class=\"report-suite\""):]
    assert "<summary>" in suite and "test_widget" in suite[:suite.index("</summary>")]
    assert suite.count('class="report-item report-test-card"') == 3


def test_report_shows_a_pillar_outage_as_an_error_not_a_500(test_client, monkeypatch):
    _stub_report(monkeypatch)

    def down(paths, project):
        raise client.PillarUnavailable("requirements daemon unreachable")

    monkeypatch.setattr(client, "requirements_report", down)
    response = test_client.get("/report", params={"project": "little-widget"})
    assert response.status_code == 200 and "unreachable" in response.text


# -- new project (bootstrap) from the toolbar ----------------------------------------


def test_new_project_form_is_reachable_from_the_dashboard_page(test_client, monkeypatch):
    text = test_client.get("/projects/new").text
    assert 'action="/projects/new"' in text and 'name="create_github_repo"' in text
    _stub_project_panels(monkeypatch, names=())
    assert 'href="/projects/new"' in test_client.get("/").text  # a contextual link on the Dashboard, not the toolbar


def test_new_project_submits_a_bootstrap_job_into_the_workspace(test_client, monkeypatch, tmp_path):
    monkeypatch.setenv("LITTLE_WORKSHOP_WORKSPACE_DIR", str(tmp_path))
    jobs = []
    monkeypatch.setattr(client, "create_job", lambda paths, **kw: (jobs.append(kw), {"status": "succeeded", "result": {"path": f"{tmp_path}/little-new"}})[1])
    response = test_client.post("/projects/new", data={"name": "little-new", "description": "A thing"}, follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"].startswith("/?message=")
    job = jobs[0]
    assert job["type"] == "project.bootstrap" and job["target_pillar"] == "registry"
    assert job["params"] == {"name": "little-new", "dest_dir": str(tmp_path), "description": "A thing", "create_github_repo": False, "public": False}
    assert "created" in unquote(response.headers["location"])


def test_new_project_creates_a_github_repo_only_when_asked(test_client, monkeypatch):
    jobs = []
    monkeypatch.setattr(client, "create_job", lambda paths, **kw: (jobs.append(kw), {"status": "succeeded", "result": {"path": "/x"}})[1])
    test_client.post("/projects/new", data={"name": "little-new", "create_github_repo": "1", "public": "1"})
    assert jobs[0]["params"]["create_github_repo"] is True and jobs[0]["params"]["public"] is True


@pytest.mark.parametrize("name", ["", "Little New", "../escape", "-lead", "UPPER", "a/b"])
def test_new_project_refuses_an_unsafe_name_and_creates_nothing(test_client, monkeypatch, name):
    jobs = []
    monkeypatch.setattr(client, "create_job", lambda paths, **kw: jobs.append(kw))
    response = test_client.post("/projects/new", data={"name": name}, follow_redirects=False)
    assert response.status_code in (303, 422) and jobs == []


def test_new_project_reports_a_failed_bootstrap(test_client, monkeypatch):
    monkeypatch.setattr(client, "create_job", lambda paths, **kw: {"status": "failed", "error": "project 'little-new' is already registered"})
    location = unquote(test_client.post("/projects/new", data={"name": "little-new"}, follow_redirects=False).headers["location"])
    assert location.startswith("/projects/new?message=") and "already registered" in location


# -- new task: immediate dispatch, and merged-in scheduling ------------------------------


def _task_job(job_id, project, title, status="running"):
    return {"id": job_id, "type": "agent.run_task", "project_id": project, "status": status, "params": {"title": title}, "created_at": "2026-09-21T10:00:00Z"}


def _stub_task_pages(monkeypatch, jobs):
    monkeypatch.setattr(client, "list_projects", lambda paths, status=None: [{"name": "little-widget", "status": "active"}])
    monkeypatch.setattr(client, "list_jobs", lambda paths, status=None: jobs)


def test_new_task_immediate_dispatches_a_job(test_client, monkeypatch):
    monkeypatch.setattr(client, "list_projects", lambda paths, status=None: [{"name": "little-widget", "status": "active"}])
    created = []
    monkeypatch.setattr(client, "create_job", lambda paths, **kw: (created.append(kw), {"status": "running"})[1])
    response = test_client.post("/tasks/new", data={
        "project": "little-widget", "title": "Add search", "description": "d", "when": "immediate"}, follow_redirects=False)
    assert response.status_code == 303 and "started" in unquote(response.headers["location"])
    job = created[0]
    assert job["type"] == "agent.run_task" and job["project_id"] == "little-widget" and job["params"]["title"] == "Add search"


def test_new_task_immediate_reports_a_failure_to_start(test_client, monkeypatch):
    monkeypatch.setattr(client, "list_projects", lambda paths, status=None: [{"name": "little-widget", "status": "active"}])
    monkeypatch.setattr(client, "create_job", lambda paths, **kw: {"status": "failed", "error": "agent unreachable"})
    response = test_client.post("/tasks/new", data={
        "project": "little-widget", "title": "Add search", "when": "immediate"}, follow_redirects=False)
    assert response.status_code == 303 and "Could not start" in unquote(response.headers["location"])


def test_schedule_once_creates_a_scheduled_task_that_starts_an_agent_task(test_client, monkeypatch):
    monkeypatch.setattr(client, "list_projects", lambda paths, status=None: [{"name": "little-widget", "status": "active"}])
    calls = []
    monkeypatch.setattr(client, "create_scheduled_task", lambda paths, **kw: calls.append(kw) or {})
    response = test_client.post("/tasks/new", data={
        "project": "little-widget", "title": "Nightly tidy", "description": "d", "when": "once",
        "kind": "once", "at": "2999-01-02T09:30"}, follow_redirects=False)
    assert response.status_code == 303 and "scheduled" in unquote(response.headers["location"])
    call = calls[0]
    assert call["name"] == "little-widget: Nightly tidy" and call["schedule"] == {"kind": "once", "at": "2999-01-02T09:30:00"}
    template = call["job_template"]
    assert template["type"] == "agent.run_task" and template["target_pillar"] == "agent" and template["project_id"] == "little-widget"
    assert template["params"]["title"] == "Nightly tidy"


def test_schedule_recurring_takes_weekdays_and_a_time(test_client, monkeypatch):
    monkeypatch.setattr(client, "list_projects", lambda paths, status=None: [{"name": "little-widget", "status": "active"}])
    calls = []
    monkeypatch.setattr(client, "create_scheduled_task", lambda paths, **kw: calls.append(kw) or {})
    test_client.post("/tasks/new", data={
        "project": "little-widget", "title": "Weekly", "when": "recurring", "kind": "recurring",
        "day": ["4", "0", "0"], "time_of_day": "07:05"})
    assert calls[0]["schedule"] == {"kind": "recurring", "days_of_week": [0, 4], "time_of_day": "07:05"}


@pytest.mark.parametrize("data", [
    {"kind": "once", "at": ""}, {"kind": "once", "at": "2001-01-01T00:00"}, {"kind": "once", "at": "not a date"},
    {"kind": "recurring", "time_of_day": "07:05"}, {"kind": "recurring", "day": ["1"], "time_of_day": "25:99"},
    {"kind": "recurring", "day": ["9"], "time_of_day": "07:05"}, {"kind": "weekly"},
])
def test_schedule_refuses_an_incomplete_or_past_schedule(test_client, monkeypatch, data):
    monkeypatch.setattr(client, "list_projects", lambda paths, status=None: [{"name": "little-widget", "status": "active"}])
    calls = []
    monkeypatch.setattr(client, "create_scheduled_task", lambda paths, **kw: calls.append(kw) or {})
    response = test_client.post("/tasks/new", data={"project": "little-widget", "title": "t", "when": data.get("kind", "once"), **data}, follow_redirects=False)
    assert response.status_code == 303 and calls == []


def test_new_task_page_lists_scheduled_agent_tasks_and_removes_one(test_client, monkeypatch):
    monkeypatch.setattr(client, "list_projects", lambda paths, status=None: [{"name": "little-widget", "status": "active"}])
    monkeypatch.setattr(client, "scheduled_tasks", lambda paths: [
        {"id": "t1", "name": "little-widget: Weekly", "state": "active", "next_run_at": "2026-09-28T07:05:00",
         "job_template": {"type": "agent.run_task"}, "schedule": {"kind": "recurring", "days_of_week": [0, 4], "time_of_day": "07:05"}},
        {"id": "t2", "name": "policy audit", "state": "active", "job_template": {"type": "policy.check_all"}, "schedule": {"kind": "once", "at": "2026-09-30T00:00:00"}},
    ])
    text = test_client.get("/tasks/new").text
    assert "little-widget: Weekly" in text and "Mon, Fri at 07:05" in text and "policy audit" not in text
    removed = []
    monkeypatch.setattr(client, "remove_scheduled_task", lambda paths, task_id: removed.append(task_id) or {})
    response = test_client.post("/tasks/schedule/t1/remove", follow_redirects=False)
    assert removed == ["t1"] and response.status_code == 303 and response.headers["location"].startswith("/tasks/new")


def test_tasks_list_shows_a_queued_agent_task_as_waiting(test_client, monkeypatch):
    monkeypatch.setattr(client, "list_projects", lambda paths, status=None: [{"name": "little-widget", "status": "active"}])
    monkeypatch.setattr(client, "agent_runs", lambda paths, project: [])
    monkeypatch.setattr(client, "list_jobs", lambda paths, status=None: [
        {"id": "j9", "type": "agent.run_task", "project_id": "little-widget", "created_at": "2026-09-22T10:00:00Z", "params": {"title": "Pending thing"}},
        {"id": "j10", "type": "deployment.deploy", "project_id": "little-widget", "created_at": "2026-09-22T09:00:00Z", "params": {}},
    ] if status == "queued" else [])
    text = test_client.get("/tasks").text
    assert "Pending thing" in text and "waiting" in text


# -- workspace-level session on the Dashboard ------------------------------------------

def test_dashboard_offers_a_workspace_session_above_the_projects(test_client, monkeypatch, tmp_path):
    monkeypatch.setenv("LITTLE_WORKSHOP_WORKSPACE_DIR", str(tmp_path))
    _stub_project_panels(monkeypatch, session={"running": False, "trusted": True})
    text = test_client.get("/").text
    assert text.index('id="workspace"') < text.index('id="little-widget"')
    assert str(tmp_path) in text and 'action="/projects/workspace/session/start"' in text and "Start workspace session" in text


def test_a_running_workspace_session_offers_stop_and_its_name(test_client, monkeypatch):
    _stub_project_panels(monkeypatch, session={"running": True, "trusted": True})
    text = test_client.get("/").text
    assert 'action="/projects/workspace/session/stop"' in text and "lws-workspace-session" in text


def test_an_untrusted_workspace_says_how_to_trust_it(test_client, monkeypatch):
    _stub_project_panels(monkeypatch, session={"running": False, "trusted": False})
    assert "accept the trust prompt" in test_client.get("/").text


def test_starting_the_workspace_session_submits_an_agent_job_for_the_reserved_id(test_client, monkeypatch):
    jobs = []
    monkeypatch.setattr(client, "create_job", lambda paths, **kw: (jobs.append(kw), {"status": "succeeded", "result": {"session_name": "lws-workspace-session"}})[1])
    response = test_client.post("/projects/workspace/session/start", follow_redirects=False)
    assert jobs[0]["type"] == "agent.session_start" and jobs[0]["project_id"] == "workspace"
    assert response.headers["location"].startswith("/?expand=workspace")


def test_a_project_cannot_be_bootstrapped_under_the_reserved_workspace_name(test_client, monkeypatch):
    jobs = []
    monkeypatch.setattr(client, "create_job", lambda paths, **kw: jobs.append(kw))
    location = unquote(test_client.post("/projects/new", data={"name": "workspace"}, follow_redirects=False).headers["location"])
    assert "reserved" in location and jobs == []


def test_report_hides_the_self_application_aggregate_suite_from_the_tests_tab(test_client, monkeypatch):
    monkeypatch.setattr(client, "list_projects", lambda paths, status=None: [{"name": "little-widget", "status": "active"}])
    monkeypatch.setattr(client, "requirements_report", lambda paths, project: {"requirements": []})
    monkeypatch.setattr(client, "requirements_list", lambda paths, project: [])
    monkeypatch.setattr(client, "requirements_suites", lambda paths, project: [
        {"id": "agg", "name": "Unit tests"}, {"id": "s1", "name": "test_widget"},
    ])
    monkeypatch.setattr(client, "requirements_test_results", lambda paths, project: [
        {"suite_id": "agg", "test_name": "tests.unit.test_widget::test_a", "verdict": "passed", "executed_at": "2026-09-22T10:00:00Z"},
        {"suite_id": "s1", "test_name": "test_a", "verdict": "passed", "executed_at": "2026-09-22T10:00:00Z"},
    ])
    text = test_client.get("/report", params={"project": "little-widget"}).text
    assert ">Unit tests<" not in text
    assert text.count("report-test-name") == 1 and "test_widget" in text


# -- incidents page: list, detail, confirm/dismiss --------------------------------------


def _incident_row(**overrides):
    base = {
        "id": "inc1", "project_id": "little-widget", "pillar": "agent", "context": "POST /v1/jobs/x",
        "exception_type": "ValueError", "message": "boom", "traceback": "Traceback...\nValueError: boom",
        "status": "open", "run_id": None, "created_at": "2026-09-22T10:00:00Z", "decided_at": None,
    }
    base.update(overrides)
    return base


def test_incidents_page_lists_open_by_default(test_client, monkeypatch):
    monkeypatch.setattr(client, "list_incidents", lambda paths, status=None: [_incident_row()] if status == "open" else [])
    text = test_client.get("/incidents").text
    assert "ValueError in agent" in text and "boom" in text and 'href="/incidents/inc1"' in text


def test_incidents_page_status_filter(test_client, monkeypatch):
    calls = []
    monkeypatch.setattr(client, "list_incidents", lambda paths, status=None: calls.append(status) or [])
    test_client.get("/incidents", params={"status": "dismissed"})
    assert "dismissed" in calls
    calls.clear()
    test_client.get("/incidents", params={"status": ""})
    assert None in calls  # "All" -> no status filter, distinct from the banner's own "open" call


def test_incident_detail_shows_traceback_and_the_confirm_form(test_client, monkeypatch):
    monkeypatch.setattr(client, "get_incident", lambda paths, incident_id: _incident_row())
    text = test_client.get("/incidents/inc1").text
    assert "Traceback..." in text and 'action="/incidents/inc1/confirm"' in text and 'action="/incidents/inc1/dismiss"' in text
    assert 'value="auto_approve"' in text


def test_a_decided_incident_offers_no_confirm_form(test_client, monkeypatch):
    monkeypatch.setattr(client, "get_incident", lambda paths, incident_id: _incident_row(status="confirmed", run_id="run-1"))
    text = test_client.get("/incidents/inc1").text
    assert "/confirm" not in text and "/dismiss" not in text
    assert 'href="/tasks/little-widget/run-1"' in text


def test_unknown_incident_says_so(test_client, monkeypatch):
    monkeypatch.setattr(client, "get_incident", lambda paths, incident_id: None)
    assert "Incident not found." in test_client.get("/incidents/ghost").text


def test_confirming_an_incident_submits_the_chosen_review_mode(test_client, monkeypatch):
    jobs = []
    monkeypatch.setattr(client, "create_job", lambda paths, **kw: (jobs.append(kw), {"status": "running", "result": {"run_id": "run-1"}})[1])
    response = test_client.post("/incidents/inc1/confirm", data={"review_mode": "manual"}, follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"].startswith("/tasks?message=")
    job = jobs[0]
    assert job["type"] == "agent.confirm_incident" and job["params"] == {"incident_id": "inc1", "review_mode": "manual"}


def test_confirming_defaults_to_auto_approve(test_client, monkeypatch):
    jobs = []
    monkeypatch.setattr(client, "create_job", lambda paths, **kw: (jobs.append(kw), {"status": "running", "result": {"run_id": "run-1"}})[1])
    test_client.post("/incidents/inc1/confirm", data={})
    assert jobs[0]["params"]["review_mode"] == "auto_approve"


def test_a_refused_confirm_returns_to_the_incident(test_client, monkeypatch):
    monkeypatch.setattr(client, "create_job", lambda paths, **kw: {"status": "failed", "error": "no such incident"})
    response = test_client.post("/incidents/inc1/confirm", data={}, follow_redirects=False)
    assert response.headers["location"].startswith("/incidents/inc1?message=") and "no such incident" in unquote(response.headers["location"])


def test_dismissing_an_incident_redirects_to_the_list(test_client, monkeypatch):
    jobs = []
    monkeypatch.setattr(client, "create_job", lambda paths, **kw: (jobs.append(kw), {"status": "succeeded"})[1])
    response = test_client.post("/incidents/inc1/dismiss", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"].startswith("/incidents?message=")
    assert jobs[0]["type"] == "agent.dismiss_incident" and jobs[0]["params"] == {"incident_id": "inc1"}


def test_a_refused_dismiss_returns_to_the_incident(test_client, monkeypatch):
    monkeypatch.setattr(client, "create_job", lambda paths, **kw: {"status": "failed", "error": "already decided"})
    response = test_client.post("/incidents/inc1/dismiss", follow_redirects=False)
    assert response.headers["location"].startswith("/incidents/inc1?message=") and "already decided" in unquote(response.headers["location"])


# -- secrets: token validity, on the Pillars page (System health) -----------------------


def test_pillars_page_shows_a_valid_token_with_no_expiration(test_client, monkeypatch):
    monkeypatch.setattr(client, "list_secrets", lambda paths: [{"name": "github", "present": True, "valid": True, "expires_at": None, "detail": "no expiration set"}])
    text = test_client.get("/pillars").text
    assert "github" in text and 'class="pill ok">valid' in text and "no expiration set" in text


def test_pillars_page_shows_the_expiration_when_the_token_has_one(test_client, monkeypatch):
    monkeypatch.setattr(client, "list_secrets", lambda paths: [{"name": "github", "present": True, "valid": True, "expires_at": "2026-12-31 00:00:00 UTC", "detail": ""}])
    text = test_client.get("/pillars").text
    assert "expires 2026-12-31 00:00:00 UTC" in text


def test_pillars_page_shows_an_invalid_token(test_client, monkeypatch):
    monkeypatch.setattr(client, "list_secrets", lambda paths: [{"name": "github", "present": True, "valid": False, "expires_at": None, "detail": "not logged in"}])
    text = test_client.get("/pillars").text
    assert 'class="pill fail">invalid' in text


def test_pillars_page_hides_the_secrets_section_when_none_are_configured(test_client, monkeypatch):
    monkeypatch.setattr(client, "list_secrets", lambda paths: [])
    assert "Secrets" not in test_client.get("/pillars").text


def test_pillars_page_survives_registry_being_down_for_secrets(test_client, monkeypatch):
    def down(paths):
        raise client.PillarUnavailable("registry daemon unreachable")

    monkeypatch.setattr(client, "list_secrets", down)
    assert test_client.get("/pillars").status_code == 200


# -- report page: no phantom horizontal scroll, bars stay fixed (2026-09-23) ------------


def test_report_card_text_can_wrap_instead_of_widening_the_page(test_client):
    css = test_client.get("/static/app.css").text
    for selector in (".report-title", ".report-desc", ".report-head code"):
        line = next(l for l in css.splitlines() if l.strip().startswith(selector + " {"))
        assert "overflow-wrap: anywhere" in line, selector
    for selector, needle in (
        ("details.report-item {", "min-width: 0"),
        ("article.report-item {", "min-width: 0"),
        (".report-head {", "min-width: 0"),
    ):
        block = css[css.index(selector):css.index("}", css.index(selector))]
        assert needle in block, selector


def test_tabbar_row_scrolls_sideways_instead_of_widening_the_page(test_client):
    css = test_client.get("/static/app.css").text
    row_block = css[css.index("nav.tabbar .tabbar-row {"):css.index("}", css.index("nav.tabbar .tabbar-row {"))]
    assert "overflow-x: auto" in row_block
    tab_block = css[css.index("nav.tabbar .tab {"):css.index("}", css.index("nav.tabbar .tab {"))]
    assert "flex: 0 0 auto" in tab_block


def test_report_page_produces_no_horizontally_overflowing_content(test_client, monkeypatch):
    """A regression guard for the phantom-scroll bug itself: a requirement with a
    long, space-free title/description must not force any element wider than
    its own card — approximated here by asserting the long text is wrapped by
    the same CSS class the manual/live check (scrollWidth == innerWidth) relied on,
    since a headless layout engine isn't available in the unit suite."""
    _stub_report(monkeypatch)
    long_word = "REQ" + "x" * 200
    monkeypatch.setattr(client, "requirements_list", lambda paths, project: [
        {"id": "r1", "key": "REQ-1", "title": long_word, "description": "y" * 200, "status": "published"},
    ])
    text = test_client.get("/report", params={"project": "little-widget"}).text
    assert long_word in text  # rendered, not stripped — wrapping is CSS's job, not the server's


# -- new job / schedule job: reuse an existing job, or ask Claude once ------------------


def test_job_new_get_renders(test_client, monkeypatch):
    monkeypatch.setattr(client, "list_jobs", lambda paths, status=None: [
        {"id": "j1", "type": "version.branch_audit", "target_pillar": "version", "project_id": "little-widget",
         "params": {}, "created_at": "2026-09-22T10:00:00Z"},
    ])
    text = test_client.get("/jobs/new").text
    assert 'action="/jobs/new"' in text and "version.branch_audit" in text


def test_job_new_build_reuse_fills_the_template_from_the_chosen_job(test_client, monkeypatch):
    monkeypatch.setattr(client, "list_jobs", lambda paths, status=None: [])
    monkeypatch.setattr(client, "get_job", lambda paths, job_id: {
        "id": job_id, "type": "version.branch_audit", "target_pillar": "version", "project_id": "little-widget", "params": {"x": 1},
    })
    text = test_client.post("/jobs/new", data={"action": "build", "mode": "reuse", "reuse_job_id": "j1"}).text
    assert "version.branch_audit" in text and "little-widget" in text


def test_job_new_build_reuse_without_a_choice_is_refused(test_client, monkeypatch):
    monkeypatch.setattr(client, "list_jobs", lambda paths, status=None: [])
    text = test_client.post("/jobs/new", data={"action": "build", "mode": "reuse", "reuse_job_id": ""}).text
    assert "Choose a job to reuse." in text


def test_job_new_build_ask_asks_claude_and_parses_the_reply(test_client, monkeypatch):
    monkeypatch.setattr(client, "list_jobs", lambda paths, status=None: [])
    monkeypatch.setattr(client, "create_job", lambda paths, **kw: {
        "status": "succeeded", "result": {"text": '```json\n{"type": "policy.check_all", "target_pillar": "policy", "params": {}}\n```'},
    })
    text = test_client.post("/jobs/new", data={"action": "build", "mode": "ask", "prompt": "run every policy check"}).text
    assert "policy.check_all" in text


def test_job_new_build_ask_with_invalid_json_shows_an_error(test_client, monkeypatch):
    monkeypatch.setattr(client, "list_jobs", lambda paths, status=None: [])
    monkeypatch.setattr(client, "create_job", lambda paths, **kw: {"status": "succeeded", "result": {"text": "not json at all"}})
    text = test_client.post("/jobs/new", data={"action": "build", "mode": "ask", "prompt": "do a thing"}).text
    assert "wasn&#39;t valid JSON" in text or "wasn't valid JSON" in text


def test_job_new_submit_dispatches_and_redirects_to_jobs(test_client, monkeypatch):
    monkeypatch.setattr(client, "list_jobs", lambda paths, status=None: [])
    created = []
    monkeypatch.setattr(client, "create_job", lambda paths, **kw: (created.append(kw), {"status": "queued"})[1])
    template = '{"type": "policy.check_all", "target_pillar": "policy", "project_id": null, "params": {}}'
    response = test_client.post("/jobs/new", data={"action": "submit", "template_json": template}, follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"].startswith("/jobs?")
    assert created[0]["type"] == "policy.check_all" and created[0]["target_pillar"] == "policy"


def test_job_new_submit_with_bad_json_is_refused(test_client, monkeypatch):
    monkeypatch.setattr(client, "list_jobs", lambda paths, status=None: [])
    text = test_client.post("/jobs/new", data={"action": "submit", "template_json": "{not json"}).text
    assert "Not valid JSON" in text


def test_job_schedule_get_renders_and_lists_scheduled_non_task_jobs(test_client, monkeypatch):
    monkeypatch.setattr(client, "list_jobs", lambda paths, status=None: [])
    monkeypatch.setattr(client, "scheduled_tasks", lambda paths: [
        {"id": "t1", "name": "audit", "state": "active", "job_template": {"type": "version.branch_audit"}, "schedule": {"kind": "once", "at": "2026-09-30T00:00:00"}},
        {"id": "t2", "name": "task-thing", "state": "active", "job_template": {"type": "agent.run_task"}, "schedule": {"kind": "once", "at": "2026-09-30T00:00:00"}},
    ])
    text = test_client.get("/jobs/schedule").text
    assert "audit" in text and "task-thing" not in text


def test_job_schedule_build_and_submit_creates_a_scheduled_task(test_client, monkeypatch):
    monkeypatch.setattr(client, "list_jobs", lambda paths, status=None: [])
    monkeypatch.setattr(client, "scheduled_tasks", lambda paths: [])
    calls = []
    monkeypatch.setattr(client, "create_scheduled_task", lambda paths, **kw: calls.append(kw) or {})
    template = '{"type": "version.branch_audit", "target_pillar": "version", "project_id": "little-widget", "params": {}}'
    response = test_client.post("/jobs/schedule", data={
        "action": "submit", "template_json": template, "kind": "once", "at": "2999-01-02T09:30"}, follow_redirects=False)
    assert response.status_code == 303 and "scheduled" in unquote(response.headers["location"])
    assert calls[0]["job_template"]["type"] == "version.branch_audit"
    assert calls[0]["schedule"] == {"kind": "once", "at": "2999-01-02T09:30:00"}


def test_job_schedule_submit_without_a_time_is_refused(test_client, monkeypatch):
    monkeypatch.setattr(client, "list_jobs", lambda paths, status=None: [])
    monkeypatch.setattr(client, "scheduled_tasks", lambda paths: [])
    calls = []
    monkeypatch.setattr(client, "create_scheduled_task", lambda paths, **kw: calls.append(kw) or {})
    template = '{"type": "version.branch_audit", "target_pillar": "version", "project_id": null, "params": {}}'
    text = test_client.post("/jobs/schedule", data={"action": "submit", "template_json": template, "kind": "once", "at": ""}).text
    assert "Choose when it should run." in text and calls == []


def test_job_schedule_remove(test_client, monkeypatch):
    removed = []
    monkeypatch.setattr(client, "remove_scheduled_task", lambda paths, task_id: removed.append(task_id) or {})
    response = test_client.post("/jobs/schedule/t1/remove", follow_redirects=False)
    assert removed == ["t1"] and response.status_code == 303 and response.headers["location"].startswith("/jobs/schedule")


# -- new policy / edit policy: Claude authors it, same as the Policy pillar's own flow --


def test_policy_new_get_renders(test_client):
    text = test_client.get("/policies/new").text
    assert 'action="/policies/new"' in text and "Author" in text


def test_policy_new_post_authors_and_redirects_to_the_new_draft(test_client, monkeypatch):
    calls = []
    monkeypatch.setattr(client, "create_job", lambda paths, **kw: (calls.append(kw), {"status": "succeeded", "result": {"draft_id": "d1"}})[1])
    response = test_client.post("/policies/new", data={
        "ears_text": "every tag shall be annotated", "topic": "tag-annotated", "target_pillar": "version",
        "scope_level": "all_projects", "gate_points": ["release"],
    }, follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"].startswith("/policies/drafts/d1")
    job = calls[0]
    assert job["type"] == "policy.author" and job["params"]["topic"] == "tag-annotated" and job["params"]["created_by"] == "user"
    assert job["params"]["save_draft"] is True and "policy_id" not in job["params"]


def test_policy_new_post_shows_the_authoring_error_and_preserves_input(test_client, monkeypatch):
    monkeypatch.setattr(client, "create_job", lambda paths, **kw: {"status": "failed", "error": "not enforceable"})
    text = test_client.post("/policies/new", data={
        "ears_text": "vague wish", "topic": "vague", "target_pillar": "policy", "scope_level": "all_projects",
    }).text
    assert "not enforceable" in text and "vague wish" in text


def test_policy_edit_get_prefills_from_the_existing_policy(test_client, monkeypatch):
    monkeypatch.setattr(client, "policy_get", lambda paths, policy_id: {
        "id": "pol1", "ears_text": "a commit shall bump the version", "topic": "commit-bumps-version",
        "target_pillar": "version", "scope_level": "all_projects", "scope_target": None, "gate_points": ["branch_finish"],
    })
    text = test_client.get("/policies/pol1/edit").text
    assert "a commit shall bump the version" in text and "Re-author" in text


def test_policy_edit_post_includes_the_policy_id_in_the_job_params(test_client, monkeypatch):
    calls = []
    monkeypatch.setattr(client, "create_job", lambda paths, **kw: (calls.append(kw), {"status": "succeeded", "result": {"draft_id": "d2"}})[1])
    response = test_client.post("/policies/pol1/edit", data={
        "ears_text": "a commit shall bump the version twice", "topic": "commit-bumps-version",
        "target_pillar": "version", "scope_level": "all_projects", "gate_points": ["branch_finish"],
    }, follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"].startswith("/policies/drafts/d2")
    assert calls[0]["params"]["policy_id"] == "pol1"


def test_an_unreachable_deployment_is_not_reported_as_a_missing_manifest(test_client, monkeypatch):
    """A status read that times out (Deployment busy or restarting) used to render as "No server manifest yet"."""
    _stub_project_panels(monkeypatch, session={"running": False, "trusted": True})

    def unreachable(paths, name):
        raise client.PillarUnavailable("deployment daemon unreachable")

    monkeypatch.setattr(client, "deployment_status_optional", unreachable)
    _stub_in_flight_jobs(monkeypatch, [{"type": "deployment.deploy", "project_id": "little-widget", "status": "running"}])
    text = test_client.get("/").text
    assert "set-manifest" not in text
    assert "Deployment is not answering right now" in text
    assert 'data-starting="launch" data-project="little-widget">Launching…' in text


def test_report_is_in_the_navigation_again(test_client, monkeypatch):
    _stub_report(monkeypatch)
    text = test_client.get("/").text
    assert 'href="/report"' in text


def test_report_offers_approve_only_on_draft_requirements(test_client, monkeypatch):
    _stub_report(monkeypatch)
    text = test_client.get("/report", params={"project": "little-widget"}).text
    assert text.count('data-busy="Approving…">Approve</button>') == 1  # REQ-4 is the only draft
    assert 'name="requirement_id" value="r4"' in text and 'value="r1"' not in text
    assert "Approve 1 shown draft" in text


def test_approving_publishes_each_requirement_through_a_job(test_client, monkeypatch):
    seen = []

    def fake_create_job(paths, **f):
        seen.append((f["type"], f["target_pillar"], f["project_id"], f["params"]))
        return {"status": "succeeded"}

    monkeypatch.setattr(client, "create_job", fake_create_job)
    response = test_client.post(
        "/report/little-widget/approve",
        data={"requirement_id": ["r4", "r5", "r4"], "back": "/report?project=little-widget&q=&verdict=draft"},
        follow_redirects=False,
    )
    assert seen == [
        ("requirements.publish_requirement", "requirements", "little-widget", {"requirement_id": "r4"}),
        ("requirements.publish_requirement", "requirements", "little-widget", {"requirement_id": "r5"}),
    ]
    location = unquote(response.headers["location"])
    assert location.startswith("/report?project=little-widget&q=&verdict=draft&") and "Approved 2 requirements." in location


def test_approving_reports_failures_and_never_redirects_off_the_report(test_client, monkeypatch):
    monkeypatch.setattr(client, "create_job", lambda paths, **f: {"status": "failed", "error": "requirement 'x' not found"})
    response = test_client.post("/report/little-widget/approve", data={"requirement_id": "x", "back": "https://evil.example"}, follow_redirects=False)
    location = unquote(response.headers["location"])
    assert location.startswith("/report?project=little-widget")
    assert "Approved 0 requirements. 1 could not be approved: requirement 'x' not found" in location
