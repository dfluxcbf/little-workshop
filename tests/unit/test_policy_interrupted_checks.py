"""A policy check cut short by the Policy & Compliance pillar stopping: its container goes with the
pillar, no verdict is recorded, and the check runs again when the pillar next starts."""

from __future__ import annotations

import json
import threading
import time

import httpx
import pytest

from common import serving
from common.shell import CommandResult
from policy import app as app_module
from policy import db, sandbox
from policy.app import build_app
from policy.models import CheckOutcome
from policy.sandbox import SandboxResult
from test_policy_app import _seed_policy, _wait_for_check_result, client, conn, paths  # noqa: F401 (fixtures)


@pytest.fixture
def stopping(monkeypatch):
    event = threading.Event()
    monkeypatch.setattr(serving, "stopping", event)
    return event


def _join_workers():
    for thread in threading.enumerate():
        if "(worker)" in thread.name:
            thread.join(timeout=10)


@pytest.fixture
def completions(monkeypatch):
    """What the pillar tells Orchestration about its Jobs (`/v1/jobs/<id>/complete`)."""
    sent = []

    def handler(request):
        sent.append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={})

    monkeypatch.setattr(app_module, "resolve_pillar_socket", lambda paths, pillar, **kw: "fake-socket")
    monkeypatch.setattr(app_module, "client_for_socket",
                        lambda socket_path, *, timeout=5.0: httpx.Client(transport=httpx.MockTransport(handler), base_url="http://pillar"))
    return sent


# --- REQ-000000405: A policy check cut by a restart runs again ---


class _DockerStoppedMidRun:
    """`docker run` still going when the pillar is asked to stop: its shutdown hook removes the
    container, which ends the client."""

    def __init__(self, stopping):
        self.stopping = stopping
        self.calls = []
        self.removed_by_hook = None

    def run(self, argv, timeout=None, cwd=None):
        self.calls.append(argv)
        if argv[:2] == ["docker", "run"]:
            self.stopping.set()
            self.removed_by_hook = sandbox.remove_running_containers(runner=self)
            return CommandResult(137, "", "")
        return CommandResult(0, "", "")


def test_a_stopping_pillar_removes_the_running_container_and_the_cut_run_gives_no_verdict(stopping):
    runner = _DockerStoppedMidRun(stopping)

    with pytest.raises(sandbox.SandboxInterrupted):
        sandbox.run_policy_check("def test_x():\n    assert True\n", {}, runner=runner)

    run = next(a for a in runner.calls if a[:2] == ["docker", "run"])
    name = next(a for a in run if a.startswith("--name=")).removeprefix("--name=")
    assert runner.removed_by_hook == [name]
    assert ["docker", "rm", "-f", name] in runner.calls
    assert sandbox._running == set()  # nothing left to remove at the next stop


def test_a_check_cut_by_the_stop_records_nothing_and_runs_again_at_the_next_start(client, conn, paths, monkeypatch, stopping, completions):
    policy = _seed_policy(client, conn)
    monkeypatch.setattr(app_module, "resolve_project", lambda paths, project_id, **kw: {"path": "/tmp/x", "name": project_id})

    def cut_short(*a, **kw):
        stopping.set()
        raise sandbox.SandboxInterrupted("the policy pillar was asked to stop while the check ran")

    monkeypatch.setattr(app_module.sandbox, "run_policy_check", cut_short)
    answer = client.post("/v1/jobs/policy.check_project", json={
        "job_id": "gate-check", "project_id": "proj-a", "params": {"policy_id": policy["id"]},
    }).json()
    assert answer["outcome"] == "accepted"
    _join_workers()

    assert db.latest_check_result(conn, policy_id=policy["id"], project_id="proj-a") is None  # no verdict
    assert completions == []  # the Job is not over
    assert [row["job_id"] for row in db.list_running_checks(conn)] == ["gate-check"]

    # The next start: a new process on the same database.
    stopping.clear()
    monkeypatch.setattr(app_module.sandbox, "run_policy_check", lambda *a, **kw: SandboxResult(CheckOutcome.PASS, "ok"))
    restarted = build_app(conn, paths)

    assert restarted.state.rerun_interrupted_checks() == ["gate-check"]
    result = _wait_for_check_result(conn, policy_id=policy["id"], project_id="proj-a")
    assert result.outcome == CheckOutcome.PASS and result.job_id == "gate-check"
    deadline = time.time() + 5
    while not completions and time.time() < deadline:
        time.sleep(0.02)
    ((path, payload),) = completions
    assert path == "/v1/jobs/gate-check/complete" and payload["succeeded"] is True and payload["result"]["outcome"] == "pass"
    assert db.list_running_checks(conn) == []


def test_a_cut_check_that_can_no_longer_run_completes_its_job_as_failed(conn, paths, completions):
    db.add_running_check(conn, "lost-check", "proj-a", {"policy_id": "deleted-meanwhile"})

    assert build_app(conn, paths).state.rerun_interrupted_checks() == ["lost-check"]

    ((path, payload),) = completions
    assert path == "/v1/jobs/lost-check/complete" and payload["succeeded"] is False and "not found" in payload["error"]
    assert db.list_running_checks(conn) == []


def test_the_pillar_runs_them_again_before_it_serves(paths, monkeypatch, completions):
    import policy.__main__ as policy_main

    previous = db.connect(paths.pillar_db_path("policy"))
    db.add_running_check(previous, "cut-check", "proj-a", {"policy_id": "deleted-meanwhile"})
    previous.close()
    monkeypatch.setattr(policy_main.Paths, "from_env", staticmethod(lambda: paths))
    left_when_serving = {}

    class Server:
        def __init__(self, config):
            self.started = True

        def run(self):
            check = db.connect(paths.pillar_db_path("policy"))
            left_when_serving["running"] = db.list_running_checks(check)
            check.close()

    monkeypatch.setattr(serving, "_Server", Server)

    policy_main.main()

    assert left_when_serving["running"] == []
    assert [path for path, _payload in completions] == ["/v1/jobs/cut-check/complete"]
