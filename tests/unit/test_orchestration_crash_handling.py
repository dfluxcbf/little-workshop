"""What Orchestration does when a pillar takes a Job and never answers: a dispatch that times out, a
dispatch its own previous process never finished, and the ceiling a pillar redeploy needs."""

from datetime import datetime, timezone

import httpx
import pytest

from orchestration import db, dispatch
from orchestration.models import Job, JobStatus


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "orchestration.db")
    yield connection
    connection.close()


@pytest.fixture
def reported(monkeypatch):
    sent = []
    monkeypatch.setattr(dispatch, "report_problem", lambda target, **kw: sent.append((target, kw)))
    monkeypatch.setattr(dispatch, "_timeouts_reported", {})
    return sent


def client_raising(exc_type):
    def _factory(socket_path, *, timeout=5.0):
        def handler(request):
            raise exc_type("timed out", request=request)

        return httpx.Client(transport=httpx.MockTransport(handler), base_url="http://pillar")

    return _factory


# --- REQ-000000402: A dispatch that times out is an incident ---


def test_a_dispatch_the_pillar_took_but_never_answered_is_an_incident_once_an_hour(monkeypatch, tmp_path, conn, reported):
    monkeypatch.setattr(dispatch, "client_for_socket", client_raising(httpx.ReadTimeout))
    clock = [1000.0]
    monkeypatch.setattr(dispatch, "_clock", lambda: clock[0])
    lookup = lambda pillar: tmp_path / f"{pillar}.sock"  # noqa: E731

    jobs = [Job(type="deployment.redeploy_pillar", target_pillar="deployment", project_id="little-workshop") for _ in range(2)]
    for job in jobs:
        db.insert_job(conn, job)
        result = dispatch.dispatch_job(conn, job, lookup, timeout=20.0)
        assert result.status == JobStatus.QUEUED  # requeued, as before
        assert "did not answer within 20s" in result.error and "may run twice" in result.error

    ((target, kw),) = reported  # the second timeout of the same type and pillar within the hour is not reported again
    assert target == tmp_path / "agent.sock"
    assert kw["kind"] == "unexpected" and kw["title"] == "DispatchTimeout" and kw["pillar"] == "orchestration"
    assert "deployment.redeploy_pillar" in kw["message"] and "deployment pillar" in kw["message"]
    assert "20s" in kw["message"] and "twice" in kw["message"]
    assert jobs[0].id in kw["context"] and jobs[0].id in kw["detail"]

    clock[0] += 3601
    later = Job(type="deployment.redeploy_pillar", target_pillar="deployment", project_id="little-workshop")
    db.insert_job(conn, later)
    dispatch.dispatch_job(conn, later, lookup, timeout=20.0)
    assert len(reported) == 2 and later.id in reported[1][1]["context"]


@pytest.mark.parametrize("exc_type", [httpx.ConnectError, httpx.ConnectTimeout])
def test_a_pillar_that_never_took_the_request_is_no_incident(monkeypatch, tmp_path, conn, reported, exc_type):
    monkeypatch.setattr(dispatch, "client_for_socket", client_raising(exc_type))
    job = Job(type="noop", target_pillar="reference")
    db.insert_job(conn, job)

    result = dispatch.dispatch_job(conn, job, lambda pillar: tmp_path / f"{pillar}.sock", timeout=20.0)

    assert result.status == JobStatus.QUEUED and "unreachable" in result.error
    assert reported == []


# --- REQ-000000403: A dispatch Orchestration never finished is settled when it starts ---


