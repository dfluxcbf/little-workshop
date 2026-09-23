"""common/incident.py: suppression, the report_incident client, and the
middleware that reports a route handler's unhandled exception without
changing its outcome."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from common.incident import install_incident_reporting, no_auto_incident, report_incident
from common.paths import Paths


@pytest.fixture
def paths(tmp_path):
    return Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")


def test_report_incident_is_silent_when_agent_is_unreachable(paths):
    report_incident(paths, project_id="p", pillar="agent", context="ctx", exc=ValueError("boom"))  # must not raise


def test_report_incident_posts_the_exception_to_agent(paths, monkeypatch):
    from common import incident as incident_module

    captured = {}

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, path, json):
            captured["path"], captured["json"] = path, json

    monkeypatch.setattr(incident_module, "resolve_pillar_socket", lambda paths_, pillar, timeout=2.0: "socket")
    monkeypatch.setattr(incident_module, "client_for_socket", lambda socket_path, timeout=3.0: FakeClient())
    try:
        raise ValueError("boom")
    except ValueError as exc:
        report_incident(paths, project_id="little-widget", pillar="agent", context="POST /x", exc=exc)
    assert captured["path"] == "/v1/incidents"
    assert captured["json"]["project_id"] == "little-widget" and captured["json"]["exception_type"] == "ValueError"
    assert "boom" in captured["json"]["message"] and "ValueError: boom" in captured["json"]["traceback"]


def test_no_auto_incident_suppresses_reporting(paths, monkeypatch):
    from common import incident as incident_module

    calls = []
    monkeypatch.setattr(incident_module, "resolve_pillar_socket", lambda *a, **kw: (calls.append(1), "socket")[1])
    with no_auto_incident():
        report_incident(paths, project_id="p", pillar="agent", context="ctx", exc=ValueError("boom"))
    assert calls == []  # never even looked up the socket


def test_suppression_does_not_leak_outside_the_block(paths, monkeypatch):
    from common import incident as incident_module

    calls = []
    monkeypatch.setattr(incident_module, "resolve_pillar_socket", lambda *a, **kw: (calls.append(1), None)[1])
    with no_auto_incident():
        pass
    report_incident(paths, project_id="p", pillar="agent", context="ctx", exc=ValueError("boom"))
    assert calls == [1]


def test_middleware_reports_and_still_lets_the_exception_propagate(paths, monkeypatch):
    reported = []
    monkeypatch.setattr("common.incident.report_incident", lambda paths_, **kw: reported.append(kw))
    app = FastAPI()
    install_incident_reporting(app, paths, pillar="agent")

    @app.get("/boom")
    def boom():
        raise ValueError("kaboom")

    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/boom")
    assert response.status_code == 500
    assert len(reported) == 1
    assert reported[0]["pillar"] == "agent" and reported[0]["context"] == "GET /boom"
    assert isinstance(reported[0]["exc"], ValueError)


def test_middleware_does_not_report_a_normal_http_error(paths, monkeypatch):
    reported = []
    monkeypatch.setattr("common.incident.report_incident", lambda paths_, **kw: reported.append(kw))
    app = FastAPI()
    install_incident_reporting(app, paths, pillar="agent")

    @app.get("/missing")
    def missing():
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="nope")

    client = TestClient(app)
    assert client.get("/missing").status_code == 404
    assert reported == []
