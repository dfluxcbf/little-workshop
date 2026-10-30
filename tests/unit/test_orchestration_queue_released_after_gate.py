"""A policy-gated Job awaiting confirmation holds its queue_key. Once it
settles -- timed out, or stopped -- the Job queued behind it is dispatched
right away, with no manual pump. REQ-000000268."""

import pytest
from fastapi.testclient import TestClient

from orchestration import db
from orchestration.app import build_app
from orchestration.models import Job, JobStatus


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "orchestration.db")
    yield connection
    connection.close()


def _gated_and_waiting(conn):
    gated = Job(type="version.improvement_finish", target_pillar="version", project_id="p", queue_key="p",
                status=JobStatus.PENDING_CONFIRMATION)
    waiting = Job(type="version.improvement_finish", target_pillar="version", project_id="p", queue_key="p")
    db.insert_job(conn, gated)
    db.insert_job(conn, waiting)
    return gated, waiting


def test_a_confirm_timeout_dispatches_the_job_queued_behind_it(conn):
    client = TestClient(build_app(conn, socket_lookup=lambda pillar: None))
    gated, waiting = _gated_and_waiting(conn)

    assert client.post(f"/v1/jobs/{gated.id}/policy_confirm_timeout").json()["timed_out"] is True

    # Dispatched for real: with no socket for "version" it fails as "pillar disabled".
    after = db.get_job(conn, waiting.id)
    assert after.status == JobStatus.FAILED and after.error == "pillar disabled"


def test_stopping_a_job_awaiting_confirmation_declines_it_and_frees_the_queue(conn):
    client = TestClient(build_app(conn, socket_lookup=lambda pillar: None))
    gated, waiting = _gated_and_waiting(conn)

    response = client.post(f"/v1/jobs/{gated.id}/stop").json()

    assert response == {"stopped": True, "reason": None}
    assert db.get_job(conn, gated.id).status == JobStatus.CANCELLED
    assert db.get_job(conn, waiting.id).status == JobStatus.FAILED  # dispatched: "pillar disabled"


def test_stopping_a_job_awaiting_confirmation_fails_its_dependents(conn):
    client = TestClient(build_app(conn, socket_lookup=lambda pillar: None))
    gated = Job(type="deployment.deploy", target_pillar="deployment", project_id="p", status=JobStatus.PENDING_CONFIRMATION)
    db.insert_job(conn, gated)
    child = Job(type="deployment.deploy", target_pillar="deployment", project_id="p", depends_on=[gated.id])
    db.insert_job(conn, child)

    client.post(f"/v1/jobs/{gated.id}/stop")

    after = db.get_job(conn, child.id)
    assert after.status == JobStatus.FAILED
    assert after.error == f"dependency {gated.id} cancelled"
