"""policy/agent_client.py: reaching Agentic Development's agent.query
directly, over a real Unix socket — no fakes, mirroring
test_policy_registry_client.py's discipline."""

from __future__ import annotations

import threading
import time

import pytest
import uvicorn
from fastapi import FastAPI

from common.paths import Paths
from policy import agent_client
from policy.agent_client import AgentClientError


class _UvicornThread:
    def __init__(self, app, socket_path):
        self._config = uvicorn.Config(app, uds=str(socket_path), log_level="warning")
        self._server = uvicorn.Server(self._config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)

    def start(self) -> None:
        self._thread.start()
        for _ in range(100):
            if getattr(self._server, "started", False):
                return
            time.sleep(0.05)
        raise RuntimeError("test server did not start in time")

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=5)


@pytest.fixture
def paths(tmp_path):
    p = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    p.ensure_layout()
    return p


def _core_stub(agent_socket, *, status: str = "active"):
    app = FastAPI()

    @app.get("/v1/status")
    def status_route() -> dict:
        return {"pillars": {"agent": {"status": status, "socket_path": str(agent_socket)}}}

    return app


def _agent_stub(*, outcome: str = "succeeded", text: str = "hi", error: str = "boom"):
    app = FastAPI()

    @app.post("/v1/jobs/agent.query")
    def job_query(request: dict) -> dict:
        if outcome == "succeeded":
            return {"outcome": "succeeded", "result": {"text": text}}
        return {"outcome": "failed", "error": error}

    return app


def test_query_raises_when_core_unreachable(paths):
    with pytest.raises(AgentClientError, match="unavailable"):
        agent_client.query(paths, "hello?", timeout=0.5)


def test_query_raises_when_agent_socket_stale(paths):
    agent_socket = paths.socket_path("agent")
    core_server = _UvicornThread(_core_stub(agent_socket), paths.core_socket_path)
    core_server.start()
    try:
        with pytest.raises(AgentClientError, match="unreachable"):
            agent_client.query(paths, "hello?", timeout=2.0)
    finally:
        core_server.stop()


def test_query_returns_the_text_on_success(paths):
    agent_socket = paths.socket_path("agent")
    agent_server = _UvicornThread(_agent_stub(text="EARS_OK: WHEN x, the system shall y."), agent_socket)
    agent_server.start()
    core_server = _UvicornThread(_core_stub(agent_socket), paths.core_socket_path)
    core_server.start()
    try:
        assert agent_client.query(paths, "refine this", timeout=2.0) == "EARS_OK: WHEN x, the system shall y."
    finally:
        agent_server.stop()
        core_server.stop()


def test_query_raises_when_agent_query_itself_reports_failure(paths):
    agent_socket = paths.socket_path("agent")
    agent_server = _UvicornThread(_agent_stub(outcome="failed", error="claude crashed"), agent_socket)
    agent_server.start()
    core_server = _UvicornThread(_core_stub(agent_socket), paths.core_socket_path)
    core_server.start()
    try:
        with pytest.raises(AgentClientError, match="claude crashed"):
            agent_client.query(paths, "refine this", timeout=2.0)
    finally:
        agent_server.stop()
        core_server.stop()


def test_query_raises_when_agent_disabled(paths):
    agent_socket = paths.socket_path("agent")
    core_server = _UvicornThread(_core_stub(agent_socket, status="disabled"), paths.core_socket_path)
    core_server.start()
    try:
        with pytest.raises(AgentClientError, match="unavailable"):
            agent_client.query(paths, "x", timeout=2.0)
    finally:
        core_server.stop()
