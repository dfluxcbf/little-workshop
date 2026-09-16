"""pipelines.py's duplicate-node_id guard, the one branch
test_pipelines.py didn't reach yet."""

import pytest

from orchestration import db
from orchestration.models import Pipeline, PipelineNode
from orchestration.pipelines import trigger_pipeline


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "orchestration.db")
    yield connection
    connection.close()


def test_trigger_pipeline_rejects_duplicate_node_ids(conn):
    pipeline = Pipeline(
        name="broken",
        version=1,
        nodes=[
            PipelineNode(node_id="a", job_type="x", target_pillar="reference"),
            PipelineNode(node_id="a", job_type="y", target_pillar="reference"),
        ],
    )
    with pytest.raises(ValueError, match="duplicate node_ids"):
        trigger_pipeline(conn, pipeline)
