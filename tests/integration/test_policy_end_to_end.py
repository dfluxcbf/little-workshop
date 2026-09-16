"""M12's end-to-end proof: the full four-gate authoring pipeline against
a real Agentic Development unit (the fake `claude` executable, the same
contract-preserving stub every prior agent-flow integration test already
uses — never the real, costly, non-deterministic CLI), a real Docker-
sandboxed policy check against a real bootstrapped project, and real
action-gating: a gated `deployment.deploy` Job genuinely blocked at
`pending_confirmation`, resolved once via a real confirm (dispatches
despite the failure) and once via the real confirm-timeout endpoint
(fails, and the server never starts) — the "two clocks, never both on
the same path" distinction `docs/pillars/policy_compliance.md` calls for.
"""

from __future__ import annotations

import os
import stat
import sys
import time
from pathlib import Path

from cli import client as cli_client
from common import systemd
from common.manifest import Manifest
from common.projectfiles import ProjectFilesClient
from common.shell import DEFAULT_RUNNER
from core.app import build_app as build_core_app
from core.health import probe_health
from core.registry import PillarStatus, RegistryEntry
from core.supervisor import Supervisor
from deployment.netutil import port_is_listening
from helpers import UvicornThread, requires_systemd_user, sweep_transient_units
from orchestration import db as orchestration_db
from orchestration.app import build_app as build_orchestration_app

PILLARS = ("registry", "deployment", "agent", "policy")
PORT = 18877

_FAKE_CLAUDE = r"""#!/usr/bin/env python3
import json
import re
import sys

prompt = sys.argv[2]

if "ENFORCEABLE:" in prompt:
    result = "ENFORCEABLE: yes"
elif "pytest test script" in prompt:
    if "ALWAYS_FAIL" in prompt:
        result = "def test_never():\n    assert False\n"
    elif "README_EXISTS" in prompt:
        result = "from pathlib import Path\ndef test_readme():\n    assert Path('/project/README.md').exists()\n"
    else:
        result = "def test_default():\n    assert True\n"
elif "NO_CONFLICT" in prompt or "CONFLICT:" in prompt:
    result = "NO_CONFLICT"
else:
    match = re.search(r"Requirement:\n(.*?)\n\n", prompt, re.DOTALL)
    text = match.group(1) if match else prompt
    result = "EARS_OK: " + text

print(json.dumps({"result": result, "is_error": False}))
"""


def _write_fake_claude(fake_bin: Path) -> None:
    claude_path = fake_bin / "claude"
    claude_path.write_text(_FAKE_CLAUDE, encoding="utf-8")
    claude_path.chmod(claude_path.stat().st_mode | stat.S_IEXEC)


class _Session:
    def __init__(self, workshop_env, fake_bin, monkeypatch):
        self.workshop_env = workshop_env
        self.fake_bin = fake_bin
        self.monkeypatch = monkeypatch

    def __enter__(self):
        try:
            self.monkeypatch.setenv("PATH", f"{self.fake_bin}:{os.environ.get('PATH', '')}")
            env = self.workshop_env.env
            started = []
            for pillar in PILLARS:
                systemd.start(pillar, [sys.executable, "-m", pillar], environment=env, runner=DEFAULT_RUNNER)
                started.append(pillar)
            for pillar in started:
                probe = probe_health(self.workshop_env.paths.socket_path(pillar), timeout=5.0, max_retries=10)
                assert probe.ok, f"{pillar}: {probe.error}"

            core_supervisor = Supervisor(paths=self.workshop_env.paths, manifest=Manifest())
            for pillar in (*started, "orchestration"):
                core_supervisor.registry.set(
                    RegistryEntry(name=pillar, socket_path=self.workshop_env.paths.socket_path(pillar), status=PillarStatus.HEALTHY)
                )
            self.core_server = UvicornThread(build_core_app(core_supervisor), self.workshop_env.paths.core_socket_path)
            self.core_server.start()

            def socket_lookup(pillar: str):
                return self.workshop_env.paths.socket_path(pillar) if pillar in PILLARS else None

            self.conn = orchestration_db.connect(self.workshop_env.paths.pillar_db_path("orchestration"))
            self.orch_server = UvicornThread(build_orchestration_app(self.conn, socket_lookup), self.workshop_env.paths.socket_path("orchestration"))
            self.orch_server.start()
        except BaseException:
            for pillar in PILLARS:
                systemd.stop(pillar, runner=DEFAULT_RUNNER)
            raise
        return self

    def __exit__(self, *exc):
        self.orch_server.stop()
        self.core_server.stop()
        self.conn.close()
        for pillar in PILLARS:
            systemd.stop(pillar, runner=DEFAULT_RUNNER)
        sweep_transient_units("little-workshop-policy-confirm-timeout-*")


