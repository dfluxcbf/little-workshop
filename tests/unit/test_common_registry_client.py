"""common/registry_client.py: resolving a project_id against Registry &
Portfolio over a real Pillar Interface, discovered through a real (stub)
Core — no fakes, the same discipline every other common/ test in this
pass uses."""

from __future__ import annotations

import threading
import time

import pytest
import uvicorn
from fastapi import FastAPI

from common.paths import Paths
from common.registry_client import ProjectResolutionError, resolve_project, resolve_project_path


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


def _core_stub(registry_socket, *, status: str = "active"):
    app = FastAPI()

    @app.get("/v1/status")
    def status_route() -> dict:
        return {"pillars": {"registry": {"status": status, "socket_path": str(registry_socket)}}}

    return app


def _registry_stub(project: dict | None):
    app = FastAPI()

    @app.get("/v1/projects/{name}")
    def get_project(name: str) -> dict:
        if project is None or project["name"] != name:
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="not found")
        return project

    return app


def test_resolve_project_rejects_missing_project_id(paths):
    with pytest.raises(ProjectResolutionError):
        resolve_project(paths, None)


def test_resolve_project_rejects_empty_project_id(paths):
    with pytest.raises(ProjectResolutionError):
        resolve_project(paths, "")


def test_resolve_project_raises_when_core_unreachable(paths):
    with pytest.raises(ProjectResolutionError, match="unavailable"):
        resolve_project(paths, "little-widget", timeout=0.5)


def test_resolve_project_raises_when_registry_socket_stale_but_core_thinks_its_active(paths):
    # Core reports registry as active and hands back a real socket path,
    # but nothing is actually listening there — a real ECONNREFUSED, not
    # a mock, hitting resolve_project's own except clause rather than
    # resolve_pillar_socket's.
    registry_socket = paths.socket_path("registry")
    core_server = _UvicornThread(_core_stub(registry_socket), paths.core_socket_path)
    core_server.start()
    try:
        with pytest.raises(ProjectResolutionError, match="unreachable"):
            resolve_project(paths, "little-widget", timeout=2.0)
    finally:
        core_server.stop()


def test_resolve_project_raises_when_registry_reports_404(paths):
    registry_socket = paths.socket_path("registry")
    registry_server = _UvicornThread(_registry_stub(None), registry_socket)
    registry_server.start()
    core_server = _UvicornThread(_core_stub(registry_socket), paths.core_socket_path)
    core_server.start()
    try:
        with pytest.raises(ProjectResolutionError, match="not registered"):
            resolve_project(paths, "little-widget", timeout=2.0)
    finally:
        registry_server.stop()
        core_server.stop()


def test_resolve_project_raises_when_registry_socket_disabled(paths):
    registry_socket = paths.socket_path("registry")
    core_server = _UvicornThread(_core_stub(registry_socket, status="disabled"), paths.core_socket_path)
    core_server.start()
    try:
        with pytest.raises(ProjectResolutionError, match="unavailable"):
            resolve_project(paths, "little-widget", timeout=2.0)
    finally:
        core_server.stop()


def test_resolve_project_returns_full_record_on_success(paths):
    project = {"name": "little-widget", "path": "/tmp/little-widget", "repo_url": "https://github.com/x/y", "status": "active"}
    registry_socket = paths.socket_path("registry")
    registry_server = _UvicornThread(_registry_stub(project), registry_socket)
    registry_server.start()
    core_server = _UvicornThread(_core_stub(registry_socket), paths.core_socket_path)
    core_server.start()
    try:
        record = resolve_project(paths, "little-widget", timeout=2.0)
        assert record["repo_url"] == "https://github.com/x/y"
    finally:
        registry_server.stop()
        core_server.stop()


def test_resolve_project_path_returns_just_the_path(paths):
    project = {"name": "little-widget", "path": "/tmp/little-widget", "repo_url": None, "status": "active"}
    registry_socket = paths.socket_path("registry")
    registry_server = _UvicornThread(_registry_stub(project), registry_socket)
    registry_server.start()
    core_server = _UvicornThread(_core_stub(registry_socket), paths.core_socket_path)
    core_server.start()
    try:
        path = resolve_project_path(paths, "little-widget", timeout=2.0)
        assert str(path) == "/tmp/little-widget"
    finally:
        registry_server.stop()
        core_server.stop()
