"""One place to build an `httpx` client bound to a Pillar Interface's
Unix domain socket — docs/architecture.md's Pillar Interface transport
("HTTP bound to a Unix domain socket, one per pillar... never a network
port"). Every caller (health probe, Orchestration dispatch, the `lws`
CLI) goes through this instead of constructing its own transport.
"""

from __future__ import annotations

from pathlib import Path

import httpx

# The Host header value is irrelevant (nothing routes on it — the socket
# path IS the address) but httpx requires a well-formed URL.
_BASE_URL = "http://pillar"


def client_for_socket(socket_path: Path, *, timeout: float = 5.0) -> httpx.Client:
    transport = httpx.HTTPTransport(uds=str(socket_path))
    return httpx.Client(transport=transport, base_url=_BASE_URL, timeout=timeout)


def async_client_for_socket(socket_path: Path, *, timeout: float = 5.0) -> httpx.AsyncClient:
    transport = httpx.AsyncHTTPTransport(uds=str(socket_path))
    return httpx.AsyncClient(transport=transport, base_url=_BASE_URL, timeout=timeout)
