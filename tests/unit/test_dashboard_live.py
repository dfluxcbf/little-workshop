"""Live pages: one event stream per page, events from Orchestration's change feed and the pillars'
version reads, the peek of a working Claude, and the pages that subscribe."""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from common.paths import Paths
from dashboard import app as app_module
from dashboard import client, live
from test_dashboard_app import _job, _stub_jobs, _usage_summary


@pytest.fixture
def paths(tmp_path):
    return Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")


@pytest.fixture
def test_client(paths):
    return TestClient(app_module.build_app(paths))


class _Recorder:
    """Stands in for a page's subscription: what it was sent."""

    def __init__(self, hub, topics):
        self.loop = asyncio.new_event_loop()
        self.subscriber = hub.subscribe(topics, self.loop)

    def messages(self):
        self.loop.run_until_complete(asyncio.sleep(0))  # deliver what call_soon_threadsafe queued
        found = []
        while not self.subscriber.queue.empty():
            found.append(self.subscriber.queue.get_nowait())
        return found

    def close(self):
        self.loop.close()


# --- REQ-000000379: Pages update themselves ---


def test_the_hub_sends_a_topic_only_to_the_pages_showing_it():
    hub = live.Hub()
    jobs_page, task_page = _Recorder(hub, ["jobs"]), _Recorder(hub, ["tasks", "peek:j1"])
    try:
        assert hub.topics() == {"jobs", "tasks", "peek:j1"}
        assert hub.publish("jobs") == 1 and hub.publish("peek:j1", {"events": [1]}) == 1 and hub.publish("usage") == 0
        assert jobs_page.messages() == [{"topic": "jobs"}]
        assert task_page.messages() == [{"topic": "peek:j1", "data": {"events": [1]}}]
        hub.unsubscribe(jobs_page.subscriber)
        assert hub.topics() == {"tasks", "peek:j1"}
    finally:
        jobs_page.close()
        task_page.close()


def test_a_change_of_orchestration_concerns_the_pages_that_show_it():
    task_job = {"kind": "job", "id": "j1", "type": "agent.run_task", "status": "running"}
    assert {"jobs", "job:j1", "tasks", "dashboard", "schedule", "attention"} <= live.job_topics(task_job)
    assert live.job_topics({"kind": "step", "id": "j1", "type": "agent.run_task"}) == {"jobs", "job:j1"}
    assert "policies" in live.job_topics({"kind": "job", "id": "j2", "type": "policy.check_project", "status": "failed"})
    assert "backlog" in live.job_topics({"kind": "job", "id": "j3", "type": "registry.backlog_refine", "status": "succeeded"})
    assert "dashboard" in live.job_topics({"kind": "job", "id": "j4", "type": "deployment.deploy", "status": "running"})
    assert live.job_topics({"kind": "scheduled_task", "id": "s1"}) == {"schedule"}
    assert live.settled({"kind": "job", "status": "failed"}) and not live.settled({"kind": "step", "status": "failed"})


def test_the_orchestration_watcher_turns_each_round_of_changes_into_events(paths, monkeypatch):
    hub = live.Hub()
    page = _Recorder(hub, ["jobs", "job:j1", "tasks", "statistics"])
    answers = iter([
        {"boot": "b1", "seq": 10, "changes": [], "reset": False},
        {"boot": "b1", "seq": 12, "reset": False, "changes": [
            {"seq": 11, "kind": "job", "id": "j1", "type": "agent.run_task", "status": "running"},
            {"seq": 12, "kind": "step", "id": "j1", "type": "agent.run_task", "status": "running"}]},
        {"boot": "b2", "seq": 1, "changes": [], "reset": True},
    ])
    asked = []

    def changes(paths, *, after, boot, timeout=0.0):
        asked.append((after, boot))
        return next(answers)

    monkeypatch.setattr(client, "orchestration_changes", changes)
    watchers = live.Watchers(paths, hub)
    try:
        cursor = watchers.follow_orchestration_once(None)  # learns where the feed stands: no event
        assert cursor == {"boot": "b1", "seq": 10} and page.messages() == []
        cursor = watchers.follow_orchestration_once(cursor)
        assert sorted(m["topic"] for m in page.messages()) == ["job:j1", "jobs", "tasks"]  # once each
        cursor = watchers.follow_orchestration_once(cursor)  # Orchestration restarted: everything is looked at again
        assert {"jobs", "tasks", "job:j1"} <= {m["topic"] for m in page.messages()}
        assert asked == [(None, None), (10, "b1"), (12, "b1")] and cursor == {"boot": "b2", "seq": 1}
    finally:
        page.close()


