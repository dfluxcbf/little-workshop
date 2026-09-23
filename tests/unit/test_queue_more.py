"""queue.py's is_ready() short-circuit for a non-queued job, the one
branch test_queue.py didn't reach yet -- is_ready is normally only ever
called on jobs already known to be queued (via ready_jobs' own list
comprehension), so this exercises the guard directly."""

import pytest

from orchestration import db
from orchestration.models import Job, JobStatus
from orchestration.queue import is_ready


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "orchestration.db")
    yield connection
    connection.close()


def test_is_ready_false_for_a_job_that_is_already_running(conn):
    job = Job(type="noop", target_pillar="reference")
    db.insert_job(conn, job)
    job.status = JobStatus.RUNNING
    db.update_job(conn, job)

    assert is_ready(conn, job) is False


def test_is_ready_false_for_a_succeeded_job(conn):
    job = Job(type="noop", target_pillar="reference")
    db.insert_job(conn, job)
    job.status = JobStatus.SUCCEEDED
    db.update_job(conn, job)

    assert is_ready(conn, job) is False
