"""common/uds_client.py: the one place an httpx client bound to a Pillar
Interface's Unix socket gets built. Exercised for real — a genuine
uvicorn server on a genuine Unix socket file, no mocking — since this
module IS the transport every pillar's real traffic goes through."""

from __future__ import annotations

import asyncio
import threading
import time

import httpx
import pytest
import uvicorn
from fastapi import FastAPI

from common.uds_client import async_client_for_socket, client_for_socket


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


def _stub_app() -> FastAPI:
    app = FastAPI()

    @app.get("/ping")
    def ping() -> dict:
        return {"pong": True}

    return app


def test_client_for_socket_real_round_trip(tmp_path):
    socket_path = tmp_path / "stub.sock"
    server = _UvicornThread(_stub_app(), socket_path)
    server.start()
    try:
        with client_for_socket(socket_path, timeout=2.0) as client:
            response = client.get("/ping")
        assert response.status_code == 200
        assert response.json() == {"pong": True}
    finally:
        server.stop()


def test_client_for_socket_connect_error_on_missing_socket(tmp_path):
    with client_for_socket(tmp_path / "does-not-exist.sock", timeout=1.0) as client:
        with pytest.raises(httpx.ConnectError):
            client.get("/ping")


def test_client_for_socket_uses_the_configured_timeout(tmp_path):
    with client_for_socket(tmp_path / "does-not-exist.sock", timeout=2.5) as client:
        assert client.timeout.connect == 2.5


def test_async_client_for_socket_real_round_trip(tmp_path):
    socket_path = tmp_path / "stub-async.sock"
    server = _UvicornThread(_stub_app(), socket_path)
    server.start()
    try:
        async def _call():
            async with async_client_for_socket(socket_path, timeout=2.0) as client:
                return await client.get("/ping")

        response = asyncio.run(_call())
        assert response.status_code == 200
        assert response.json() == {"pong": True}
    finally:
        server.stop()
