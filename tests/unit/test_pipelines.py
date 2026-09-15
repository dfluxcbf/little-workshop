import pytest

from orchestration import db
from orchestration.models import Pipeline, PipelineNode
from orchestration.pipelines import trigger_pipeline


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "orchestration.db")
    yield connection
    connection.close()


def test_trigger_pipeline_creates_one_job_per_node(conn):
    pipeline = Pipeline(
        name="release",
        version=1,
        nodes=[
            PipelineNode(node_id="build", job_type="build.run", target_pillar="build"),
            PipelineNode(
                node_id="deploy",
                job_type="deployment.deploy",
                target_pillar="deployment",
                depends_on=["build"],
            ),
        ],
    )
    run = trigger_pipeline(conn, pipeline, project_id="little-meals", created_by="lws")

    assert set(run.job_ids) == {"build", "deploy"}
    build_job = db.get_job(conn, run.job_ids["build"])
    deploy_job = db.get_job(conn, run.job_ids["deploy"])
    assert build_job.project_id == "little-meals"
    assert deploy_job.depends_on == [build_job.id]


def test_trigger_pipeline_respects_per_node_project_override(conn):
    pipeline = Pipeline(
        name="cross-project",
        version=1,
        nodes=[
            PipelineNode(
                node_id="a", job_type="noop", target_pillar="reference", project_id="proj-a"
            ),
            PipelineNode(
                node_id="b", job_type="noop", target_pillar="reference", project_id="proj-b"
            ),
        ],
    )
    run = trigger_pipeline(conn, pipeline, project_id="default-project")
    job_a = db.get_job(conn, run.job_ids["a"])
    job_b = db.get_job(conn, run.job_ids["b"])
    assert job_a.project_id == "proj-a"
    assert job_b.project_id == "proj-b"


def test_trigger_pipeline_rejects_cycles(conn):
    pipeline = Pipeline(
        name="broken",
        version=1,
        nodes=[
            PipelineNode(node_id="a", job_type="noop", target_pillar="reference", depends_on=["b"]),
            PipelineNode(node_id="b", job_type="noop", target_pillar="reference", depends_on=["a"]),
        ],
    )
    with pytest.raises(ValueError):
        trigger_pipeline(conn, pipeline)


def test_trigger_pipeline_rejects_unknown_dependency(conn):
    pipeline = Pipeline(
        name="broken",
        version=1,
        nodes=[
            PipelineNode(
                node_id="a", job_type="noop", target_pillar="reference", depends_on=["ghost"]
            )
        ],
    )
    with pytest.raises(ValueError):
        trigger_pipeline(conn, pipeline)