def test_versions_are_compared_only_while_a_page_shows_them_and_statistics_recount_when_jobs_settle(paths, monkeypatch):
    hub = live.Hub()
    versions = {"backlog": "a:1"}
    reads = []

    def backlog_version(paths, **kw):
        reads.append("backlog")
        return versions["backlog"]

    monkeypatch.setattr(client, "backlog_version", backlog_version)
    monkeypatch.setattr(client, "usage_version", lambda paths, **kw: pytest.fail("no page shows usage"))
    monkeypatch.setattr(client, "incidents_version", lambda paths, **kw: pytest.fail("no page shows incidents"))
    recounts = []
    clock = [100.0]
    watchers = live.Watchers(paths, hub, recount_statistics=lambda: recounts.append(1), clock=lambda: clock[0])
    watchers.check_versions_once()
    assert reads == []  # nobody is looking: nothing is read
    page = _Recorder(hub, ["backlog", "statistics"])
    try:
        watchers.check_versions_once()  # the first read only learns the version
        versions["backlog"] = "a:2"
        watchers.check_versions_once()
        assert [m["topic"] for m in page.messages()] == ["backlog"]

        watchers.statistics_due = True
        watchers.check_versions_once()
        watchers.statistics_due = True
        clock[0] += 5  # sooner than STATISTICS_EVERY: wait
        watchers.check_versions_once()
        assert recounts == [1] and [m["topic"] for m in page.messages()] == ["statistics"]
        clock[0] += live.STATISTICS_EVERY
        watchers.check_versions_once()
        assert recounts == [1, 1]
    finally:
        page.close()


def test_the_peek_watcher_sends_what_claude_added_and_once_more_when_it_stops(paths, monkeypatch):
    hub = live.Hub()
    transcript = {"next": 100, "running": True, "events": []}

    def agent_peek(paths, job_id, *, after=0, limit=200, **kw):
        if after < 0:
            return {"next": transcript["next"], "running": transcript["running"], "events": []}
        return {"next": transcript["next"], "running": transcript["running"],
                "events": [e for e in transcript["events"] if e["at"] >= after], "skipped": 0}

    monkeypatch.setattr(client, "agent_peek", agent_peek)
    watchers = live.Watchers(paths, hub)
    page = _Recorder(hub, ["peek:j1"])
    try:
        watchers.check_peeks_once()  # learns where the transcript ends
        watchers.check_peeks_once()
        assert page.messages() == []  # nothing new
        transcript.update(next=180, events=[{"at": 100, "kind": "tool", "tool": "Bash", "text": "pytest"}])
        watchers.check_peeks_once()
        (message,) = page.messages()
        assert message["topic"] == "peek:j1" and message["data"]["from"] == 100 and message["data"]["next"] == 180
        assert message["data"]["events"][0]["tool"] == "Bash" and message["data"]["running"] is True
        transcript.update(running=False, events=[])
        watchers.check_peeks_once()
        watchers.check_peeks_once()
        assert [m["data"]["running"] for m in page.messages()] == [False]  # said once
    finally:
        page.close()


def test_the_event_stream_sends_the_pages_topics_as_they_change(paths):
    app = app_module.build_app(paths)
    route = next(r for r in app.routes if getattr(r, "path", "") == "/live/events")

    class FakeRequest:
        async def is_disconnected(self):
            return False

    async def scenario():
        response = await route.endpoint(FakeRequest(), topics="jobs,job:j1,jobs")
        stream = response.body_iterator
        first = await stream.__anext__()
        assert first.startswith("retry:")
        assert live.HUB.topics() >= {"jobs", "job:j1"}
        live.publish("job:j1")
        live.publish("tasks")  # not this page's
        live.publish("jobs")
        events = [await stream.__anext__(), await stream.__anext__()]
        await stream.aclose()
        return response, events

    response, events = asyncio.run(scenario())
    assert response.media_type == "text/event-stream" and response.headers["cache-control"] == "no-cache"
    assert [json.loads(e.split("data: ", 1)[1]) for e in events] == [{"topic": "job:j1"}, {"topic": "jobs"}]
    assert "job:j1" not in live.HUB.topics()  # the page left: its subscription is gone


