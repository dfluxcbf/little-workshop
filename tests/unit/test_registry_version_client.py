"""registry/version_client.py: reaching Version Control & Release's
Pillar Interface over a real Unix socket, discovered through a real
(stub) Core — the same discipline test_policy_registry_client.py
already established for its own cross-pillar client. M16's
`bootstrap-little-project` parity gap."""

from __future__ import annotations

import threading
import time

import pytest
import uvicorn
from fastapi import FastAPI

from common.paths import Paths
from registry import version_client


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


def _core_stub(version_socket):
    app = FastAPI()

    @app.get("/v1/status")
    def status() -> dict:
        return {"pillars": {"version": {"status": "active", "socket_path": str(version_socket)}}}

    return app


def test_init_version_tracking_returns_false_when_core_unreachable(paths):
    assert version_client.init_version_tracking(paths, "little-widget", timeout=0.5) is False


def test_init_version_tracking_returns_false_when_core_does_not_know_version(paths):
    app = FastAPI()

    @app.get("/v1/status")
    def status() -> dict:
        return {"pillars": {}}

    core_server = _UvicornThread(app, paths.core_socket_path)
    core_server.start()
    try:
        assert version_client.init_version_tracking(paths, "little-widget", timeout=2.0) is False
    finally:
        core_server.stop()


def test_init_version_tracking_returns_true_on_a_real_succeeded_outcome(paths):
    captured = {}
    version_socket = paths.socket_path("version")
    version_app = FastAPI()

    @version_app.post("/v1/jobs/version.init")
    def job_init(request: dict) -> dict:
        captured["request"] = request
        return {"outcome": "succeeded", "result": {}}

    version_server = _UvicornThread(version_app, version_socket)
    version_server.start()
    core_server = _UvicornThread(_core_stub(version_socket), paths.core_socket_path)
    core_server.start()
    try:
        result = version_client.init_version_tracking(paths, "little-widget", timeout=2.0)
        assert result is True
        assert captured["request"]["project_id"] == "little-widget"
        assert captured["request"]["params"] == {"production_branch": "master", "integration_branch": "dev"}
    finally:
        version_server.stop()
        core_server.stop()


def test_init_version_tracking_returns_false_on_a_failed_outcome(paths):
    version_socket = paths.socket_path("version")
    version_app = FastAPI()

    @version_app.post("/v1/jobs/version.init")
    def job_init(request: dict) -> dict:
        return {"outcome": "failed", "error": "not a git repository"}

    version_server = _UvicornThread(version_app, version_socket)
    version_server.start()
    core_server = _UvicornThread(_core_stub(version_socket), paths.core_socket_path)
    core_server.start()
    try:
        assert version_client.init_version_tracking(paths, "little-widget", timeout=2.0) is False
    finally:
        version_server.stop()
        core_server.stop()


def test_init_version_tracking_passes_through_explicit_branch_names(paths):
    captured = {}
    version_socket = paths.socket_path("version")
    version_app = FastAPI()

    @version_app.post("/v1/jobs/version.init")
    def job_init(request: dict) -> dict:
        captured["params"] = request["params"]
        return {"outcome": "succeeded", "result": {}}

    version_server = _UvicornThread(version_app, version_socket)
    version_server.start()
    core_server = _UvicornThread(_core_stub(version_socket), paths.core_socket_path)
    core_server.start()
    try:
        version_client.init_version_tracking(
            paths, "little-widget", production_branch="main", integration_branch="develop", timeout=2.0,
        )
        assert captured["params"] == {"production_branch": "main", "integration_branch": "develop"}
    finally:
        version_server.stop()
        core_server.stop()
