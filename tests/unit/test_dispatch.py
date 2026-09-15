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
