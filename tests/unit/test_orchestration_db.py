import pytest

from orchestration import db
from orchestration.models import Job, JobStatus, Pipeline, PipelineNode


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "orchestration.db")
    yield connection
    connection.close()


def test_insert_and_get_job_round_trips(conn):
    job = Job(type="noop", target_pillar="reference", project_id="little-meals", params={"x": 1})
    db.insert_job(conn, job)

    fetched = db.get_job(conn, job.id)
    assert fetched.type == "noop"
    assert fetched.target_pillar == "reference"
    assert fetched.params == {"x": 1}
    assert fetched.status == JobStatus.QUEUED


def test_update_job_persists_status_change(conn):
    job = Job(type="noop", target_pillar="reference")
    db.insert_job(conn, job)

    job.status = JobStatus.SUCCEEDED
    job.result = {"ok": True}
    db.update_job(conn, job)

    fetched = db.get_job(conn, job.id)
    assert fetched.status == JobStatus.SUCCEEDED
    assert fetched.result == {"ok": True}


def test_list_jobs_filters_by_status(conn):
    a = Job(type="noop", target_pillar="reference")
    b = Job(type="noop", target_pillar="reference")
    db.insert_job(conn, a)
    db.insert_job(conn, b)
    b.status = JobStatus.SUCCEEDED
    db.update_job(conn, b)

    queued = db.list_jobs(conn, status=JobStatus.QUEUED)
    assert [j.id for j in queued] == [a.id]


def test_job_runs_recorded_and_listed(conn):
    from datetime import datetime, timezone

    job = Job(type="noop", target_pillar="reference")
    db.insert_job(conn, job)
    now = datetime.now(timezone.utc)
    db.insert_job_run(conn, job.id, "succeeded", "ok", now, now)

    runs = db.list_job_runs(conn, job.id)
    assert len(runs) == 1
    assert runs[0]["outcome"] == "succeeded"


def test_pipeline_round_trips_and_versions(conn):
    pipeline = Pipeline(
        name="release",
        version=1,
        nodes=[PipelineNode(node_id="build", job_type="build.run", target_pillar="build")],
    )
    db.save_pipeline(conn, pipeline)

    fetched = db.get_pipeline(conn, "release")
    assert fetched.version == 1
    assert fetched.nodes[0].job_type == "build.run"

    pipeline_v2 = Pipeline(
        name="release",
        version=2,
        nodes=[PipelineNode(node_id="build", job_type="build.run", target_pillar="build")],
    )
    db.save_pipeline(conn, pipeline_v2)
    latest = db.get_pipeline(conn, "release")
    assert latest.version == 2
    v1 = db.get_pipeline(conn, "release", version=1)
    assert v1.version == 1


# -- cleanup of old, finished jobs ----------------------------------------------------


from datetime import datetime, timedelta, timezone  # noqa: E402

from orchestration.models import PipelineRun, ScheduledTask  # noqa: E402

NOW = datetime(2026, 9, 21, 12, 0, 0, tzinfo=timezone.utc)


def _aged(conn, days, *, status=JobStatus.SUCCEEDED, **fields):
    when = NOW - timedelta(days=days)
    job = Job(type="noop", target_pillar="reference", status=status, created_at=when, updated_at=when, **fields)
    db.insert_job(conn, job)
    return job


def _ids(conn):
    return {j.id for j in db.list_jobs(conn)}


def test_prune_deletes_only_finished_jobs_older_than_the_cutoff(conn):
    old_ok = _aged(conn, 10)
    old_failed = _aged(conn, 10, status=JobStatus.FAILED)
    old_cancelled = _aged(conn, 10, status=JobStatus.CANCELLED)
    old_stopped = _aged(conn, 10, status=JobStatus.STOPPED)
    recent = _aged(conn, 1)

    result = db.prune_finished_jobs(conn, NOW - timedelta(days=7))
    assert result == {"deleted": 4, "pipeline_runs_deleted": 0, "kept_for_safety": 0}
    assert _ids(conn) == {recent.id}
    assert not {old_ok.id, old_failed.id, old_cancelled.id, old_stopped.id} & _ids(conn)


@pytest.mark.parametrize("status", [JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.PENDING_CONFIRMATION])
def test_prune_never_touches_unfinished_jobs_however_old(conn, status):
    stale = _aged(conn, 400, status=status)
    assert db.prune_finished_jobs(conn, NOW)["deleted"] == 0
    assert _ids(conn) == {stale.id}


def test_prune_removes_the_run_records_of_deleted_jobs(conn):
    job = _aged(conn, 30)
    db.insert_job_run(conn, job.id, outcome="succeeded", detail=None, started_at=job.created_at, finished_at=job.updated_at)
    survivor = _aged(conn, 1)
    db.insert_job_run(conn, survivor.id, outcome="succeeded", detail=None, started_at=survivor.created_at, finished_at=survivor.updated_at)
    db.prune_finished_jobs(conn, NOW - timedelta(days=7))
    assert db.list_job_runs(conn, job.id) == [] and len(db.list_job_runs(conn, survivor.id)) == 1


