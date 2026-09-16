"""Orchestration's own machinery, beyond M1's original single-job proof:
the `queue_key` mutex actually serializing two jobs for real, a
`depends_on` chain only dispatching once its dependency succeeds (and
cascading a failure through a multi-level chain), pipeline expansion,
job-list status filtering, and dispatch to a disabled/unknown pillar
failing cleanly rather than hanging. All against a real Orchestration
app on a real Unix socket, dispatching to the real `reference_pillar`
test app (no systemd needed for either — both run in-process, matching
docs/milestones.md M1's own reference-pillar design intent).
"""

from __future__ import annotations

import pytest

from cli import client as cli_client
from helpers import UvicornThread
from orchestration.app import build_app as build_orchestration_app
from orchestration import db as orchestration_db
from orchestration.models import JobStatus
from reference_pillar.app import build_app as build_reference_app


@pytest.fixture
def orch_env(workshop_env):
    paths = workshop_env.paths
    reference_server = UvicornThread(build_reference_app(), paths.socket_path("reference"))
    reference_server.start()

    def socket_lookup(pillar: str):
        return paths.socket_path("reference") if pillar == "reference" else None

    conn = orchestration_db.connect(paths.pillar_db_path("orchestration"))
    orch_server = UvicornThread(build_orchestration_app(conn, socket_lookup), paths.socket_path("orchestration"))
    orch_server.start()
    try:
        yield workshop_env
    finally:
        orch_server.stop()
        reference_server.stop()
        conn.close()


def _create(env, **kw):
    kw.setdefault("target_pillar", "reference")
    kw.setdefault("project_id", None)
    kw.setdefault("params", {})
    kw.setdefault("queue_key", None)
    kw.setdefault("depends_on", [])
    kw.setdefault("created_by", "integration-test")
    return cli_client.create_job(env.paths, **kw)


def test_queue_key_mutex_holds_a_second_job_queued_until_the_first_settles(orch_env):
    first = _create(orch_env, type="slow", queue_key="project-x")
    assert first["status"] == "running"

    second = _create(orch_env, type="noop", queue_key="project-x")
    assert second["status"] == "queued"

    # Settle the first job for real — the same completion callback M5/M6's
    # accepted-Job workers actually use.
    from common.uds_client import client_for_socket

    with client_for_socket(orch_env.paths.socket_path("orchestration"), timeout=5.0) as http:
        completed = http.post(f"/v1/jobs/{first['id']}/complete", json={"succeeded": True, "result": {}})
        assert completed.status_code == 200

    # Pump the queue by creating a third, unrelated job — dispatch_ready_jobs
    # runs on every create_job call.
    _create(orch_env, type="noop", queue_key="unrelated")

    settled = cli_client.job_status(orch_env.paths, second["id"])
    assert settled["job"]["status"] == "succeeded"


def test_two_different_queue_keys_never_block_each_other(orch_env):
    first = _create(orch_env, type="slow", queue_key="project-a")
    second = _create(orch_env, type="noop", queue_key="project-b")
    assert first["status"] == "running"
    assert second["status"] == "succeeded"


def test_jobs_with_no_queue_key_never_serialize_against_each_other(orch_env):
    first = _create(orch_env, type="slow", queue_key=None)
    second = _create(orch_env, type="noop", queue_key=None)
    assert first["status"] == "running"
    assert second["status"] == "succeeded"


def test_depends_on_holds_a_job_queued_until_its_dependency_succeeds(orch_env):
    dependency = _create(orch_env, type="slow")
    assert dependency["status"] == "running"

    dependent = _create(orch_env, type="noop", depends_on=[dependency["id"]])
    assert dependent["status"] == "queued"

    from common.uds_client import client_for_socket

    with client_for_socket(orch_env.paths.socket_path("orchestration"), timeout=5.0) as http:
        http.post(f"/v1/jobs/{dependency['id']}/complete", json={"succeeded": True, "result": {}})

    _create(orch_env, type="noop")  # pump

    settled = cli_client.job_status(orch_env.paths, dependent["id"])
    assert settled["job"]["status"] == "succeeded"


def test_a_failed_dependency_cascades_forward_to_every_job_that_needed_it(orch_env):
    dependency = _create(orch_env, type="slow")
    dependent = _create(orch_env, type="noop", depends_on=[dependency["id"]])
    grand_dependent = _create(orch_env, type="noop", depends_on=[dependent["id"]])
    assert dependent["status"] == "queued"
    assert grand_dependent["status"] == "queued"

    from common.uds_client import client_for_socket

    with client_for_socket(orch_env.paths.socket_path("orchestration"), timeout=5.0) as http:
        http.post(f"/v1/jobs/{dependency['id']}/complete", json={"succeeded": False, "error": "boom"})

    _create(orch_env, type="noop")  # pump the cascade

    dependent_settled = cli_client.job_status(orch_env.paths, dependent["id"])
    grand_settled = cli_client.job_status(orch_env.paths, grand_dependent["id"])
    assert dependent_settled["job"]["status"] == "failed"
    assert "failed" in dependent_settled["job"]["error"]
    assert grand_settled["job"]["status"] == "failed"


def test_dispatch_to_a_disabled_or_unknown_pillar_fails_cleanly_not_a_hang(orch_env):
    result = _create(orch_env, type="noop", target_pillar="nonexistent-pillar")
    assert result["status"] == "failed"
    assert result["error"] == "pillar disabled"


def test_job_list_filters_by_status(orch_env):
    _create(orch_env, type="noop")
    _create(orch_env, type="always_fail")
    running = _create(orch_env, type="slow")

    succeeded = cli_client.list_jobs(orch_env.paths, status="succeeded")
    failed = cli_client.list_jobs(orch_env.paths, status="failed")
    still_running = cli_client.list_jobs(orch_env.paths, status="running")

    assert len(succeeded["jobs"]) == 1
    assert len(failed["jobs"]) == 1
    assert [j["id"] for j in still_running["jobs"]] == [running["id"]]


def test_job_result_and_params_round_trip_through_a_real_dispatch(orch_env):
    result = _create(orch_env, type="noop", params={"greeting": "hi"}, project_id="little-meals")
    assert result["result"] == {"echoed_params": {"greeting": "hi"}}
    assert result["project_id"] == "little-meals"
