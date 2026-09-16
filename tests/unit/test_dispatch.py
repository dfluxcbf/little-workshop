from pathlib import Path

import httpx
import pytest

from orchestration import db, dispatch
from orchestration.models import Job, JobStatus


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "orchestration.db")
    yield connection
    connection.close()


def mock_client_factory(handler):
    def _factory(socket_path, *, timeout=5.0):
        return httpx.Client(transport=httpx.MockTransport(handler), base_url="http://pillar")

    return _factory


def test_dispatch_fails_immediately_when_pillar_disabled(conn):
    job = Job(type="noop", target_pillar="policy")
    db.insert_job(conn, job)

    result = dispatch.dispatch_job(conn, job, socket_lookup=lambda pillar: None)

    assert result.status == JobStatus.FAILED
    assert result.error == "pillar disabled"


def test_dispatch_succeeds_on_200_succeeded_outcome(monkeypatch, tmp_path, conn):
    def handler(request):
        assert request.url.path == "/v1/jobs/noop"
        return httpx.Response(200, json={"outcome": "succeeded", "result": {"done": True}})

    monkeypatch.setattr(dispatch, "client_for_socket", mock_client_factory(handler))

    job = Job(type="noop", target_pillar="reference")
    db.insert_job(conn, job)
    result = dispatch.dispatch_job(conn, job, socket_lookup=lambda pillar: tmp_path / "ref.sock")

    assert result.status == JobStatus.SUCCEEDED
    assert result.result == {"done": True}
    runs = db.list_job_runs(conn, job.id)
    assert runs[-1]["outcome"] == "succeeded"


def test_dispatch_marks_failed_on_pillar_reported_failure(monkeypatch, tmp_path, conn):
    def handler(request):
        return httpx.Response(200, json={"outcome": "failed", "error": "boom"})

    monkeypatch.setattr(dispatch, "client_for_socket", mock_client_factory(handler))
    job = Job(type="noop", target_pillar="reference")
    db.insert_job(conn, job)
    result = dispatch.dispatch_job(conn, job, socket_lookup=lambda pillar: tmp_path / "ref.sock")

    assert result.status == JobStatus.FAILED
    assert result.error == "boom"


def test_dispatch_leaves_accepted_job_running(monkeypatch, tmp_path, conn):
    def handler(request):
        return httpx.Response(200, json={"outcome": "accepted"})

    monkeypatch.setattr(dispatch, "client_for_socket", mock_client_factory(handler))
    job = Job(type="agent.run_task", target_pillar="agent")
    db.insert_job(conn, job)
    result = dispatch.dispatch_job(conn, job, socket_lookup=lambda pillar: tmp_path / "agent.sock")

    assert result.status == JobStatus.RUNNING


def test_dispatch_stays_queued_when_pillar_unreachable(monkeypatch, tmp_path, conn):
    def handler(request):
        raise httpx.ConnectError("no such socket", request=request)

    monkeypatch.setattr(dispatch, "client_for_socket", mock_client_factory(handler))
    job = Job(type="noop", target_pillar="reference")
    db.insert_job(conn, job)
    result = dispatch.dispatch_job(conn, job, socket_lookup=lambda pillar: tmp_path / "ref.sock")

    assert result.status == JobStatus.QUEUED
    assert "unreachable" in result.error


def test_complete_job_records_callback_result(conn):
    job = Job(type="agent.run_task", target_pillar="agent")
    db.insert_job(conn, job)
    job.status = JobStatus.RUNNING
    db.update_job(conn, job)

    result = dispatch.complete_job(conn, job, succeeded=True, result={"pr": 42}, error=None)
    assert result.status == JobStatus.SUCCEEDED
    assert db.get_job(conn, job.id).result == {"pr": 42}


