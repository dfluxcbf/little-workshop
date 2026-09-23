"""No dedicated test file existed for netutil.py before this pass — real
sockets throughout, no mocking, since a plain `socket.create_connection`
probe is cheap and deterministic to exercise for real."""

import socket
import threading

import pytest

from deployment.netutil import port_is_listening, wait_for_port, wait_for_port_free


@pytest.fixture
def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_port_is_listening_false_for_a_genuinely_closed_port(free_port):
    assert port_is_listening(free_port) is False


def test_port_is_listening_true_for_a_real_bound_socket():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    try:
        assert port_is_listening(port) is True
    finally:
        server.close()


def test_wait_for_port_returns_true_immediately_when_already_listening():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    try:
        assert wait_for_port(port, timeout=5, sleep=lambda s: None) is True
    finally:
        server.close()


def test_wait_for_port_times_out_on_a_port_that_never_opens(free_port):
    calls = {"clock": 0.0}

    def clock():
        calls["clock"] += 1.0
        return calls["clock"]

    assert wait_for_port(free_port, timeout=2.0, sleep=lambda s: None, clock=clock) is False


def test_wait_for_port_polls_until_a_delayed_listener_opens(free_port):
    """A real server socket opened from a background thread partway
    through the wait — proves the polling loop actually re-probes rather
    than deciding once up front."""
    server_holder = {}
    opened = threading.Event()

    def open_late():
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.bind(("127.0.0.1", free_port))
        server.listen(1)
        server_holder["server"] = server
        opened.set()

    sleeps = {"n": 0}

    def sleep(_seconds):
        sleeps["n"] += 1
        if sleeps["n"] == 1:
            threading.Thread(target=open_late, daemon=True).start()
            opened.wait(timeout=5)

    try:
        assert wait_for_port(free_port, timeout=5, interval=0.01, sleep=sleep) is True
    finally:
        server_holder["server"].close()


def test_wait_for_port_free_returns_true_immediately_when_already_free(free_port):
    assert wait_for_port_free(free_port, timeout=5, sleep=lambda s: None) is True


def test_wait_for_port_free_times_out_while_still_listening():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    calls = {"clock": 0.0}

    def clock():
        calls["clock"] += 1.0
        return calls["clock"]

    try:
        assert wait_for_port_free(port, timeout=2.0, sleep=lambda s: None, clock=clock) is False
    finally:
        server.close()
