"""Routes a client module's `client_for_socket` calls to in-process
FastAPI apps over httpx's ASGI transport instead of a real Unix-domain-
socket file — the same substitution FastAPI's own `TestClient` makes
(it's an httpx.Client over `ASGITransport` too), just applied here to
`cli.client`/`mcp_server.client`'s literal `client_for_socket(socket_path,
...)` calls so their success-path HTTP/JSON logic (status codes, request
bodies, response parsing, exception mapping) runs for real without a real
socket file or a real server thread. Any socket path not in `apps` falls
through to the real `client_for_socket`, so existing "daemon unreachable"
tests (a real, unbound socket path) keep behaving exactly as before.
"""

from __future__ import annotations

from pathlib import Path

_BASE_URL = "http://pillar"


def patch_socket_apps(monkeypatch, client_module, apps: dict) -> None:
    """`apps` maps a `Path` (as returned by `paths.socket_path(...)` /
    `paths.core_socket_path`) to the FastAPI `app` object that should
    answer requests aimed at it. Patches `client_module.client_for_socket`
    (the name as imported into that module with `from ... import
    client_for_socket`) in place.

    Routes through Starlette's own `TestClient` rather than building a raw
    `httpx.Client(transport=httpx.ASGITransport(...))` directly: httpx's
    `ASGITransport` only implements the *async* context-manager protocol
    (`__aenter__`), while every real caller here (`cli.client`,
    `mcp_server.client`, `orchestration.dispatch`) opens its client with a
    plain `with client_for_socket(...) as client:` — a sync `__enter__`.
    `TestClient` is the thing that already bridges that gap correctly (the
    same object every `tests/unit/test_*_app.py` file already uses), so
    reusing it here keeps this a real HTTP/ASGI round trip end to end
    rather than a hand-rolled workaround."""
    from starlette.testclient import TestClient

    real_client_for_socket = client_module.client_for_socket
    normalized = {Path(path): app for path, app in apps.items()}

    def fake(socket_path, *, timeout: float = 5.0):
        app = normalized.get(Path(socket_path))
        if app is None:
            return real_client_for_socket(socket_path, timeout=timeout)
        return TestClient(app, base_url=_BASE_URL)

    monkeypatch.setattr(client_module, "client_for_socket", fake)


def build_fake_core_app(pillar_sockets: dict):
    """A minimal stand-in for Core's real `/v1/status` — reports each
    `{pillar_name: socket_path}` entry as healthy. Used so
    `common.pillar_socket.resolve_pillar_socket` (the discovery step
    `mcp_server.client` and `cli.client.complete_orchestration_job` both
    go through) resolves to a real socket path during a test without
    needing a real Core process."""
    from fastapi import FastAPI

    app = FastAPI()
    pillars = {
        name: {"status": "healthy", "socket_path": str(path), "interface_version": "v1", "crash_count": 0}
        for name, path in pillar_sockets.items()
    }

    @app.get("/v1/status")
    def status() -> dict:
        return {"pillars": pillars}

    return app


def patch_core_discovery(monkeypatch, paths, pillar_sockets: dict) -> None:
    """Makes `resolve_pillar_socket(paths, <pillar>)` resolve for real
    (through a real, if in-process, `/v1/status` call) to whatever real
    socket path each pillar in `pillar_sockets` maps to."""
    import common.pillar_socket as pillar_socket_module

    core_app = build_fake_core_app(pillar_sockets)
    patch_socket_apps(monkeypatch, pillar_socket_module, {paths.core_socket_path: core_app})
