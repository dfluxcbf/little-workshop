"""Deployment & Servers scenarios beyond M4's original deploy/stop
proof: seed_confirmation establishing a baseline, a manual confirm
settling a confirm-gated deploy (never rolling back), a real rollback
triggered by calling the watchdog's own endpoint directly (never waiting
on a real systemd timer), deployment history accumulating real entries,
and a bad install failing the deploy cleanly without ever touching the
previously-running (healthy) process — docs/design.md's own stated
guarantee for `_deploy`.
"""

from __future__ import annotations

import sys

import pytest

from cli import client as cli_client
from common import systemd
from common.manifest import Manifest
from common.projectfiles import ProjectFilesClient
from common.shell import DEFAULT_RUNNER
from common.uds_client import client_for_socket
from helpers import UvicornThread, requires_lws_installed, requires_systemd_user, sweep_transient_units
from core.app import build_app as build_core_app
from core.health import probe_health
from core.registry import PillarStatus, RegistryEntry
from core.supervisor import Supervisor
from deployment.netutil import port_is_listening
from orchestration.app import build_app as build_orchestration_app
from orchestration import db as orchestration_db

PORT = 18766


@pytest.fixture
def deployment_env(workshop_env):
    env = workshop_env.env
    systemd.start("registry", [sys.executable, "-m", "registry"], environment=env, runner=DEFAULT_RUNNER)
    systemd.start("deployment", [sys.executable, "-m", "deployment"], environment=env, runner=DEFAULT_RUNNER)
    try:
        for pillar in ("registry", "deployment"):
            probe = probe_health(workshop_env.paths.socket_path(pillar), timeout=5.0, max_retries=10)
            assert probe.ok, f"{pillar} pillar never became healthy: {probe.error}"

        core_supervisor = Supervisor(paths=workshop_env.paths, manifest=Manifest())
        core_supervisor.registry.set(
            RegistryEntry(name="registry", socket_path=workshop_env.paths.socket_path("registry"), status=PillarStatus.HEALTHY)
        )
        core_server = UvicornThread(build_core_app(core_supervisor), workshop_env.paths.core_socket_path)
        core_server.start()

        def socket_lookup(pillar: str):
            return workshop_env.paths.socket_path(pillar) if pillar in ("registry", "deployment") else None

        conn = orchestration_db.connect(workshop_env.paths.pillar_db_path("orchestration"))
        orch = UvicornThread(build_orchestration_app(conn, socket_lookup), workshop_env.paths.socket_path("orchestration"))
        orch.start()
        try:
            yield workshop_env
        finally:
            orch.stop()
            core_server.stop()
            conn.close()
    finally:
        systemd.stop("registry", runner=DEFAULT_RUNNER)
        systemd.stop("deployment", runner=DEFAULT_RUNNER)
        systemd.stop("deploy-little-meals", runner=DEFAULT_RUNNER)
        sweep_transient_units("little-workshop-deploy-timeout-*")


def _bootstrap(env, tmp_path, name="little-meals"):
    dest = tmp_path / "workdir"
    dest.mkdir(exist_ok=True)
    result = cli_client.create_job(
        env.paths, type="project.bootstrap", target_pillar="registry", project_id=name,
        params={"name": name, "dest_dir": str(dest)}, queue_key=None, depends_on=[], created_by="integration-test",
    )
    assert result["status"] == "succeeded", result
    return dest / name


def _write_manifest(env, name, *, port=PORT, confirm_timeout_seconds=None, install="true"):
    extra = f"confirm_timeout_seconds = {confirm_timeout_seconds}\n" if confirm_timeout_seconds else ""
    manifest_toml = f"""
[server]
name = "{name}"
command = "python3 -m http.server {port}"
port = {port}
match = "http.server {port}"
install = ["{install}"]
ready_timeout_seconds = 15
stop_timeout_seconds = 15
{extra}
"""
    ProjectFilesClient(env.paths, caller_pillar="deployment").write(name, "manifest.deployment.toml", manifest_toml)


def _deploy(env, name="little-meals", branch="master"):
    return cli_client.create_job(
        env.paths, type="deployment.deploy", target_pillar="deployment", project_id=name,
        params={"branch": branch}, queue_key=name, depends_on=[], created_by="integration-test",
    )


@requires_systemd_user
def test_seed_confirmation_establishes_a_baseline_from_the_currently_deployed_version(deployment_env, tmp_path):
    _bootstrap(deployment_env, tmp_path)
    _write_manifest(deployment_env, "little-meals")
    deployed = _deploy(deployment_env)
    assert deployed["status"] == "succeeded", deployed

    seeded = cli_client.create_job(
        deployment_env.paths, type="deployment.seed_confirmation", target_pillar="deployment", project_id="little-meals",
        params={}, queue_key="little-meals", depends_on=[], created_by="integration-test",
    )
    assert seeded["status"] == "succeeded", seeded

    confirmation = cli_client.deployment_confirmation(deployment_env.paths, "little-meals")
    assert confirmation["status"] == "confirmed"
    assert confirmation["confirmed_commit"] == deployed["result"]["commit"]

    systemd.stop("deploy-little-meals", runner=DEFAULT_RUNNER)


@requires_systemd_user
def test_seed_confirmation_refuses_with_nothing_deployed_yet(deployment_env, tmp_path):
    _bootstrap(deployment_env, tmp_path)
    _write_manifest(deployment_env, "little-meals")

    seeded = cli_client.create_job(
        deployment_env.paths, type="deployment.seed_confirmation", target_pillar="deployment", project_id="little-meals",
        params={}, queue_key="little-meals", depends_on=[], created_by="integration-test",
    )
    assert seeded["status"] == "failed"
    assert "nothing has been deployed" in seeded["error"]