def test_a_finished_job_an_unfinished_job_still_depends_on_is_kept(conn):
    dependency = _aged(conn, 30)
    waiting = _aged(conn, 30, status=JobStatus.QUEUED, depends_on=[dependency.id])
    free = _aged(conn, 30)
    result = db.prune_finished_jobs(conn, NOW - timedelta(days=7))
    assert result["deleted"] == 1 and result["kept_for_safety"] == 1
    assert _ids(conn) == {dependency.id, waiting.id}
    assert free.id not in _ids(conn)


def test_a_finished_jobs_dependencies_do_not_need_protecting(conn):
    dependency = _aged(conn, 30)
    dependent = _aged(conn, 30, depends_on=[dependency.id])
    assert db.prune_finished_jobs(conn, NOW - timedelta(days=7))["deleted"] == 2
    assert _ids(conn) == set()


def test_a_scheduled_tasks_last_job_is_kept(conn):
    from orchestration.models import JobTemplate, Schedule, ScheduleKind

    last = _aged(conn, 30)
    other = _aged(conn, 30)
    task = ScheduledTask(
        name="nightly", job_template=JobTemplate(type="noop", target_pillar="reference"),
        schedule=Schedule(kind=ScheduleKind.RECURRING, days_of_week=[0], time_of_day="09:00"), last_job_id=last.id,
    )
    db.insert_scheduled_task(conn, task)
    db.prune_finished_jobs(conn, NOW - timedelta(days=7))
    assert _ids(conn) == {last.id} and other.id not in _ids(conn)


def test_a_pipeline_run_is_removed_whole_when_all_its_jobs_are_old_and_finished(conn):
    a, b = _aged(conn, 30), _aged(conn, 30)
    db.insert_pipeline_run(conn, PipelineRun(pipeline_name="p", pipeline_version=1, job_ids={"n1": a.id, "n2": b.id}, created_at=NOW - timedelta(days=30)))
    result = db.prune_finished_jobs(conn, NOW - timedelta(days=7))
    assert result["deleted"] == 2 and result["pipeline_runs_deleted"] == 1
    assert conn.execute("SELECT COUNT(*) FROM pipeline_runs").fetchone()[0] == 0


def test_a_pipeline_run_with_a_recent_or_unfinished_job_is_kept_whole(conn):
    old, recent = _aged(conn, 30), _aged(conn, 1)
    db.insert_pipeline_run(conn, PipelineRun(pipeline_name="p", pipeline_version=1, job_ids={"n1": old.id, "n2": recent.id}, created_at=NOW - timedelta(days=30)))
    running_old = _aged(conn, 30)
    running = _aged(conn, 30, status=JobStatus.RUNNING)
    db.insert_pipeline_run(conn, PipelineRun(pipeline_name="q", pipeline_version=1, job_ids={"n1": running_old.id, "n2": running.id}, created_at=NOW - timedelta(days=30)))

    result = db.prune_finished_jobs(conn, NOW - timedelta(days=7))
    assert result == {"deleted": 0, "pipeline_runs_deleted": 0, "kept_for_safety": 2}
    assert len(_ids(conn)) == 4 and conn.execute("SELECT COUNT(*) FROM pipeline_runs").fetchone()[0] == 2


def test_protection_spreads_through_a_pipeline_run(conn):
    """A queued job waits on run A's job; that keeps all of run A, not just the one job."""
    a1, a2 = _aged(conn, 30), _aged(conn, 30)
    db.insert_pipeline_run(conn, PipelineRun(pipeline_name="p", pipeline_version=1, job_ids={"n1": a1.id, "n2": a2.id}, created_at=NOW - timedelta(days=30)))
    _aged(conn, 30, status=JobStatus.QUEUED, depends_on=[a1.id])
    db.prune_finished_jobs(conn, NOW - timedelta(days=7))
    assert {a1.id, a2.id} <= _ids(conn) and conn.execute("SELECT COUNT(*) FROM pipeline_runs").fetchone()[0] == 1


def test_an_emptied_old_pipeline_run_row_is_dropped(conn):
    db.insert_pipeline_run(conn, PipelineRun(pipeline_name="p", pipeline_version=1, job_ids={"n1": "already-gone"}, created_at=NOW - timedelta(days=30)))
    db.insert_pipeline_run(conn, PipelineRun(pipeline_name="q", pipeline_version=1, job_ids={"n1": "gone-too"}, created_at=NOW - timedelta(days=1)))
    assert db.prune_finished_jobs(conn, NOW - timedelta(days=7))["pipeline_runs_deleted"] == 1


def test_count_prunable_matches_what_prune_would_delete_without_deleting(conn):
    _aged(conn, 30); _aged(conn, 30, status=JobStatus.FAILED); _aged(conn, 3); _aged(conn, 30, status=JobStatus.RUNNING)
    before = NOW - timedelta(days=7)
    assert db.count_prunable_jobs(conn, before) == 2 and len(_ids(conn)) == 4
    assert db.prune_finished_jobs(conn, before)["deleted"] == 2


def test_prune_on_an_empty_database_is_a_noop(conn):
    assert db.prune_finished_jobs(conn, NOW) == {"deleted": 0, "pipeline_runs_deleted": 0, "kept_for_safety": 0}
