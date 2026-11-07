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


def test_queue_key_capacity_of_2_allows_a_second_concurrent_job(conn):
    db.set_queue_capacity(conn, "little-meals", 2)

    running = Job(type="deploy", target_pillar="deployment", queue_key="little-meals")
    db.insert_job(conn, running)
    running.status = JobStatus.RUNNING
    db.update_job(conn, running)

    queued = Job(type="deploy", target_pillar="deployment", queue_key="little-meals")
    db.insert_job(conn, queued)
    assert is_ready(conn, queued) is True


def test_queue_key_capacity_of_2_still_blocks_a_third(conn):
    db.set_queue_capacity(conn, "little-meals", 2)

    for _ in range(2):
        running = Job(type="deploy", target_pillar="deployment", queue_key="little-meals")
        db.insert_job(conn, running)
        running.status = JobStatus.RUNNING
        db.update_job(conn, running)

    queued = Job(type="deploy", target_pillar="deployment", queue_key="little-meals")
    db.insert_job(conn, queued)
    assert is_ready(conn, queued) is False


def test_queue_capacity_defaults_to_1_when_never_set(conn):
    assert db.get_queue_capacity(conn, "never-configured") == 1


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


def _redeploy_changed(conn):
    job = Job(type="deployment.redeploy_changed_pillars", target_pillar="deployment", project_id="little-workshop")
    db.insert_job(conn, job)
    return job


def _orchestration_redeploy(conn):
    job = Job(type="deployment.redeploy_pillar", target_pillar="deployment", project_id="orchestration",
              params={"pillar": "orchestration", "commit": "abc"})
    db.insert_job(conn, job)
    return job


# --- REQ-000000476: The automatic pillar redeploy waits until the workshop is idle ---

def test_the_changed_pillars_redeploy_runs_when_nothing_else_is_going_on(conn):
    assert is_ready(conn, _redeploy_changed(conn)) is True


@pytest.mark.parametrize("status", [JobStatus.QUEUED, JobStatus.RUNNING])
def test_the_changed_pillars_redeploy_waits_for_any_project_job(conn, status):
    db.insert_job(conn, Job(type="agent.run_task", target_pillar="agent", project_id="little-meals", status=status))
    job = _redeploy_changed(conn)
    assert is_ready(conn, job) is False
    assert job not in ready_jobs(conn)


def test_other_redeploys_and_jobs_behind_a_held_job_do_not_keep_it_waiting(conn):
    db.insert_job(conn, Job(type="deployment.redeploy_pillar", target_pillar="deployment", project_id="policy", status=JobStatus.RUNNING))
    held = Job(type="version.feature_finish", target_pillar="version", project_id="little-meals", status=JobStatus.PENDING_CONFIRMATION)
    db.insert_job(conn, held)
    db.insert_job(conn, Job(type="noop", target_pillar="reference", project_id="little-meals", depends_on=[held.id]))
    assert is_ready(conn, _redeploy_changed(conn)) is True


# --- REQ-000000477: Orchestration is redeployed last, never mid-dispatch ---

def test_orchestration_redeploy_waits_while_another_dispatch_is_in_flight(conn):
    finish = Job(type="version.feature_finish", target_pillar="version", project_id="little-meals", status=JobStatus.RUNNING)
    db.insert_job(conn, finish)  # claimed, its dispatch call not answered yet
    job = _orchestration_redeploy(conn)
    assert is_ready(conn, job) is False
    db.insert_job_run(conn, finish.id, outcome="running", detail="accepted", started_at=finish.created_at, finished_at=None)
    assert is_ready(conn, job) is True  # accepted: it survives Orchestration's restart


def test_another_pillars_redeploy_does_not_wait_for_dispatches(conn):
    db.insert_job(conn, Job(type="version.feature_finish", target_pillar="version", project_id="little-meals", status=JobStatus.RUNNING))
    job = Job(type="deployment.redeploy_pillar", target_pillar="deployment", project_id="policy", params={"pillar": "policy"})
    db.insert_job(conn, job)
    assert is_ready(conn, job) is True