def _author(workshop_env, **params):
    defaults = {"created_by": "user"}
    defaults.update(params)
    return cli_client.create_job(
        workshop_env.paths, type="policy.author", target_pillar="policy", params=defaults,
        queue_key=None, depends_on=[], created_by="integration-test", timeout=120.0,
    )


def _activate(workshop_env, draft, **overrides):
    params = {**draft, "approved": True, **overrides}
    return cli_client.create_job(
        workshop_env.paths, type="policy.activate", target_pillar="policy", params=params,
        queue_key=None, depends_on=[], created_by="integration-test", timeout=60.0,
    )


def _pump(workshop_env):
    """`dispatch_ready_jobs` only walks one snapshot of `ready_jobs()` per
    call — when a gate's `policy.check_project` dependency finishes
    inside that same call, the now-ready gated Job isn't picked up until
    the *next* pump. Every real caller gets this for free on its own
    next activity; a test asserting immediately after one `create_job`
    needs one explicit extra pump, the same real `/v1/pump` endpoint any
    caller could hit."""
    from common.uds_client import client_for_socket

    with client_for_socket(workshop_env.paths.socket_path("orchestration"), timeout=10.0) as http:
        http.post("/v1/pump")


def _wait_until(predicate, timeout=60.0, interval=0.3):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def _bootstrap(tmp_path, workshop_env, project_id="little-widget"):
    dest_dir = tmp_path / "workdir"
    dest_dir.mkdir(exist_ok=True)
    bootstrap = cli_client.create_job(
        workshop_env.paths, type="project.bootstrap", target_pillar="registry", project_id=project_id,
        params={"name": project_id, "dest_dir": str(dest_dir)}, queue_key=None, depends_on=[],
        created_by="integration-test",
    )
    assert bootstrap["status"] == "succeeded", bootstrap
    return dest_dir / project_id


@requires_systemd_user
def test_authoring_pipeline_produces_a_real_activated_policy_against_a_real_agent_unit(tmp_path, workshop_env, monkeypatch):
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    _write_fake_claude(fake_bin)

    with _Session(workshop_env, fake_bin, monkeypatch):
        author = _author(
            workshop_env, ears_text="every project needs a README",
            topic="readme-presence", target_pillar="registry", scope_level="all_projects",
        )
        assert author["status"] == "succeeded", author
        draft = author["result"]
        assert draft["ears_text"] == "EARS_OK: every project needs a README" or "README" in draft["ears_text"]
        assert "def test_default" in draft["test_script"]

        activated = _activate(workshop_env, draft)
        assert activated["status"] == "succeeded", activated
        policy_id = activated["result"]["id"]

        fetched = cli_client.policy_get(workshop_env.paths, policy_id)
        assert fetched["policy"]["topic"] == "readme-presence"


@requires_systemd_user
def test_check_project_runs_a_real_docker_sandboxed_check_against_a_real_bootstrapped_project(tmp_path, workshop_env, monkeypatch):
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    _write_fake_claude(fake_bin)

    with _Session(workshop_env, fake_bin, monkeypatch):
        project_id = "little-widget"
        _bootstrap(tmp_path, workshop_env, project_id)

        author = _author(
            workshop_env, ears_text="README_EXISTS: every project shall have a README",
            topic="readme-check", target_pillar="registry", scope_level="all_projects",
        )
        policy_id = _activate(workshop_env, author["result"])["result"]["id"]

        checked = cli_client.create_job(
            workshop_env.paths, type="policy.check_project", target_pillar="policy", project_id=project_id,
            params={"policy_id": policy_id}, queue_key=None, depends_on=[], created_by="integration-test",
            timeout=120.0,
        )
        assert checked["status"] == "succeeded", checked
        assert checked["result"]["outcome"] == "pass"

        results = cli_client.policy_check_results(workshop_env.paths, policy_id, project_id)
        assert results["results"][0]["outcome"] == "pass"