def test_dispatch_ready_jobs_cascades_and_dispatches(monkeypatch, tmp_path, conn):
    def handler(request):
        return httpx.Response(200, json={"outcome": "succeeded", "result": {}})

    monkeypatch.setattr(dispatch, "client_for_socket", mock_client_factory(handler))

    failed_dep = Job(type="noop", target_pillar="reference")
    db.insert_job(conn, failed_dep)
    failed_dep.status = JobStatus.FAILED
    db.update_job(conn, failed_dep)

    blocked_child = Job(type="noop", target_pillar="reference", depends_on=[failed_dep.id])
    db.insert_job(conn, blocked_child)

    free_job = Job(type="noop", target_pillar="reference")
    db.insert_job(conn, free_job)

    results = dispatch.dispatch_ready_jobs(conn, socket_lookup=lambda pillar: tmp_path / "r.sock")

    assert db.get_job(conn, blocked_child.id).status == JobStatus.FAILED
    assert any(j.id == free_job.id and j.status == JobStatus.SUCCEEDED for j in results)


# ---------------------------------------------------------------------------
# stop_job -- docs/milestones.md M9
# ---------------------------------------------------------------------------

def test_stop_job_refuses_a_job_that_is_not_running(conn):
    job = Job(type="agent.run_task", target_pillar="agent", status=JobStatus.QUEUED)
    db.insert_job(conn, job)

    result = dispatch.stop_job(conn, job, socket_lookup=lambda pillar: None)

    assert result == {"stopped": False, "reason": "job is queued, not running"}


def test_stop_job_rejects_a_declared_unstoppable_type_without_a_network_call(conn, monkeypatch):
    def handler(request):
        raise AssertionError("must not attempt a real call for a declared-unstoppable type")

    monkeypatch.setattr(dispatch, "client_for_socket", mock_client_factory(handler))

    job = Job(type="version.feature_start", target_pillar="version", status=JobStatus.RUNNING)
    db.insert_job(conn, job)

    result = dispatch.stop_job(conn, job, socket_lookup=lambda pillar: Path("/x"))

    assert result["stopped"] is False
    assert "declared unstoppable" in result["reason"]


def test_stop_job_reports_unreachable_when_pillar_disabled(conn):
    job = Job(type="agent.run_task", target_pillar="agent", status=JobStatus.RUNNING)
    db.insert_job(conn, job)

    result = dispatch.stop_job(conn, job, socket_lookup=lambda pillar: None)

    assert result == {"stopped": False, "reason": "target pillar unreachable"}


def test_stop_job_probes_and_succeeds_when_the_target_pillar_actually_stops_it(conn, monkeypatch, tmp_path):
    def handler(request):
        assert request.url.path.endswith("/stop")
        return httpx.Response(200, json={"stopped": True})

    monkeypatch.setattr(dispatch, "client_for_socket", mock_client_factory(handler))

    job = Job(type="agent.run_task", target_pillar="agent", status=JobStatus.RUNNING)
    db.insert_job(conn, job)

    result = dispatch.stop_job(conn, job, socket_lookup=lambda pillar: tmp_path / "agent.sock")

    assert result == {"stopped": True, "reason": None}
    reloaded = db.get_job(conn, job.id)
    assert reloaded.status == JobStatus.STOPPED
    assert reloaded.error == "stopped by user"


def test_stop_job_probes_and_reports_not_stoppable_when_the_pillar_refuses(conn, monkeypatch, tmp_path):
    def handler(request):
        return httpx.Response(200, json={"stopped": False, "reason": "past the point of no return"})

    monkeypatch.setattr(dispatch, "client_for_socket", mock_client_factory(handler))

    job = Job(type="agent.run_task", target_pillar="agent", status=JobStatus.RUNNING)
    db.insert_job(conn, job)

    result = dispatch.stop_job(conn, job, socket_lookup=lambda pillar: tmp_path / "agent.sock")

    assert result == {"stopped": False, "reason": "past the point of no return"}
    assert db.get_job(conn, job.id).status == JobStatus.RUNNING  # unchanged


def test_stop_job_treats_a_404_as_not_stoppable_for_an_undeclared_type(conn, monkeypatch, tmp_path):
    def handler(request):
        return httpx.Response(404)

    monkeypatch.setattr(dispatch, "client_for_socket", mock_client_factory(handler))

    job = Job(type="deployment.deploy", target_pillar="deployment", status=JobStatus.RUNNING)
    db.insert_job(conn, job)

    result = dispatch.stop_job(conn, job, socket_lookup=lambda pillar: tmp_path / "d.sock")

    assert result == {"stopped": False, "reason": "not stoppable"}
    assert db.get_job(conn, job.id).status == JobStatus.RUNNING
