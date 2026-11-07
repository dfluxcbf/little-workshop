"""Port probes using plain sockets — ported verbatim from `little-runner`'s
`netutil.py`."""

from __future__ import annotations

import socket
import time
import urllib.error
import urllib.request
from typing import Callable


def port_is_listening(port: int, host: str = "127.0.0.1", timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def wait_for_port(
    port: int, timeout: float, *, interval: float = 1.0,
    sleep: Callable[[float], None] = time.sleep, probe: Callable[[int], bool] = port_is_listening,
    clock: Callable[[], float] = time.monotonic,
) -> bool:
    deadline = clock() + timeout
    while True:
        if probe(port):
            return True
        if clock() >= deadline:
            return False
        sleep(interval)


def wait_for_port_free(
    port: int, timeout: float, *, interval: float = 0.5,
    sleep: Callable[[float], None] = time.sleep, probe: Callable[[int], bool] = port_is_listening,
    clock: Callable[[], float] = time.monotonic,
) -> bool:
    deadline = clock() + timeout
    while True:
        if not probe(port):
            return True
        if clock() >= deadline:
            return False
        sleep(interval)


def http_is_healthy(url: str, timeout: float = 2.0) -> bool:
    """A GET of `url` answered with a status below 400 (a redirect is not followed further)."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310 - a configured local URL
            return response.status < 400
    except urllib.error.HTTPError as exc:
        return exc.code < 400
    except (urllib.error.URLError, OSError, ValueError):
        return False