def test_every_live_page_names_its_topics_and_marks_what_may_be_replaced(test_client, monkeypatch):
    monkeypatch.setattr(client, "list_projects", lambda paths, status=None, **kw: [])
    monkeypatch.setattr(client, "list_jobs", lambda paths, status=None, **kw: [])
    monkeypatch.setattr(client, "core_status", lambda paths: {"pillars": {}})
    monkeypatch.setattr(client, "usage_summary", lambda paths, **kw: _usage_summary())
    monkeypatch.setattr(client, "usage_doctor", lambda paths: None)
    monkeypatch.setattr(client, "usage_findings", lambda paths, **kw: [])
    monkeypatch.setattr(client, "usage_candidates", lambda paths, **kw: [])
    monkeypatch.setattr(client, "list_incidents", lambda paths, status=None: [])
    for url, topics, region in [
        ("/", "dashboard", "dash-live"), ("/jobs", "jobs", "jobs-live"), ("/tasks", "tasks", "tasks-live"),
        ("/incidents", "incidents", "incidents-live"), ("/settings", "attention", "attention-live"),
        ("/statistics", "statistics", "stats-live"), ("/usage", "usage", "usage-live-data"), ("/schedule", "schedule", "schedule-live"),
    ]:
        page = test_client.get(url).text
        assert f'data-live="{topics}"' in page, url
        assert f'id="{region}"' in page and "data-live-region" in page, url
        assert "/static/live.js" in page and "setInterval" not in page.split("/static/live.js")[0].split("<main")[1], url


def test_the_attention_banner_follows_the_stream_instead_of_a_timer(test_client):
    page = test_client.get("/offline").text
    assert "window.refreshAttention" in page and "setTimeout(poll" not in page and "/static/live.js" in page


def test_the_live_script_replaces_only_changed_regions_and_never_what_the_user_types(test_client):
    script = test_client.get("/static/live.js").text
    assert "new EventSource('/live/events?topics='" in script and "data-live-region" in script
    assert "typingIn(region)" in script and "details[open]" in script and "setInterval" not in script


# --- REQ-000000380: Claude can be watched while it works ---


def _peek(**overrides):
    base = {"available": True, "running": True, "source": "run", "next": 420, "skipped": 0,
            "events": [{"at": "2026-10-04T20:10:00Z", "kind": "tool", "tool": "Bash", "text": "python3 -m pytest tests/unit"}]}
    base.update(overrides)
    return base


def test_the_task_and_job_pages_show_the_peek_and_follow_it_while_claude_works(test_client, monkeypatch):
    job = {**_job(1, type="agent.run_task", status="running", project_id="little-meals", result=None), "steps": []}
    run = {"id": "r1", "job_id": job["id"], "project_id": "little-meals", "title": "Do it", "status": "running",
           "created_at": "2026-10-04T20:00:00+00:00", "updated_at": "2026-10-04T20:00:00+00:00"}
    monkeypatch.setattr(client, "agent_run", lambda paths, project, run_id: run)
    monkeypatch.setattr(client, "agent_run_for_job", lambda paths, job_id: run)
    monkeypatch.setattr(client, "get_job", lambda paths, job_id: job)
    monkeypatch.setattr(client, "scheduled_tasks", lambda paths: [])
    monkeypatch.setattr(client, "agent_peek", lambda paths, job_id, **kw: _peek())
    for url in ("/tasks/little-meals/r1", f"/jobs/{job['id']}"):
        page = test_client.get(url).text
        assert "Claude peek" in page and f'data-peek-job="{job["id"]}"' in page and "python3 -m pytest tests/unit" in page
        assert f"peek:{job['id']}" in page.split('data-live="', 1)[1].split('"', 1)[0], url
        assert "/static/peek.js" in page
    monkeypatch.setattr(client, "agent_peek", lambda paths, job_id, **kw: _peek(running=False))
    page = test_client.get(f"/jobs/{job['id']}").text
    assert "peek:" not in page.split('data-live="', 1)[1].split('"', 1)[0] and ">finished<" in page


