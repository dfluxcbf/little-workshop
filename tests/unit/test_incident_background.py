"""Incidents beyond a request's exception: a background thread that dies, a Job stuck running past its
limit, and a pillar that systemd had to restart after it failed."""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

import pytest

from common import incident
from common.paths import Paths
from common.shell import CommandResult


@pytest.fixture
def paths(tmp_path):
    return Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")


@pytest.fixture
def quiet_excepthook():
    """Each test installs the incident hook over a silent one, and the one before comes back afterwards."""
    before = threading.excepthook
    threading.excepthook = lambda args: None
    yield
    threading.excepthook = before


# --- REQ-000000367: Background crashes become incidents ---


def test_an_exception_escaping_a_background_thread_is_reported_as_an_incident(paths, monkeypatch, quiet_excepthook):
    reported = []
    monkeypatch.setattr(incident, "report_incident", lambda p, **kw: reported.append(kw))
    incident.install_thread_incident_reporting(paths, pillar="agent")

    worker = threading.Thread(target=lambda: 1 / 0, name="agent.run_task 1234abcd")
    worker.start()
    worker.join()

    (kw,) = reported
    assert kw["pillar"] == "agent" and kw["project_id"] == "little-workshop"
    assert "agent.run_task 1234abcd" in kw["context"] and isinstance(kw["exc"], ZeroDivisionError)


def test_installing_twice_reports_once_and_a_thread_ending_by_systemexit_is_no_crash(paths, monkeypatch, quiet_excepthook):
    reported = []
    monkeypatch.setattr(incident, "report_incident", lambda p, **kw: reported.append(kw))
    incident.install_thread_incident_reporting(paths, pillar="usage")
    incident.install_thread_incident_reporting(paths, pillar="usage")

    def leave():
        raise SystemExit(0)

    for target in (leave, lambda: [][1]):
        thread = threading.Thread(target=target)
        thread.start()
        thread.join()
    assert len(reported) == 1 and isinstance(reported[0]["exc"], IndexError)


def test_every_pillar_app_installs_the_thread_hook(paths, quiet_excepthook):
    from orchestration import db as orchestration_db
    from orchestration.app import build_app

    conn = orchestration_db.connect(paths.state_home / "o.db")
    try:
        build_app(conn, socket_lookup=lambda p: None, paths=paths)
    finally:
        conn.close()
    assert getattr(threading.excepthook, "incident_pillar", None) == "orchestration"


# --- REQ-000000368: Unexpected behaviour becomes an incident ---


def test_a_job_failed_for_running_too_long_is_an_unexpected_behaviour_incident(tmp_path, monkeypatch):
    from orchestration import db, dispatch
    from orchestration.models import Job, JobStatus

    conn = db.connect(tmp_path / "orchestration.db")
    reported = []
    monkeypatch.setattr(dispatch, "report_problem", lambda target, **kw: reported.append((target, kw)))
    job = Job(type="agent.run_task", target_pillar="agent", project_id="little-workshop", status=JobStatus.RUNNING)
    db.insert_job(conn, job)
    conn.execute("UPDATE jobs SET updated_at = ? WHERE id = ?", ((datetime.now(timezone.utc) - timedelta(hours=7)).isoformat(), job.id))
    conn.commit()

    dispatch.expire_stuck_jobs(conn, lambda pillar: tmp_path / f"{pillar}.sock")

    ((target, kw),) = reported
    assert target == tmp_path / "agent.sock"
    assert kw["kind"] == "unexpected" and kw["pillar"] == "orchestration" and kw["title"] == "StuckJob"
    assert "agent.run_task" in kw["message"] and "little-workshop" in kw["message"] and "6 hours" in kw["message"]
    assert job.id in kw["context"]
    conn.close()


class _FakeSystemctl:
    """`systemctl --user show` answers from a table that a test changes between rounds."""

    def __init__(self):
        self.restarts = {}
        self.calls = []

    def run(self, argv, timeout=None, cwd=None):
        self.calls.append(argv)
        if argv[:3] == ["systemctl", "--user", "show"]:
            units = [a for a in argv[3:] if a.endswith(".service")]
            blocks = [f"Id={u}\nNRestarts={self.restarts.get(u, 0)}" for u in units]
            return CommandResult(0, "\n\n".join(blocks) + "\n", "")
        if argv[:2] == ["journalctl", "--user"]:
            return CommandResult(0, "Traceback (most recent call last):\nValueError: bad config\n", "")
        return CommandResult(0, "active\n", "")


def test_a_pillar_systemd_restarted_after_a_failure_is_a_crash_incident(paths, monkeypatch):
    from common.manifest import Manifest, PillarEntry
    from core.supervisor import Supervisor

    manifest = Manifest(pillars={"agent": PillarEntry(name="agent", enabled=True, command=["python3", "-m", "agent"]),
                                 "usage": PillarEntry(name="usage", enabled=True, command=["python3", "-m", "usage"])})
    runner = _FakeSystemctl()
    reported = []
    supervisor = Supervisor(paths=paths, manifest=manifest, runner=runner,
                            report=lambda target, **kw: reported.append((target, kw)))

    supervisor.check_restarts()  # the first look only learns where each unit stands
    runner.restarts["little-workshop-agent.service"] = 2
    supervisor.check_restarts()
    supervisor.check_restarts()  # nothing new since

    ((target, kw),) = reported
    assert target == paths.socket_path("agent")
    assert kw["kind"] == "crash" and kw["pillar"] == "agent" and kw["title"] == "PillarCrashed"
    assert "restarted it 2 times" in kw["message"] and "ValueError: bad config" in kw["detail"]

    # A unit started anew (a redeploy) counts from zero again: that is no crash.
    runner.restarts["little-workshop-agent.service"] = 0
    supervisor.check_restarts()
    assert len(reported) == 1


def test_the_agent_keeps_an_incidents_kind(tmp_path):
    from fastapi.testclient import TestClient

    from agent import db
    from agent.app import build_app

    conn = db.connect(tmp_path / "agent.db")
    client = TestClient(build_app(Paths(config_home=tmp_path / "c", state_home=tmp_path / "s"), conn))
    made = client.post("/v1/incidents", json={"project_id": "little-workshop", "pillar": "orchestration", "kind": "unexpected",
                                              "exception_type": "StuckJob", "message": "ran 6 hours"}).json()
    assert client.get(f"/v1/incidents/{made['id']}").json()["kind"] == "unexpected"
    plain = client.post("/v1/incidents", json={"project_id": "little-workshop", "pillar": "agent", "exception_type": "KeyError"}).json()
    assert client.get(f"/v1/incidents/{plain['id']}").json()["kind"] == "exception"
    conn.close()


def test_a_problem_report_posts_an_incident_without_an_exception(tmp_path, monkeypatch):
    posted = []

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, path, json):
            posted.append((path, json))

    monkeypatch.setattr(incident, "client_for_socket", FakeClient)
    incident.report_problem(tmp_path / "agent.sock", pillar="core", context="unit x", kind="crash", title="PillarCrashed",
                            message="restarted", detail="log")
    assert posted == [("/v1/incidents", {"project_id": "little-workshop", "pillar": "core", "context": "unit x", "kind": "crash",
                                         "exception_type": "PillarCrashed", "message": "restarted", "traceback": "log"})]
    incident.report_problem(None, pillar="core", context="x", kind="crash", title="t", message="m")  # nowhere to send: no error
    assert len(posted) == 1
