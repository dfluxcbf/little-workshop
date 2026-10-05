"""REQ-000000398: the "Activate remote-control" button on a running Claude job's page.

The app under test is started here with a fake Claude job whose state the test changes (no pillar is
touched, nothing is started), then the page is driven in a browser. The one thing that needs the live
feed -- the job stopping -- is announced the way Orchestration's feed announces it (`live.publish`).
"""

from __future__ import annotations

import socket
import threading
import time

import pytest
import uvicorn
from playwright.sync_api import Page, expect

from common.paths import Paths
from dashboard import app as app_module
from dashboard import client, live

JOB_ID = "00000001aaaabbbbccccdddd"
RUN_PATH = "/tasks/little-meals/r1"


class World:
    """What the fake pillars answer: the job, its task run and the agent's remote-control state."""

    def __init__(self):
        self.job_type, self.job_status, self.run_status = "agent.run_task", "running", "running"
        self.rc: dict | None = {"running": True, "active": False, "can_activate": True, "can_deactivate": False, "already_set_up": False,
                                "session_name": None, "started_at": None, "stopped_at": None}
        self.acted: list[str] = []

    def job(self):
        return {"id": JOB_ID, "type": self.job_type, "target_pillar": "agent", "project_id": "little-meals", "params": {}, "status": self.job_status,
                "queue_key": None, "depends_on": [], "created_by": "mcp", "result": None, "error": None, "steps": [],
                "created_at": "2026-10-05T10:00:00.000000Z", "updated_at": "2026-10-05T10:00:01.000000Z"}

    def run(self):
        return {"id": "r1", "job_id": JOB_ID, "project_id": "little-meals", "title": "Do it", "status": self.run_status,
                "created_at": "2026-10-05T10:00:00+00:00", "updated_at": "2026-10-05T10:00:00+00:00"}

    def stop(self):
        self.job_status, self.run_status = "failed", "stopped"
        self.rc = {**(self.rc or {}), "running": False, "can_activate": False, "can_deactivate": False, "active": False}
        live.publish(f"job:{JOB_ID}")
        live.publish("tasks")

    def act(self, paths, job_id, action, project_id=""):
        self.acted.append(action)
        if action == "activate":
            self.rc = {**self.rc, "active": True, "can_activate": False, "can_deactivate": True, "session_name": "lws-little-meals-rc-00000001",
                       "started_at": "2026-10-05T10:01:00+00:00"}
            return {"session_name": "lws-little-meals-rc-00000001"}
        self.rc = {**self.rc, "active": False, "can_deactivate": False, "can_activate": True, "stopped_at": "2026-10-05T10:02:00+00:00"}
        return {"deactivated": True}


@pytest.fixture
def world(tmp_path, monkeypatch):
    world = World()
    monkeypatch.setattr(client, "get_job", lambda paths, job_id: world.job())
    monkeypatch.setattr(client, "agent_run", lambda paths, project, run_id: world.run())
    monkeypatch.setattr(client, "agent_run_for_job", lambda paths, job_id: world.run())
    monkeypatch.setattr(client, "agent_peek", lambda paths, job_id, **kw: None)
    monkeypatch.setattr(client, "scheduled_tasks", lambda paths: [])
    monkeypatch.setattr(client, "agent_remote_control", lambda paths, job_id, project_id="": world.rc)
    monkeypatch.setattr(client, "agent_remote_control_act", world.act)
    paths = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app_module.build_app(paths), host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    world.url = f"http://127.0.0.1:{port}"
    yield world
    server.force_exit = True
    server.should_exit = True
    thread.join(timeout=10)


@pytest.mark.parametrize("path", [RUN_PATH, f"/jobs/{JOB_ID}"])
def test_a_running_claude_job_shows_the_button_and_activating_shows_status_and_name_only(page: Page, world, path):
    page.goto(world.url + path)
    button = page.get_by_role("button", name="Activate remote-control", exact=True)
    expect(button).to_be_visible()
    button.click()
    card = page.locator(".remote-control")
    expect(card).to_contain_text("lws-little-meals-rc-00000001")
    expect(card.locator(".rc-status")).to_have_text("active")
    expect(page.get_by_role("button", name="Activate remote-control", exact=True)).to_have_count(0)
    expect(card.locator("a, img, svg, canvas")).to_have_count(0)  # no link, URL or QR code
    assert "http" not in card.inner_text()


def test_deactivating_keeps_the_job_running_and_the_status_as_history(page: Page, world):
    page.goto(world.url + RUN_PATH)
    page.get_by_role("button", name="Activate remote-control", exact=True).click()
    page.get_by_role("button", name="Deactivate remote-control").click()
    expect(page.locator(".remote-control .rc-status")).to_have_text("ended")
    expect(page.locator(".remote-control")).to_contain_text("lws-little-meals-rc-00000001")
    expect(page.get_by_role("button", name="Stop job")).to_be_visible()  # the task is still running
    assert world.acted == ["activate", "deactivate"]


def test_an_existing_session_is_only_reported(page: Page, world):
    world.rc = {**world.rc, "active": True, "can_activate": False, "already_set_up": True, "session_name": "lws-little-meals-run-00000001",
                "started_at": "2026-10-05T10:00:00+00:00"}
    page.goto(world.url + RUN_PATH)
    expect(page.locator(".remote-control")).to_contain_text("already set up")
    expect(page.locator(".remote-control")).to_contain_text("lws-little-meals-run-00000001")
    expect(page.get_by_role("button", name="Activate remote-control", exact=True)).to_have_count(0)
    expect(page.get_by_role("button", name="Deactivate remote-control")).to_have_count(0)


@pytest.mark.parametrize("path", [RUN_PATH, f"/jobs/{JOB_ID}"])
def test_the_button_disappears_without_a_refresh_when_the_job_stops(page: Page, world, path):
    page.goto(world.url + path)
    expect(page.get_by_role("button", name="Activate remote-control", exact=True)).to_be_visible()
    page.evaluate("window.__stay = 1")
    world.stop()
    expect(page.get_by_role("button", name="Activate remote-control", exact=True)).to_have_count(0)
    assert page.evaluate("window.__stay") == 1  # the same document: nothing reloaded


def test_a_job_that_has_not_started_or_does_not_use_claude_has_no_button(page: Page, world):
    world.job_status, world.run_status, world.rc = "pending_confirmation", "starting", {**world.rc, "running": False, "can_activate": False}
    page.goto(world.url + f"/jobs/{JOB_ID}")
    expect(page.get_by_role("button", name="Activate remote-control", exact=True)).to_have_count(0)
    world.job_type, world.job_status, world.rc = "deployment.redeploy_pillar", "running", None
    page.goto(world.url + f"/jobs/{JOB_ID}")
    expect(page.locator("h1, .job-type").first).to_be_visible()
    expect(page.get_by_role("button", name="Activate remote-control", exact=True)).to_have_count(0)


def test_the_list_views_have_no_button(page: Page, world, monkeypatch):
    monkeypatch.setattr(client, "agent_runs", lambda paths, project: [])
    for path in ("/jobs", "/tasks"):
        page.goto(world.url + path)
        expect(page.get_by_role("button", name="Activate remote-control", exact=True)).to_have_count(0)


def test_phone_viewport_keeps_the_button_usable(page: Page, world):
    page.set_viewport_size({"width": 412, "height": 915})
    page.goto(world.url + RUN_PATH)
    expect(page.get_by_role("button", name="Activate remote-control", exact=True)).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
