"""policy/registry_client.py: reaching Registry & Portfolio's Pillar
Interface over a real Unix socket, discovered through a real (stub)
Core — no fakes, the same discipline test_common_worktree_client.py
already established."""

from __future__ import annotations

import threading
import time

import pytest
import uvicorn
from fastapi import FastAPI

from common.paths import Paths
from policy import registry_client
from policy.registry_client import RegistryReadError


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


def _core_stub(registry_socket):
    app = FastAPI()

    @app.get("/v1/status")
    def status() -> dict:
        return {"pillars": {"registry": {"status": "active", "socket_path": str(registry_socket)}}}

    return app


def _registry_stub(projects, project_sets):
    app = FastAPI()

    @app.get("/v1/projects")
    def list_projects(status: str | None = None) -> list[dict]:
        return projects

    @app.get("/v1/project_sets")
    def list_project_sets() -> list[dict]:
        return project_sets

    @app.get("/v1/project_sets/{name}")
    def get_project_set(name: str) -> dict:
        from fastapi import HTTPException

        match = next((s for s in project_sets if s["name"] == name), None)
        if match is None:
            raise HTTPException(status_code=404, detail="not found")
        return match

    return app


def test_list_active_project_ids_raises_when_core_unreachable(paths):
    with pytest.raises(RegistryReadError):
        registry_client.list_active_project_ids(paths, timeout=0.5)


def test_list_active_project_ids_returns_names(paths):
    registry_socket = paths.socket_path("registry")
    registry_server = _UvicornThread(_registry_stub([{"name": "a"}, {"name": "b"}], []), registry_socket)
    registry_server.start()
    core_server = _UvicornThread(_core_stub(registry_socket), paths.core_socket_path)
    core_server.start()
    try:
        assert registry_client.list_active_project_ids(paths, timeout=2.0) == ["a", "b"]
    finally:
        registry_server.stop()
        core_server.stop()


def test_project_sets_containing_filters_by_membership(paths):
    registry_socket = paths.socket_path("registry")
    sets = [
        {"name": "household", "project_names": ["a", "b"]},
        {"name": "work", "project_names": ["c"]},
    ]
    registry_server = _UvicornThread(_registry_stub([], sets), registry_socket)
    registry_server.start()
    core_server = _UvicornThread(_core_stub(registry_socket), paths.core_socket_path)
    core_server.start()
    try:
        assert registry_client.project_sets_containing(paths, "a", timeout=2.0) == ["household"]
        assert registry_client.project_sets_containing(paths, "c", timeout=2.0) == ["work"]
        assert registry_client.project_sets_containing(paths, "ghost", timeout=2.0) == []
    finally:
        registry_server.stop()
        core_server.stop()


def test_project_set_members_returns_the_real_list(paths):
    registry_socket = paths.socket_path("registry")
    sets = [{"name": "household", "project_names": ["a", "b"]}]
    registry_server = _UvicornThread(_registry_stub([], sets), registry_socket)
    registry_server.start()
    core_server = _UvicornThread(_core_stub(registry_socket), paths.core_socket_path)
    core_server.start()
    try:
        assert registry_client.project_set_members(paths, "household", timeout=2.0) == ["a", "b"]
    finally:
        registry_server.stop()
        core_server.stop()


def test_project_set_members_returns_empty_for_unknown_set(paths):
    registry_socket = paths.socket_path("registry")
    registry_server = _UvicornThread(_registry_stub([], []), registry_socket)
    registry_server.start()
    core_server = _UvicornThread(_core_stub(registry_socket), paths.core_socket_path)
    core_server.start()
    try:
        assert registry_client.project_set_members(paths, "ghost", timeout=2.0) == []
    finally:
        registry_server.stop()
        core_server.stop()
