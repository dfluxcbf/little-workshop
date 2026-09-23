"""A Job that takes minutes at its target pillar must never stall the rest
of Orchestration: dispatch holds the db lock to claim a Job and to record
its outcome, never across the call to the pillar itself. Each test here
parks a fake pillar call on an Event and checks what still answers."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from orchestration import db, dispatch
from orchestration.app import build_app


class FakeResponse:
    def __init__(self, body: dict):
        self.status_code = 200
        self._body = body

    def json(self) -> dict:
        return self._body


class FakePillars:
    """Stands in for `client_for_socket`: a call for a Job type in `hold`
    blocks until that type's Event is set; every call is recorded."""

    def __init__(self):
        self.hold: dict[str, threading.Event] = {}
        self.entered: dict[str, threading.Event] = {}
        self.outcome: dict[str, dict] = {}
        self.calls: list[str] = []

    def __call__(self, socket_path, timeout=5.0):
        pillars = self

        class _Client:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def post(self, path, json=None):
                job_type = path.rsplit("/", 1)[-1]
                pillars.calls.append(job_type)
                pillars.entered.setdefault(job_type, threading.Event()).set()
                if job_type in pillars.hold:
                    assert pillars.hold[job_type].wait(timeout=10)
                return FakeResponse(pillars.outcome.get(job_type, {"outcome": "succeeded", "result": {"ok": job_type}}))

        return _Client()


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "orchestration.db")
    yield connection
    connection.close()


@pytest.fixture
def pillars(monkeypatch):
    fake = FakePillars()
    monkeypatch.setattr(dispatch, "client_for_socket", fake)
    return fake


@pytest.fixture
def app(conn):
    return build_app(conn, socket_lookup=lambda pillar: Path(f"/fake/{pillar}.sock"))


def _submit_in_background(app, body: dict) -> tuple[threading.Thread, dict]:
    out: dict = {}

    def go():
        out["job"] = TestClient(app).post("/v1/jobs", json=body).json()

    thread = threading.Thread(target=go, daemon=True)
    thread.start()
    return thread, out


def _timed(fn):
    start = time.monotonic()
    result = fn()
    return result, time.monotonic() - start


def test_reads_and_other_jobs_answer_while_a_slow_job_is_being_dispatched(app, pillars):
    pillars.hold["deployment.deploy"] = threading.Event()
    thread, out = _submit_in_background(app, {
        "type": "deployment.deploy", "target_pillar": "deployment", "project_id": "p", "queue_key": "p",
    })
    assert pillars.entered.setdefault("deployment.deploy", threading.Event()).wait(timeout=5)

    client = TestClient(app)
    running, took = _timed(lambda: client.get("/v1/jobs", params={"status": "running"}).json())
    assert took < 2 and [j["type"] for j in running] == ["deployment.deploy"]
    assert client.get("/v1/health").json()["running_jobs"] == 1

    # A different project's Job goes through while the deploy is still in flight.
    started, took = _timed(lambda: client.post("/v1/jobs", json={
        "type": "agent.session_start", "target_pillar": "agent", "project_id": "q",
    }).json())
    assert took < 2 and started["status"] == "succeeded"

    pillars.hold["deployment.deploy"].set()
    thread.join(timeout=5)
    assert out["job"]["status"] == "succeeded" and out["job"]["result"] == {"ok": "deployment.deploy"}


def test_a_job_behind_a_busy_queue_key_runs_once_the_first_finishes(app, pillars):
    pillars.hold["deployment.deploy"] = threading.Event()
    first, first_out = _submit_in_background(app, {
        "type": "deployment.deploy", "target_pillar": "deployment", "project_id": "p", "queue_key": "p",
    })
    assert pillars.entered.setdefault("deployment.deploy", threading.Event()).wait(timeout=5)

    client = TestClient(app)
    second = client.post("/v1/jobs", json={
        "type": "deployment.stop", "target_pillar": "deployment", "project_id": "p", "queue_key": "p",
    }).json()
    assert second["status"] == "queued"  # the queue_key mutex still holds while the deploy runs

    pillars.hold["deployment.deploy"].set()
    first.join(timeout=5)
    assert first_out["job"]["status"] == "succeeded"
    assert client.get(f"/v1/jobs/{second['id']}").json()["status"] == "succeeded"
    assert pillars.calls == ["deployment.deploy", "deployment.stop"]


def test_a_completion_callback_during_the_dispatch_call_is_not_overwritten(app, pillars):
    pillars.hold["agent.run_task"] = threading.Event()
    pillars.outcome["agent.run_task"] = {"outcome": "accepted"}
    thread, out = _submit_in_background(app, {"type": "agent.run_task", "target_pillar": "agent", "project_id": "p"})
    assert pillars.entered.setdefault("agent.run_task", threading.Event()).wait(timeout=5)

    client = TestClient(app)
    job_id = client.get("/v1/jobs", params={"status": "running"}).json()[0]["id"]
    done = client.post(f"/v1/jobs/{job_id}/complete", json={"succeeded": True, "result": {"pr": 7}, "error": None})
    assert done.json()["status"] == "succeeded"

    pillars.hold["agent.run_task"].set()
    thread.join(timeout=5)
    final = client.get(f"/v1/jobs/{job_id}").json()
    assert final["status"] == "succeeded" and final["result"] == {"pr": 7}


def test_concurrent_pumps_never_dispatch_the_same_job_twice(app, conn, pillars):
    from orchestration.models import Job

    with_lock = threading.Lock()
    for n in range(5):
        db.insert_job(conn, Job(type=f"build.run{n}", target_pillar="build", project_id=f"p{n}"))
    pumps = [
        threading.Thread(target=lambda: dispatch.dispatch_ready_jobs(conn, lambda p: Path("/fake"), lock=with_lock))
        for _ in range(4)
    ]
    for t in pumps:
        t.start()
    for t in pumps:
        t.join(timeout=5)
    assert sorted(pillars.calls) == [f"build.run{n}" for n in range(5)]