@requires_systemd_user
@requires_lws_installed
def test_confirming_a_gated_deploy_settles_it_so_a_later_rollback_call_is_a_no_op(deployment_env, tmp_path):
    _bootstrap(deployment_env, tmp_path)
    _write_manifest(deployment_env, "little-meals", confirm_timeout_seconds=3600)

    deployed = _deploy(deployment_env)
    assert deployed["status"] == "succeeded", deployed
    assert "confirmation" in deployed["result"]["confirm_message"] or "awaiting" in deployed["result"]["confirm_message"]

    pending = cli_client.deployment_confirmation(deployment_env.paths, "little-meals")
    assert pending["status"] == "pending"
    deploy_id = pending["deploy_id"]

    confirmed = cli_client.create_job(
        deployment_env.paths, type="deployment.confirm", target_pillar="deployment", project_id="little-meals",
        params={}, queue_key="little-meals", depends_on=[], created_by="integration-test",
    )
    assert confirmed["status"] == "succeeded", confirmed

    settled = cli_client.deployment_confirmation(deployment_env.paths, "little-meals")
    assert settled["status"] == "confirmed"

    # Calling the watchdog endpoint directly now (as the real armed timer
    # would, eventually) must be a clean no-op — already settled.
    rolled = cli_client.deployment_rollback_timeout(deployment_env.paths, "little-meals", deploy_id)
    assert rolled["rolled_back"] is False

    sweep_transient_units("little-workshop-deploy-timeout-*")
    systemd.stop("deploy-little-meals", runner=DEFAULT_RUNNER)


@requires_systemd_user
@requires_lws_installed
def test_an_unconfirmed_deploy_is_rolled_back_by_a_direct_watchdog_call(deployment_env, tmp_path):
    _bootstrap(deployment_env, tmp_path)
    _write_manifest(deployment_env, "little-meals")

    first = _deploy(deployment_env)
    assert first["status"] == "succeeded", first
    seed = cli_client.create_job(
        deployment_env.paths, type="deployment.seed_confirmation", target_pillar="deployment", project_id="little-meals",
        params={}, queue_key="little-meals", depends_on=[], created_by="integration-test",
    )
    assert seed["status"] == "succeeded", seed
    confirmed_commit = first["result"]["commit"]

    # Redeploy the exact same commit under a gate so there's a real
    # `deploy_id` to roll back against — a same-commit "deploy" is a
    # legitimate, ordinary redeploy, not a special case.
    _write_manifest(deployment_env, "little-meals", confirm_timeout_seconds=3600)
    second = _deploy(deployment_env)
    assert second["status"] == "succeeded", second

    pending = cli_client.deployment_confirmation(deployment_env.paths, "little-meals")
    assert pending["status"] == "pending"

    rolled = cli_client.deployment_rollback_timeout(deployment_env.paths, "little-meals", pending["deploy_id"])
    assert rolled["rolled_back"] is True

    settled = cli_client.deployment_confirmation(deployment_env.paths, "little-meals")
    # A rollback settles back to "confirmed" on the commit it rolled back
    # to — there's no separate "rolled_back" status; the system is simply
    # confirmed again, on the known-good commit.
    assert settled["status"] == "confirmed"
    assert settled["confirmed_commit"] == confirmed_commit

    assert port_is_listening(PORT), "server should still be listening after rollback (same commit, redeployed)"

    sweep_transient_units("little-workshop-deploy-timeout-*")
    systemd.stop("deploy-little-meals", runner=DEFAULT_RUNNER)


@requires_systemd_user
def test_a_bad_install_command_fails_the_deploy_without_touching_the_running_server(deployment_env, tmp_path):
    _bootstrap(deployment_env, tmp_path)
    _write_manifest(deployment_env, "little-meals")

    good = _deploy(deployment_env)
    assert good["status"] == "succeeded", good
    assert port_is_listening(PORT)

    _write_manifest(deployment_env, "little-meals", install="false")  # `false` always exits non-zero
    bad = _deploy(deployment_env)
    assert bad["status"] == "failed", bad

    assert port_is_listening(PORT), "a failed build/install must never take the running server down"

    with client_for_socket(deployment_env.paths.socket_path("deployment"), timeout=5.0) as http:
        status = http.get("/v1/status/little-meals").json()
    assert status["listening"] is True

    systemd.stop("deploy-little-meals", runner=DEFAULT_RUNNER)


@requires_systemd_user
def test_deployment_history_accumulates_every_real_deploy(deployment_env, tmp_path):
    _bootstrap(deployment_env, tmp_path)
    _write_manifest(deployment_env, "little-meals")

    first = _deploy(deployment_env)
    assert first["status"] == "succeeded", first
    second = _deploy(deployment_env)
    assert second["status"] == "succeeded", second

    history = cli_client.deployment_history(deployment_env.paths, "little-meals")
    assert len(history) == 2
    assert all(entry["outcome"] == "succeeded" for entry in history)

    systemd.stop("deploy-little-meals", runner=DEFAULT_RUNNER)


@requires_systemd_user
def test_deployment_branches_lists_the_projects_real_branches(deployment_env, tmp_path):
    _bootstrap(deployment_env, tmp_path)
    branches = cli_client.deployment_branches(deployment_env.paths, "little-meals")
    assert "master" in branches
    assert "dev" in branches


@requires_systemd_user
def test_deploying_a_nonexistent_branch_fails_cleanly(deployment_env, tmp_path):
    _bootstrap(deployment_env, tmp_path)
    _write_manifest(deployment_env, "little-meals")

    result = _deploy(deployment_env, branch="does-not-exist")
    assert result["status"] == "failed"
    assert "does-not-exist" in result["error"]