@requires_systemd_user
def test_gated_deploy_reaches_pending_confirmation_and_a_real_confirm_dispatches_it(tmp_path, workshop_env, monkeypatch):
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    _write_fake_claude(fake_bin)

    with _Session(workshop_env, fake_bin, monkeypatch):
        project_id = "little-widget"
        project_dir = _bootstrap(tmp_path, workshop_env, project_id)

        author = _author(
            workshop_env, ears_text="ALWAYS_FAIL: deploys are always blocked for this test",
            topic="deployment-policy", target_pillar="deployment", scope_level="all_projects",
        )
        _activate(workshop_env, author["result"])

        manifest_toml = f"""
[server]
name = "{project_id}"
command = "python3 -m http.server {PORT}"
port = {PORT}
match = "http.server {PORT}"
install = ["true"]
ready_timeout_seconds = 15
stop_timeout_seconds = 15
"""
        ProjectFilesClient(workshop_env.paths, caller_pillar="deployment").write(project_id, "manifest.deployment.toml", manifest_toml)

        deploy = cli_client.create_job(
            workshop_env.paths, type="deployment.deploy", target_pillar="deployment", project_id=project_id,
            params={"branch": "master"}, queue_key=project_id, depends_on=[], created_by="integration-test",
        )
        _pump(workshop_env)
        assert _wait_until(lambda: cli_client.job_status(workshop_env.paths, deploy["id"])["job"]["status"] == "pending_confirmation")
        deploy = cli_client.job_status(workshop_env.paths, deploy["id"])["job"]
        assert not port_is_listening(PORT)

        confirmed = cli_client.policy_confirm_job(workshop_env.paths, deploy["id"])
        assert confirmed["confirmed"] is True
        assert confirmed["job"]["status"] == "succeeded"
        assert port_is_listening(PORT)

        systemd.stop(f"deploy-{project_id}", runner=DEFAULT_RUNNER)


@requires_systemd_user
def test_gated_deploy_times_out_unconfirmed_and_the_action_never_happens(tmp_path, workshop_env, monkeypatch):
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    _write_fake_claude(fake_bin)

    with _Session(workshop_env, fake_bin, monkeypatch):
        project_id = "little-widget"
        _bootstrap(tmp_path, workshop_env, project_id)

        author = _author(
            workshop_env, ears_text="ALWAYS_FAIL: deploys are always blocked for this test too",
            topic="deployment-policy", target_pillar="deployment", scope_level="all_projects",
        )
        _activate(workshop_env, author["result"])

        port = PORT + 1
        manifest_toml = f"""
[server]
name = "{project_id}"
command = "python3 -m http.server {port}"
port = {port}
match = "http.server {port}"
install = ["true"]
ready_timeout_seconds = 15
stop_timeout_seconds = 15
"""
        ProjectFilesClient(workshop_env.paths, caller_pillar="deployment").write(project_id, "manifest.deployment.toml", manifest_toml)

        deploy = cli_client.create_job(
            workshop_env.paths, type="deployment.deploy", target_pillar="deployment", project_id=project_id,
            params={"branch": "master"}, queue_key=project_id, depends_on=[], created_by="integration-test",
        )
        _pump(workshop_env)
        assert _wait_until(lambda: cli_client.job_status(workshop_env.paths, deploy["id"])["job"]["status"] == "pending_confirmation")

        # The real confirm-timeout endpoint, called directly rather than
        # waiting out the real 10-minute systemd-run watchdog it also
        # armed for real (proven separately at the unit level) — this is
        # exactly what that timer's own callback would do.
        timed_out = cli_client.policy_confirm_timeout_job(workshop_env.paths, deploy["id"])
        assert timed_out["timed_out"] is True

        settled = cli_client.job_status(workshop_env.paths, deploy["id"])
        assert settled["job"]["status"] == "failed"
        assert settled["job"]["error"] == "policy check unconfirmed"
        assert not port_is_listening(port)
