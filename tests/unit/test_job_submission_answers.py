"""A Job that has to wait (behind its policy checks or a busy queue_key)
is answered at once, queued, with its id; and a pillar that took a
request but has not answered yet is never reported "unreachable".
REQ-000000271."""

import contextlib
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from orchestration import db
from orchestration.app import build_app
from orchestration.models import Job, JobStatus


@pytest.fixture
def conn(tmp_path):
    import threading

    before = set(threading.enumerate())
    connection = db.connect(tmp_path / "orchestration.db")
    yield connection
    for thread in set(threading.enumerate()) - before:  # the background pump, done with the db
        thread.join(10)
    connection.close()


def test_a_job_behind_a_slow_policy_check_is_answered_queued_at_once(conn, monkeypatch):
    """The dependency is queued and ready: the pump runs it (a test-suite
    policy check takes minutes), and the request must not wait for it."""
    import threading
    import time

    from orchestration import app as app_module
    from orchestration import dispatch

    release = threading.Event()
    real_dispatch_job = dispatch.dispatch_job

    def slow_dispatch_job(conn_, job, *a, **k):
        if job.type == "policy.check_project":
            release.wait(10)
        return real_dispatch_job(conn_, job, *a, **k)

    monkeypatch.setattr(dispatch, "dispatch_job", slow_dispatch_job)
    client = TestClient(app_module.build_app(conn, socket_lookup=lambda pillar: None))
    check = Job(type="policy.check_project", target_pillar="policy", project_id="p")
    db.insert_job(conn, check)

    started = time.monotonic()
    body = client.post(
        "/v1/jobs", json={"type": "version.improvement_finish", "target_pillar": "version", "depends_on": [check.id]}
    ).json()
    elapsed = time.monotonic() - started
    release.set()

    assert body["status"] == "queued" and body["id"]
    assert elapsed < 5


def test_a_job_behind_a_busy_queue_key_is_answered_queued(conn):
    client = TestClient(build_app(conn, socket_lookup=lambda pillar: None))
    holder = Job(type="agent.run_task", target_pillar="agent", project_id="p", queue_key="p", status=JobStatus.RUNNING)
    db.insert_job(conn, holder)

    body = client.post("/v1/jobs", json={"type": "version.improvement_finish", "target_pillar": "version", "queue_key": "p"}).json()

    assert body["status"] == "queued"


def test_a_ready_job_is_still_answered_with_its_outcome(conn):
    client = TestClient(build_app(conn, socket_lookup=lambda pillar: None))
    body = client.post("/v1/jobs", json={"type": "noop", "target_pillar": "reference"}).json()
    assert body["status"] == "failed" and body["error"] == "pillar disabled"


def test_mcp_client_says_a_slow_answer_is_not_unreachable():
    from mcp_server.client import _failure

    request = httpx.Request("POST", "http://x/v1/jobs")
    slow = _failure("orchestration", httpx.ReadTimeout("slow", request=request), 30.0)
    assert "has not answered within 30s" in slow and "unreachable" not in slow
    assert _failure("orchestration", httpx.ConnectError("refused", request=request), 30.0) == "orchestration daemon unreachable"


def test_cli_client_says_a_slow_answer_is_not_unreachable(monkeypatch, tmp_path):
    from cli import client
    from common.paths import Paths

    class Slow:
        """A daemon that took the request and answers nothing in time, reads included. The post lags,
        so `lws` reads the Job for its progress while it waits, as it does whenever the post is not
        answered at once (a busy machine made that read fail this test, the stub having no `get`)."""

        def post(self, *a, **k):
            time.sleep(0.2)
            raise httpx.ReadTimeout("slow", request=httpx.Request("POST", "http://x/v1/jobs"))

        def get(self, *a, **k):
            raise httpx.ReadTimeout("slow", request=httpx.Request("GET", "http://x/v1/jobs/j"))

    monkeypatch.setattr(client, "client_for_socket", lambda *a, **k: contextlib.nullcontext(Slow()))
    paths = Paths(config_home=tmp_path / "c", state_home=tmp_path / "s")
    with pytest.raises(client.DaemonUnavailable, match="has not answered within 30s"):
        client.create_job(paths, type="noop", target_pillar="reference")