def test_at_start_a_job_whose_dispatch_never_returned_is_failed_with_its_dependants(tmp_path, conn, reported):
    lookup = lambda pillar: tmp_path / f"{pillar}.sock"  # noqa: E731
    # Claimed and sent by the previous process, which died before the pillar answered: no run row.
    lost = Job(type="version.improvement_finish", target_pillar="version", project_id="little-workshop", status=JobStatus.RUNNING)
    # Accepted by its pillar, which completes it through the callback: left alone.
    accepted = Job(type="policy.check_project", target_pillar="policy", project_id="little-workshop", status=JobStatus.RUNNING)
    waiting = Job(type="noop", target_pillar="reference", depends_on=[lost.id])
    for job in (lost, accepted, waiting):
        db.insert_job(conn, job)
    now = datetime.now(timezone.utc)
    db.insert_job_run(conn, accepted.id, outcome="running", detail=None, started_at=now, finished_at=now)

    settled = dispatch.settle_interrupted_dispatches(conn, lookup)

    assert [job.id for job in settled] == [lost.id]
    failed = db.get_job(conn, lost.id)
    assert failed.status == JobStatus.FAILED
    assert "Orchestration restarted" in failed.error and "version pillar" in failed.error and "unknown" in failed.error
    assert db.list_job_runs(conn, lost.id)[-1]["outcome"] == "failed"
    assert db.get_job(conn, accepted.id).status == JobStatus.RUNNING
    assert db.get_job(conn, waiting.id).status == JobStatus.FAILED

    ((target, kw),) = reported
    assert target == tmp_path / "agent.sock"
    assert kw["kind"] == "unexpected" and kw["title"] == "InterruptedDispatch" and kw["pillar"] == "orchestration"
    assert "1 Job" in kw["message"]
    assert lost.id in kw["detail"] and "version.improvement_finish" in kw["detail"] and accepted.id not in kw["detail"]


def test_at_start_with_nothing_cut_nothing_is_settled_or_reported(tmp_path, conn, reported):
    done = Job(type="noop", target_pillar="reference", status=JobStatus.SUCCEEDED)
    db.insert_job(conn, done)

    assert dispatch.settle_interrupted_dispatches(conn, lambda pillar: tmp_path / f"{pillar}.sock") == []
    assert reported == []


def test_orchestration_settles_them_before_it_serves(tmp_path, monkeypatch, reported):
    import orchestration.__main__ as orchestration_main
    from common import serving
    from common.paths import Paths

    paths = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    paths.ensure_layout()
    previous = db.connect(paths.pillar_db_path("orchestration"))
    lost = Job(type="noop", target_pillar="reference", status=JobStatus.RUNNING)
    db.insert_job(previous, lost)
    previous.close()
    monkeypatch.setattr(orchestration_main.Paths, "from_env", staticmethod(lambda: paths))
    status_when_serving = {}

    class Server:
        def __init__(self, config):
            self.started = True

        def run(self):
            check = db.connect(paths.pillar_db_path("orchestration"))
            status_when_serving["lost"] = db.get_job(check, lost.id).status
            check.close()

    monkeypatch.setattr(serving, "_Server", Server)

    orchestration_main.main()

    assert status_when_serving["lost"] == JobStatus.FAILED


# --- REQ-000000407: A dispatch the pillar dropped is settled at once ---


@pytest.mark.parametrize("exc_type", [httpx.RemoteProtocolError, httpx.ReadError])
def test_a_dispatch_the_pillar_dropped_without_answering_fails_at_once(monkeypatch, tmp_path, conn, reported, exc_type):
    # What a pillar that stops (a redeploy cuts what still runs) or crashes mid-request answers: nothing.
    monkeypatch.setattr(dispatch, "client_for_socket", client_raising(exc_type))
    job = Job(type="version.improvement_finish", target_pillar="version", project_id="little-workshop")
    db.insert_job(conn, job)

    result = dispatch.dispatch_job(conn, job, lambda pillar: tmp_path / f"{pillar}.sock", timeout=180.0)

    assert result.status == JobStatus.FAILED
    assert "version pillar dropped the request" in result.error and "unknown" in result.error
    assert db.list_job_runs(conn, job.id)[-1]["outcome"] == "failed"
    ((target, kw),) = reported
    assert target == tmp_path / "agent.sock"
    assert kw["kind"] == "unexpected" and kw["title"] == "DispatchDropped" and kw["pillar"] == "orchestration"
    assert job.id in kw["context"] and "version.improvement_finish" in kw["message"]


# --- REQ-000000406: A pillar redeploy is never dispatched twice ---


def test_a_pillar_redeploy_waits_long_enough_for_the_redeploys_queued_before_it():
    # Deployment runs redeploys one at a time under its lock, so a batch's last one waits for all the others.
    assert dispatch.dispatch_timeout_for("deployment.redeploy_pillar") == 1800.0
