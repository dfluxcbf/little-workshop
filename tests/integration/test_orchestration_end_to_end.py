"""Testing-fortification pass: Orchestration's Job dispatch/queue
machinery beyond M1's original proof — the `queue_key` mutex, `depends_on`
resolution and cascade-failure, status filtering, an unknown-pillar
dispatch, and the async-Job completion callback — using the reference
pillar's `slow` Job type (built for exactly this) to hold a Job open on
demand rather than racing real pillar timing.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from cli import client as cli_client
from common import systemd
from common.shell import DEFAULT_RUNNER
from core.health import probe_health
from orchestration import db as orchestration_db
from orchestration.dispatch import complete_job
from orchestration.models import JobStatus

from helpers import INTEGRATION, UvicornThread, requires_systemd_user


def _start_reference(workshop_env):
    reference_command = [sys.executable, "-m", "reference_pillar", str(workshop_env.paths.socket_path("reference"))]
    systemd.start("reference", reference_command, environment={"PYTHONPATH": str(INTEGRATION)}, runner=DEFAULT_RUNNER)
    probe = probe_health(workshop_env.paths.socket_path("reference"), timeout=5.0, max_retries=10)
    assert probe.ok, probe.error


def _orchestration_server(workshop_env, known_pillars=("reference",)):
    def socket_lookup(pillar: str):
        return workshop_env.paths.socket_path(pillar) if pillar in known_pillars else None

    conn = orchestration_db.connect(workshop_env.paths.pillar_db_path("orchestration"))
    from orchestration.app import build_app

    server = UvicornThread(build_app(conn, socket_lookup), workshop_env.paths.socket_path("orchestration"))
    server.start()
    return server, conn


@requires_systemd_user
def test_queue_key_mutex_serializes_two_jobs_on_the_same_project(workshop_env):
    _start_reference(workshop_env)
    try:
        server, conn = _orchestration_server(workshop_env)
        try:
            first = cli_client.create_job(
                workshop_env.paths, type="slow", target_pillar="reference", project_id="p",
                params={}, queue_key="p", depends_on=[], created_by="t",
            )
            assert first["status"] == "running", first  # "accepted" -> stays RUNNING

            second = cli_client.create_job(
                workshop_env.paths, type="noop", target_pillar="reference", project_id="p",
                params={}, queue_key="p", depends_on=[], created_by="t",
            )
            # The mutex holds: a second job under the same queue_key can't
            # even be dispatched while the first is still RUNNING.
            assert second["status"] == "queued", second

            complete_job(conn, orchestration_db.get_job(conn, first["id"]), succeeded=True, result={}, error=None)

            # Now that the first has settled, creating a third job (which
            # triggers Orchestration's own dispatch pump) lets the second
            # one finally go through.
            cli_client.create_job(
                workshop_env.paths, type="noop", target_pillar="reference", project_id="other",
                params={}, queue_key=None, depends_on=[], created_by="t",
            )
            settled_second = cli_client.job_status(workshop_env.paths, second["id"])["job"]
            assert settled_second["status"] == "succeeded", settled_second
        finally:
            server.stop()
            conn.close()
    finally:
        systemd.stop("reference", runner=DEFAULT_RUNNER)


@requires_systemd_user
def test_job_with_depends_on_waits_for_its_dependency_to_succeed(workshop_env):
    _start_reference(workshop_env)
    try:
        server, conn = _orchestration_server(workshop_env)
        try:
            dependency = cli_client.create_job(
                workshop_env.paths, type="slow", target_pillar="reference", project_id="p",
                params={}, queue_key=None, depends_on=[], created_by="t",
            )
            assert dependency["status"] == "running", dependency

            dependent = cli_client.create_job(
                workshop_env.paths, type="noop", target_pillar="reference", project_id="p",
                params={}, queue_key=None, depends_on=[dependency["id"]], created_by="t",
            )
            assert dependent["status"] == "queued", dependent

            complete_job(conn, orchestration_db.get_job(conn, dependency["id"]), succeeded=True, result={}, error=None)
            cli_client.create_job(
                workshop_env.paths, type="noop", target_pillar="reference", project_id="other",
                params={}, queue_key=None, depends_on=[], created_by="t",
            )
            settled = cli_client.job_status(workshop_env.paths, dependent["id"])["job"]
            assert settled["status"] == "succeeded", settled
        finally:
            server.stop()
            conn.close()
    finally:
        systemd.stop("reference", runner=DEFAULT_RUNNER)


@requires_systemd_user
def test_job_whose_dependency_fails_also_fails_via_cascade(workshop_env):
    _start_reference(workshop_env)
    try:
        server, conn = _orchestration_server(workshop_env)
        try:
            dependency = cli_client.create_job(
                workshop_env.paths, type="always_fail", target_pillar="reference", project_id="p",
                params={}, queue_key=None, depends_on=[], created_by="t",
            )
            assert dependency["status"] == "failed", dependency

            dependent = cli_client.create_job(
                workshop_env.paths, type="noop", target_pillar="reference", project_id="p",
                params={}, queue_key=None, depends_on=[dependency["id"]], created_by="t",
            )
            # dispatch_ready_jobs runs cascade_dependency_failures before
            # dispatching, on every POST /v1/jobs call -- so this Job
            # should already have failed synchronously, in the very same
            # request that created it, never sitting queued forever.
            assert dependent["status"] == "failed", dependent
            assert dependency["id"] in dependent["error"]
        finally:
            server.stop()
            conn.close()
    finally:
        systemd.stop("reference", runner=DEFAULT_RUNNER)


@requires_systemd_user
def test_job_list_filters_by_status_after_several_real_jobs_settle(workshop_env):
    _start_reference(workshop_env)
    try:
        server, conn = _orchestration_server(workshop_env)
        try:
            cli_client.create_job(
                workshop_env.paths, type="noop", target_pillar="reference", project_id="p1",
                params={}, queue_key=None, depends_on=[], created_by="t",
            )
            cli_client.create_job(
                workshop_env.paths, type="always_fail", target_pillar="reference", project_id="p2",
                params={}, queue_key=None, depends_on=[], created_by="t",
            )
            cli_client.create_job(
                workshop_env.paths, type="slow", target_pillar="reference", project_id="p3",
                params={}, queue_key=None, depends_on=[], created_by="t",
            )

            succeeded = cli_client.list_jobs(workshop_env.paths, status="succeeded")
            failed = cli_client.list_jobs(workshop_env.paths, status="failed")
            running = cli_client.list_jobs(workshop_env.paths, status="running")
            assert len(succeeded["jobs"]) == 1
            assert len(failed["jobs"]) == 1
            assert len(running["jobs"]) == 1
        finally:
            server.stop()
            conn.close()
    finally:
        systemd.stop("reference", runner=DEFAULT_RUNNER)


@requires_systemd_user
def test_dispatch_to_a_pillar_core_does_not_know_about_fails_cleanly(workshop_env):
    _start_reference(workshop_env)
    try:
        # socket_lookup only knows "reference" -- "phantom-pillar" is not
        # in it at all, the same shape a disabled/never-registered pillar
        # would have from Orchestration's point of view.
        server, conn = _orchestration_server(workshop_env, known_pillars=("reference",))
        try:
            result = cli_client.create_job(
                workshop_env.paths, type="noop", target_pillar="phantom-pillar", project_id="p",
                params={}, queue_key=None, depends_on=[], created_by="t",
            )
            assert result["status"] == "failed", result
            assert result["error"] == "pillar disabled"
        finally:
            server.stop()
            conn.close()
    finally:
        systemd.stop("reference", runner=DEFAULT_RUNNER)


@requires_systemd_user
def test_completion_callback_settles_an_accepted_job_to_succeeded(workshop_env):
    _start_reference(workshop_env)
    try:
        server, conn = _orchestration_server(workshop_env)
        try:
            accepted = cli_client.create_job(
                workshop_env.paths, type="slow", target_pillar="reference", project_id="p",
                params={}, queue_key=None, depends_on=[], created_by="t",
            )
            assert accepted["status"] == "running", accepted

            from common.uds_client import client_for_socket

            with client_for_socket(workshop_env.paths.socket_path("orchestration"), timeout=5.0) as orch_client:
                response = orch_client.post(
                    f"/v1/jobs/{accepted['id']}/complete",
                    json={"succeeded": True, "result": {"did": "the work"}, "error": None},
                )
            assert response.status_code == 200

            settled = cli_client.job_status(workshop_env.paths, accepted["id"])["job"]
            assert settled["status"] == "succeeded"
            assert settled["result"] == {"did": "the work"}
        finally:
            server.stop()
            conn.close()
    finally:
        systemd.stop("reference", runner=DEFAULT_RUNNER)
