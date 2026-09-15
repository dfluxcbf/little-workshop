import pytest

from orchestration import db
from orchestration.models import Job, JobStatus
from orchestration.queue import cascade_dependency_failures, is_ready, ready_jobs


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "orchestration.db")
    yield connection
    connection.close()


def test_job_with_no_dependencies_is_ready(conn):
    job = Job(type="noop", target_pillar="reference")
    db.insert_job(conn, job)
    assert is_ready(conn, job) is True


def test_job_waiting_on_unfinished_dependency_is_not_ready(conn):
    dep = Job(type="noop", target_pillar="reference")
    db.insert_job(conn, dep)
    job = Job(type="noop", target_pillar="reference", depends_on=[dep.id])
    db.insert_job(conn, job)
    assert is_ready(conn, job) is False


def test_job_becomes_ready_once_dependency_succeeds(conn):
    dep = Job(type="noop", target_pillar="reference")
    db.insert_job(conn, dep)
    job = Job(type="noop", target_pillar="reference", depends_on=[dep.id])
    db.insert_job(conn, job)

    dep.status = JobStatus.SUCCEEDED
    db.update_job(conn, dep)
    assert is_ready(conn, job) is True


def test_queue_key_mutex_blocks_a_second_job_while_one_runs(conn):
    running = Job(type="deploy", target_pillar="deployment", queue_key="little-meals")
    db.insert_job(conn, running)
    running.status = JobStatus.RUNNING
    db.update_job(conn, running)

    queued = Job(type="deploy", target_pillar="deployment", queue_key="little-meals")
    db.insert_job(conn, queued)
    assert is_ready(conn, queued) is False


def test_queue_key_mutex_frees_once_holder_completes(conn):
    finished = Job(type="deploy", target_pillar="deployment", queue_key="little-meals")
    db.insert_job(conn, finished)
    finished.status = JobStatus.SUCCEEDED
    db.update_job(conn, finished)

    queued = Job(type="deploy", target_pillar="deployment", queue_key="little-meals")
    db.insert_job(conn, queued)
    assert is_ready(conn, queued) is True


def test_ready_jobs_returns_only_the_ready_ones(conn):
    ready = Job(type="noop", target_pillar="reference")
    blocked_dep = Job(type="noop", target_pillar="reference")
    db.insert_job(conn, ready)
    db.insert_job(conn, blocked_dep)
    blocked = Job(type="noop", target_pillar="reference", depends_on=[blocked_dep.id])
    db.insert_job(conn, blocked)

    result_ids = {j.id for j in ready_jobs(conn)}
    assert result_ids == {ready.id, blocked_dep.id}


def test_cascade_failure_propagates_to_dependents(conn):
    dep = Job(type="noop", target_pillar="reference")
    db.insert_job(conn, dep)
    child = Job(type="noop", target_pillar="reference", depends_on=[dep.id])
    db.insert_job(conn, child)
    grandchild = Job(type="noop", target_pillar="reference", depends_on=[child.id])
    db.insert_job(conn, grandchild)

    dep.status = JobStatus.FAILED
    db.update_job(conn, dep)

    changed = cascade_dependency_failures(conn)
    assert {j.id for j in changed} == {child.id, grandchild.id}
    assert db.get_job(conn, child.id).status == JobStatus.FAILED
    assert db.get_job(conn, grandchild.id).status == JobStatus.FAILED
