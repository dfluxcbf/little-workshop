"""Force-stopping a Job: the user's way out when its pillar cannot (or will not) stop it, and why a
completion that arrives after the Job settled changes nothing."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from orchestration import db, dispatch
from orchestration.app import build_app
from orchestration.models import Job, JobStatus
from test_dispatch import mock_client_factory


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "orchestration.db")
    yield connection
    connection.close()


def _refusing_pillar(monkeypatch, seen: list):
    def handler(request):
        seen.append(json.loads(request.content or b"{}"))
        return httpx.Response(200, json={"stopped": False, "reason": "not stoppable"})

    monkeypatch.setattr(dispatch, "client_for_socket", mock_client_factory(handler))


# --- REQ-000000366: A job can be force-stopped ---


def test_the_pillar_is_told_the_jobs_type_and_whether_the_stop_is_forced(conn, monkeypatch, tmp_path):
    seen = []
    _refusing_pillar(monkeypatch, seen)
    job = Job(type="agent.run_task", target_pillar="agent", status=JobStatus.RUNNING)
    db.insert_job(conn, job)
    assert dispatch.stop_job(conn, job, socket_lookup=lambda p: tmp_path / "agent.sock") == {"stopped": False, "reason": "not stoppable"}
    assert seen == [{"type": "agent.run_task", "force": False}]
    assert db.get_job(conn, job.id).status == JobStatus.RUNNING


def test_a_force_stop_settles_a_job_its_pillar_will_not_stop_and_fails_what_waits_on_it(conn, monkeypatch, tmp_path):
    seen = []
    _refusing_pillar(monkeypatch, seen)
    job = Job(type="policy.check_project", target_pillar="policy", status=JobStatus.RUNNING, queue_key="little-workshop")
    waiting = Job(type="version.improvement_finish", target_pillar="version", depends_on=[job.id])
    db.insert_job(conn, job)
    db.insert_job(conn, waiting)

    result = dispatch.stop_job(conn, job, socket_lookup=lambda p: tmp_path / "policy.sock", force=True)

    assert result["stopped"] is True and result["forced"] is True
    assert seen == [{"type": "policy.check_project", "force": True}]
    stopped = db.get_job(conn, job.id)
    assert stopped.status == JobStatus.STOPPED
    assert stopped.error == "force-stopped by user (the pillar said: not stoppable)"
    after = db.get_job(conn, waiting.id)
    assert after.status == JobStatus.FAILED and job.id in after.error


def test_a_force_stop_needs_no_reachable_pillar(conn):
    job = Job(type="agent.run_task", target_pillar="agent", status=JobStatus.RUNNING)
    db.insert_job(conn, job)
    result = dispatch.stop_job(conn, job, socket_lookup=lambda p: None, force=True)
    assert result["stopped"] is True
    assert db.get_job(conn, job.id).error == "force-stopped by user (the pillar said: target pillar unreachable)"


def test_a_declared_unstoppable_job_is_force_stopped_without_asking_its_pillar(conn, monkeypatch):
    def handler(request):
        raise AssertionError("a declared-unstoppable type has no stop to ask for")

    monkeypatch.setattr(dispatch, "client_for_socket", mock_client_factory(handler))
    job = Job(type="version.feature_start", target_pillar="version", status=JobStatus.RUNNING)
    db.insert_job(conn, job)
    assert dispatch.stop_job(conn, job, socket_lookup=lambda p: Path("/x"))["stopped"] is False
    result = dispatch.stop_job(conn, job, socket_lookup=lambda p: Path("/x"), force=True)
    assert result["stopped"] is True
    assert db.get_job(conn, job.id).error == "force-stopped by user (the pillar said: 'version.feature_start' is declared unstoppable)"


def test_a_job_that_waits_on_a_stopped_one_fails_instead_of_waiting_forever(conn):
    first = Job(type="a", target_pillar="x", status=JobStatus.STOPPED)
    second = Job(type="b", target_pillar="x", depends_on=[first.id])
    db.insert_job(conn, first)
    db.insert_job(conn, second)
    from orchestration.queue import cascade_dependency_failures

    cascade_dependency_failures(conn)
    assert db.get_job(conn, second.id).status == JobStatus.FAILED


def test_force_stop_through_the_api_releases_the_queue_for_the_next_job(conn):
    client = TestClient(build_app(conn, socket_lookup=lambda pillar: None))
    stuck = Job(type="agent.run_task", target_pillar="agent", status=JobStatus.RUNNING, queue_key="little-workshop")
    behind = Job(type="version.improvement_start", target_pillar="version", queue_key="little-workshop")
    db.insert_job(conn, stuck)
    db.insert_job(conn, behind)

    refused = client.post(f"/v1/jobs/{stuck.id}/stop").json()
    assert refused["stopped"] is False
    assert db.get_job(conn, behind.id).status == JobStatus.QUEUED  # still held behind the stuck job

    forced = client.post(f"/v1/jobs/{stuck.id}/stop", json={"force": True}).json()
    assert forced["stopped"] is True and forced["forced"] is True
    assert db.get_job(conn, stuck.id).status == JobStatus.STOPPED
    # Released: the next job was dispatched (and, with no pillar reachable here, failed as disabled).
    assert db.get_job(conn, behind.id).status == JobStatus.FAILED


def test_a_completion_reported_after_the_job_settled_is_ignored(conn):
    client = TestClient(build_app(conn, socket_lookup=lambda pillar: None))
    job = Job(type="agent.run_task", target_pillar="agent", status=JobStatus.STOPPED, error="force-stopped by user")
    db.insert_job(conn, job)
    answer = client.post(f"/v1/jobs/{job.id}/complete", json={"succeeded": True, "result": {"run_id": "r1"}})
    assert answer.status_code == 200 and answer.json()["ignored"] is True
    kept = db.get_job(conn, job.id)
    assert kept.status == JobStatus.STOPPED and kept.result is None and kept.error == "force-stopped by user"


def test_a_running_job_still_completes_normally(conn):
    client = TestClient(build_app(conn, socket_lookup=lambda pillar: None))
    job = Job(type="agent.run_task", target_pillar="agent", status=JobStatus.RUNNING)
    db.insert_job(conn, job)
    client.post(f"/v1/jobs/{job.id}/complete", json={"succeeded": True, "result": {"run_id": "r1"}})
    assert db.get_job(conn, job.id).status == JobStatus.SUCCEEDED