def test_a_page_that_missed_part_of_the_peek_catches_up_through_the_dashboard(test_client, monkeypatch):
    asked = []
    monkeypatch.setattr(client, "agent_peek", lambda paths, job_id, *, after=0, **kw: asked.append((job_id, after)) or _peek(next=900))
    assert test_client.get("/jobs/j1/peek.json", params={"after": 420}).json()["next"] == 900 and asked == [("j1", 420)]
    monkeypatch.setattr(client, "agent_peek", lambda paths, job_id, **kw: None)
    assert test_client.get("/jobs/j1/peek.json").status_code == 404


def test_the_peek_script_appends_only_what_follows_on_from_what_is_shown(test_client):
    script = test_client.get("/static/peek.js").text
    assert "message.data.from !== next" in script and "catchUp()" in script and "/peek.json?after=" in script


# --- REQ-000000368: Unexpected behaviour becomes an incident (the pages show the kind) ---


def test_incidents_show_their_kind(test_client, monkeypatch):
    incident = {"id": "i1", "project_id": "little-workshop", "pillar": "orchestration", "context": "job j1", "kind": "unexpected",
                "exception_type": "StuckJob", "message": "agent.run_task was still running after 6 hours", "traceback": "job j1",
                "status": "open", "created_at": "2026-10-04T20:00:00+00:00"}
    monkeypatch.setattr(client, "list_incidents", lambda paths, status=None: [incident])
    monkeypatch.setattr(client, "get_incident", lambda paths, incident_id: incident)
    assert "unexpected behaviour" in test_client.get("/incidents").text
    detail = test_client.get("/incidents/i1").text
    assert "unexpected behaviour" in detail and ">Details<" in detail and "Traceback" not in detail


# --- REQ-000000374: Hovering a Claude usage plot shows the day's numbers ---


def test_the_usage_plots_mark_the_day_pointed_at_and_list_every_series_value(test_client):
    script = test_client.get("/static/usage.js").text
    for wiring in ("addEventListener('pointermove', show)", "addEventListener('pointerdown', show)", "addEventListener('pointerleave'",
                   "'stats-cursor'", "el('div', 'stats-tip')", "shownSeries.forEach", "'Total'", "'Nothing measured'"):
        assert wiring in script, wiring
    assert "svg('title'" not in script  # the browser's slow, hover-only tooltips are gone


# --- REQ-000000388: Pillar health updates the pages that show it ---


def test_a_pillars_health_changing_updates_the_pillars_and_dashboard_pages(paths, monkeypatch):
    hub = live.Hub()
    status = {"pillars": {"agent": {"status": "healthy", "crash_count": 0, "interface_version": "v1"}}}
    reads = []
    monkeypatch.setattr(client, "core_status", lambda paths, **kw: reads.append(1) or json.loads(json.dumps(status)))
    for name in ("backlog_version", "usage_version", "incidents_version"):
        monkeypatch.setattr(client, name, lambda paths, **kw: "same")
    watchers = live.Watchers(paths, hub)
    watchers.check_versions_once()
    assert reads == []  # neither page is open: Core is not asked
    page = _Recorder(hub, ["dashboard"])
    try:
        watchers.check_versions_once()
        status["pillars"]["agent"].update(status="unhealthy", crash_count=1)
        watchers.check_versions_once()
        assert [m["topic"] for m in page.messages()] == ["dashboard"]
    finally:
        page.close()
    assert "pillars" in live.job_topics({"kind": "job", "id": "j", "type": "deployment.redeploy_pillar", "status": "succeeded"})


def test_the_pillars_page_follows_its_stream(test_client, monkeypatch):
    monkeypatch.setattr(client, "core_status", lambda paths, **kw: {"pillars": {"agent": {"status": "healthy", "crash_count": 0}}})
    monkeypatch.setattr(client, "deployment_pillars", lambda paths, **kw: None)
    monkeypatch.setattr(client, "list_secrets", lambda paths, **kw: [])
    page = test_client.get("/pillars").text
    assert 'data-live="pillars"' in page and 'id="pillars-live" data-live-region' in page
