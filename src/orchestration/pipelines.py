"""Pipeline expansion: a named, versioned DAG of Job templates becomes
concrete, dependency-linked Jobs at trigger time — docs/design.md's
Pipeline/PipelineRun concept, and docs/milestones.md M1's note that this
needs no execution-layer primitive beyond what `depends_on`-linked Jobs
already provide.
"""

from __future__ import annotations

import sqlite3

from orchestration.db import insert_job, insert_pipeline_run
from orchestration.models import Job, Pipeline, PipelineNode, PipelineRun


def _topological_order(nodes: list[PipelineNode]) -> list[PipelineNode]:
    by_id = {node.node_id: node for node in nodes}
    if len(by_id) != len(nodes):
        raise ValueError("pipeline has duplicate node_ids")

    state: dict[str, str] = {}
    order: list[PipelineNode] = []

    def visit(node_id: str) -> None:
        if state.get(node_id) == "done":
            return
        if state.get(node_id) == "visiting":
            raise ValueError(f"pipeline has a dependency cycle involving {node_id!r}")
        if node_id not in by_id:
            raise ValueError(f"unknown node_id {node_id!r}")
        state[node_id] = "visiting"
        for dep_id in by_id[node_id].depends_on:
            visit(dep_id)
        state[node_id] = "done"
        order.append(by_id[node_id])

    for node in nodes:
        visit(node.node_id)
    return order


def trigger_pipeline(
    conn: sqlite3.Connection,
    pipeline: Pipeline,
    *,
    project_id: str | None = None,
    created_by: str = "unknown",
) -> PipelineRun:
    node_to_job_id: dict[str, str] = {}

    for node in _topological_order(pipeline.nodes):
        job = Job(
            type=node.job_type,
            target_pillar=node.target_pillar,
            project_id=node.project_id or project_id,
            params=node.params,
            queue_key=node.queue_key,
            depends_on=[node_to_job_id[dep_id] for dep_id in node.depends_on],
            created_by=created_by,
        )
        insert_job(conn, job)
        node_to_job_id[node.node_id] = job.id

    run = PipelineRun(
        pipeline_name=pipeline.name,
        pipeline_version=pipeline.version,
        project_id=project_id,
        created_by=created_by,
        job_ids=node_to_job_id,
    )
    insert_pipeline_run(conn, run)
    return run
