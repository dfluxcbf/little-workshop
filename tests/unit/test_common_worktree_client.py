"""common/worktree_client.py: reaching Deployment & Servers' worktree
Pillar Interface over a real Unix socket, discovered through a real
(stub) Core — no fakes, the same discipline test_common_registry_client.py
already established for the analogous registry-resolution client."""

from __future__ import annotations

import threading
import time

import pytest
import uvicorn
from fastapi import FastAPI

from common.paths import Paths
from common.worktree_client import WorktreeUnavailable, provision_worktree, release_worktree


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


def _core_stub(deployment_socket, *, status: str = "active"):
    app = FastAPI()

    @app.get("/v1/status")
    def status_route() -> dict:
        return {"pillars": {"deployment": {"status": status, "socket_path": str(deployment_socket)}}}

    return app


def _deployment_stub(*, worktree_path: str = "/tmp/wt1"):
    app = FastAPI()
    released = []

    @app.post("/v1/worktrees")
    def provision() -> dict:
        return {"worktree_id": "wt1", "path": worktree_path}

    @app.delete("/v1/worktrees/{worktree_id}")
    def release(worktree_id: str, project_id: str) -> dict:
        released.append((worktree_id, project_id))
        return {"released": True}

    app.state.released = released
    return app


def test_provision_worktree_raises_when_core_unreachable(paths):
    with pytest.raises(WorktreeUnavailable, match="unavailable"):
        provision_worktree(paths, "little-widget", "HEAD", timeout=0.5)


def test_provision_worktree_raises_when_deployment_socket_stale(paths):
    deployment_socket = paths.socket_path("deployment")
    core_server = _UvicornThread(_core_stub(deployment_socket), paths.core_socket_path)
    core_server.start()
    try:
        with pytest.raises(WorktreeUnavailable, match="unreachable"):
            provision_worktree(paths, "little-widget", "HEAD", timeout=2.0)
    finally:
        core_server.stop()


def test_provision_worktree_returns_id_and_path_on_success(paths):
    deployment_socket = paths.socket_path("deployment")
    deployment_server = _UvicornThread(_deployment_stub(worktree_path="/tmp/real-wt"), deployment_socket)
    deployment_server.start()
    core_server = _UvicornThread(_core_stub(deployment_socket), paths.core_socket_path)
    core_server.start()
    try:
        worktree_id, path = provision_worktree(paths, "little-widget", "HEAD", timeout=2.0)
        assert worktree_id == "wt1"
        assert str(path) == "/tmp/real-wt"
    finally:
        deployment_server.stop()
        core_server.stop()


def test_release_worktree_raises_when_core_unreachable(paths):
    with pytest.raises(WorktreeUnavailable, match="unavailable"):
        release_worktree(paths, "little-widget", "wt1", timeout=0.5)


def test_release_worktree_calls_through_on_success(paths):
    deployment_socket = paths.socket_path("deployment")
    stub_app = _deployment_stub()
    deployment_server = _UvicornThread(stub_app, deployment_socket)
    deployment_server.start()
    core_server = _UvicornThread(_core_stub(deployment_socket), paths.core_socket_path)
    core_server.start()
    try:
        release_worktree(paths, "little-widget", "wt1", timeout=2.0)
        assert stub_app.state.released == [("wt1", "little-widget")]
    finally:
        deployment_server.stop()
        core_server.stop()


def test_provision_worktree_disabled_pillar_is_unavailable(paths):
    deployment_socket = paths.socket_path("deployment")
    core_server = _UvicornThread(_core_stub(deployment_socket, status="disabled"), paths.core_socket_path)
    core_server.start()
    try:
        with pytest.raises(WorktreeUnavailable, match="unavailable"):
            provision_worktree(paths, "little-widget", "HEAD", timeout=2.0)
    finally:
        core_server.stop()
