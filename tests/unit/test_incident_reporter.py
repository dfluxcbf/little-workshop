"""tools/incident_reporter.py — the vendorable reporter a child project copies
into its own tree. Tested here since little-workshop is where it's authored
and kept in sync; a child project's own copy carries these same guarantees."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

_spec = importlib.util.spec_from_file_location(
    "incident_reporter", Path(__file__).resolve().parents[2] / "tools" / "incident_reporter.py"
)
ir = importlib.util.module_from_spec(_spec)
sys.modules["incident_reporter"] = ir
_spec.loader.exec_module(ir)


def test_report_incident_is_silent_when_the_socket_does_not_exist(tmp_path, monkeypatch):
    monkeypatch.setenv("LITTLE_WORKSHOP_AGENT_SOCKET", str(tmp_path / "nope.sock"))
    ir.report_incident(project_id="little-meals", context="ctx", exc=ValueError("boom"))  # must not raise


def test_report_incident_posts_over_the_configured_socket(tmp_path, monkeypatch):
    socket_path = tmp_path / "agent.sock"
    socket_path.touch()
    monkeypatch.setenv("LITTLE_WORKSHOP_AGENT_SOCKET", str(socket_path))
    captured = {}

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, path, json):
            captured["path"], captured["json"] = path, json

    monkeypatch.setattr(ir.httpx, "Client", lambda **kw: FakeClient())
    try:
        raise ValueError("boom")
    except ValueError as exc:
        ir.report_incident(project_id="little-meals", context="POST /x", exc=exc)
    assert captured["path"] == "/v1/incidents"
    assert captured["json"]["project_id"] == "little-meals" and captured["json"]["pillar"] == "server"
    assert captured["json"]["exception_type"] == "ValueError"


def test_no_auto_incident_suppresses_reporting(tmp_path, monkeypatch):
    socket_path = tmp_path / "agent.sock"
    socket_path.touch()
    monkeypatch.setenv("LITTLE_WORKSHOP_AGENT_SOCKET", str(socket_path))
    calls = []
    monkeypatch.setattr(ir.httpx, "Client", lambda **kw: (calls.append(1), pytest.fail("should not be called"))[0])
    with ir.no_auto_incident():
        ir.report_incident(project_id="little-meals", context="ctx", exc=ValueError("boom"))
    assert calls == []


def test_middleware_reports_and_still_lets_the_exception_propagate(tmp_path, monkeypatch):
    reported = []
    monkeypatch.setattr(ir, "report_incident", lambda **kw: reported.append(kw))
    app = FastAPI()
    ir.install_incident_reporting(app, project_id="little-meals")

    @app.get("/boom")
    def boom():
        raise ValueError("kaboom")

    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/boom")
    assert response.status_code == 500
    assert reported[0]["project_id"] == "little-meals" and reported[0]["context"] == "GET /boom"


def test_agent_socket_path_defaults_to_the_well_known_location(monkeypatch):
    monkeypatch.delenv("LITTLE_WORKSHOP_AGENT_SOCKET", raising=False)
    monkeypatch.delenv("LITTLE_WORKSHOP_STATE_HOME", raising=False)
    assert str(ir._agent_socket_path()).endswith(".local/state/little-workshop/sockets/agent.sock")
