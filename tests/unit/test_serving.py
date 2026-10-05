"""A pillar asked to stop leaves on its own, in bounded time: every pillar serves through
`common.serving`, whose stop cuts what is still running instead of waiting for systemd to kill it."""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient

from common import serving
from common.paths import Paths

SRC = Path(__file__).resolve().parents[2] / "src"


@pytest.fixture
def stopping(monkeypatch):
    event = threading.Event()
    monkeypatch.setattr(serving, "stopping", event)
    return event


# --- REQ-000000404: A pillar asked to stop leaves within a minute ---


def test_a_pillar_serves_its_socket_with_a_bounded_stop(monkeypatch, tmp_path):
    seen = {}

    class FakeServer:
        def __init__(self, config):
            seen["config"] = config
            self.started = True

        def run(self):
            seen["ran"] = True

    monkeypatch.setattr(serving, "_Server", FakeServer)
    stale = tmp_path / "pillar.sock"
    stale.write_text("")  # left by an earlier process

    serving.serve(object(), stale)

    assert seen["ran"] and not stale.exists()
    assert seen["config"].uds == str(stale) and seen["config"].timeout_graceful_shutdown == serving.GRACEFUL_SHUTDOWN_SECONDS == 60.0


def test_the_stop_signal_marks_the_process_stopping(stopping):
    server = serving._Server(uvicorn.Config(app=lambda scope, receive, send: None, uds="unused.sock"))

    server.handle_exit(signal.SIGTERM, None)

    assert stopping.is_set() and server.should_exit


def test_every_pillar_serves_through_the_bounded_helper():
    entrypoints = sorted(p for p in SRC.glob("*/__main__.py") if (p.parent / "app.py").exists())  # the pillars and Core
    assert len(entrypoints) >= 11
    for entrypoint in entrypoints:
        text = entrypoint.read_text()
        assert "serving.serve(" in text, f"{entrypoint} must serve through common.serving.serve (bounded stop)"
        assert "uvicorn.run(" not in text, f"{entrypoint} calls uvicorn.run directly: its stop would wait without limit"


_STOPPABLE_PILLAR = """
import sys, time
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from common import serving

serving.GRACEFUL_SHUTDOWN_SECONDS = 1.0
marker = Path(sys.argv[2])

@asynccontextmanager
async def lifespan(app):
    yield
    marker.write_text(f"shutdown hook ran, stopping={serving.stopping.is_set()}")

app = FastAPI(lifespan=lifespan)

@app.get("/hang")
def hang():
    time.sleep(60)  # a request that outlives the stop
    return {}

serving.serve(app, Path(sys.argv[1]))
"""


def test_a_pillar_asked_to_stop_leaves_with_a_request_still_running(tmp_path):
    socket_path, marker = tmp_path / "p.sock", tmp_path / "stopped"
    env = {**os.environ, "PYTHONPATH": str(SRC)}
    process = subprocess.Popen([sys.executable, "-c", _STOPPABLE_PILLAR, str(socket_path), str(marker)], env=env,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 20
        while not socket_path.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert socket_path.exists(), "the pillar never started serving"

        def call_hang():
            transport = httpx.HTTPTransport(uds=str(socket_path))
            try:
                with httpx.Client(transport=transport, base_url="http://pillar", timeout=30) as client:
                    client.get("/hang")
            except httpx.HTTPError:
                pass  # cut by the stop

        threading.Thread(target=call_hang, daemon=True).start()
        time.sleep(0.5)  # the request is in flight

        asked = time.monotonic()
        process.send_signal(signal.SIGTERM)
        process.wait(timeout=15)  # without the bound it waits the 60s request out (systemd kills it at 90s)
        assert time.monotonic() - asked < 10
        assert marker.read_text() == "shutdown hook ran, stopping=True"
    finally:
        if process.poll() is None:
            process.kill()


def test_a_wait_for_a_job_answers_at_once_when_orchestration_is_stopping(tmp_path, stopping):
    from orchestration import db
    from orchestration.app import build_app
    from orchestration.models import Job, JobStatus

    conn = db.connect(tmp_path / "orchestration.db")
    try:
        job = Job(type="noop", target_pillar="reference", status=JobStatus.RUNNING)
        db.insert_job(conn, job)
        client = TestClient(build_app(conn, socket_lookup=lambda pillar: None))
        stopping.set()

        started = time.monotonic()
        body = client.get(f"/v1/jobs/{job.id}/wait", params={"timeout": 300}).json()

        assert time.monotonic() - started < 5 and body["status"] == "running"
    finally:
        conn.close()


def test_a_stopping_dashboard_ends_its_event_streams_at_once(tmp_path, stopping):
    from dashboard import app as app_module
    from dashboard import live

    app = app_module.build_app(Paths(config_home=tmp_path / "config", state_home=tmp_path / "state"))
    route = next(r for r in app.routes if getattr(r, "path", "") == "/live/events")

    class FakeRequest:
        async def is_disconnected(self):
            return False

    async def scenario():
        response = await route.endpoint(FakeRequest(), topics="stop-check")
        stream = response.body_iterator
        assert (await stream.__anext__()).startswith("retry:")
        stopping.set()
        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(stream.__anext__(), timeout=5)

    asyncio.run(scenario())
    assert "stop-check" not in live.HUB.topics()  # the stream's end unsubscribed it
