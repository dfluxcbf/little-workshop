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
