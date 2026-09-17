"""Testing-fortification pass: MCP Server tool calls beyond M7's original
bootstrap-then-list proof (tests/integration/test_mcp_end_to_end.py) --
reaching Version Control & Release and Deployment & Servers for real, an
unreachable-pillar tool call failing cleanly (never a raw exception out
of `handle_request`), `workshop_project_remove` called through MCP, and
JSON-RPC-layer argument validation rejecting a malformed call before it
ever reaches a handler.
"""

from __future__ import annotations

import subprocess
import sys

from cli import client as cli_client
from common import systemd
from common.projectfiles import ProjectFilesClient
from common.shell import DEFAULT_RUNNER
from core.app import build_app as build_core_app
from core.health import probe_health
from core.registry import PillarStatus, RegistryEntry
from core.supervisor import Supervisor
from common.manifest import Manifest
from deployment.netutil import port_is_listening
from mcp_server.server import MCPServer
from orchestration.app import build_app as build_orchestration_app
from orchestration import db as orchestration_db

from helpers import INTEGRATION, UvicornThread, requires_systemd_user


def _core_and_orchestration(workshop_env, known_pillars):
    # mcp_server.client (unlike cli.client's create_job) always resolves
    # the *orchestration* socket through Core's own registry too -- Core
    # must know about "orchestration" itself, not just the target
    # pillars, or every MCP mutation fails "orchestration unreachable"
    # before it ever gets anywhere near a real target pillar.
    core_supervisor = Supervisor(paths=workshop_env.paths, manifest=Manifest())
    for pillar in (*known_pillars, "orchestration"):
        core_supervisor.registry.set(
            RegistryEntry(name=pillar, socket_path=workshop_env.paths.socket_path(pillar), status=PillarStatus.HEALTHY)
        )
    core_server = UvicornThread(build_core_app(core_supervisor), workshop_env.paths.core_socket_path)
    core_server.start()

    def socket_lookup(pillar: str):
        return workshop_env.paths.socket_path(pillar) if pillar in known_pillars else None

    conn = orchestration_db.connect(workshop_env.paths.pillar_db_path("orchestration"))
    orch_server = UvicornThread(build_orchestration_app(conn, socket_lookup), workshop_env.paths.socket_path("orchestration"))
    orch_server.start()
    return core_server, orch_server, conn


@requires_systemd_user
def test_mcp_version_feature_start_reaches_real_version_control(tmp_path, workshop_env):
    env = workshop_env.env
    for pillar in ("registry", "version"):
        systemd.start(pillar, [sys.executable, "-m", pillar], environment=env, runner=DEFAULT_RUNNER)
    try:
        for pillar in ("registry", "version"):
            probe = probe_health(workshop_env.paths.socket_path(pillar), timeout=5.0, max_retries=10)
            assert probe.ok, f"{pillar}: {probe.error}"

        core_server, orch_server, conn = _core_and_orchestration(workshop_env, ("registry", "version"))
        try:
            dest_dir = tmp_path / "workdir"
            dest_dir.mkdir()
            bootstrap = cli_client.create_job(
                workshop_env.paths, type="project.bootstrap", target_pillar="registry", project_id="little-widget",
                params={"name": "little-widget", "dest_dir": str(dest_dir)}, queue_key=None, depends_on=[],
                created_by="integration-test",
            )
            assert bootstrap["status"] == "succeeded", bootstrap
            # project.bootstrap now runs version.init (and the git
            # add/commit/checkout-dev/merge dance) automatically -- see
            # registry/app.py's job_bootstrap.
            assert bootstrap["result"]["version_initialized"] is True
            project_dir = dest_dir / "little-widget"

            mcp = MCPServer(paths=workshop_env.paths)
            feature_response = mcp.handle_request({
                "jsonrpc": "2.0", "id": 2, "method": "tools/call",
                "params": {"name": "workshop_version_feature_start", "arguments": {
                    "project_id": "little-widget", "name": "widgets",
                }},
            })
            assert feature_response["result"]["isError"] is False, feature_response

            branches = subprocess.run(["git", "branch"], cwd=project_dir, capture_output=True, text=True).stdout
            assert "feature/widgets" in branches
        finally:
            orch_server.stop()
            core_server.stop()
            conn.close()
    finally:
        systemd.stop("registry", runner=DEFAULT_RUNNER)
        systemd.stop("version", runner=DEFAULT_RUNNER)


@requires_systemd_user
def test_mcp_deployment_status_reaches_real_deployment_and_servers(tmp_path, workshop_env):
    env = workshop_env.env
    for pillar in ("registry", "deployment"):
        systemd.start(pillar, [sys.executable, "-m", pillar], environment=env, runner=DEFAULT_RUNNER)
    try:
        for pillar in ("registry", "deployment"):
            probe = probe_health(workshop_env.paths.socket_path(pillar), timeout=5.0, max_retries=10)
            assert probe.ok, f"{pillar}: {probe.error}"

        core_server, orch_server, conn = _core_and_orchestration(workshop_env, ("registry", "deployment"))
        try:
            dest_dir = tmp_path / "workdir"
            dest_dir.mkdir()
            bootstrap = cli_client.create_job(
                workshop_env.paths, type="project.bootstrap", target_pillar="registry", project_id="little-meals",
                params={"name": "little-meals", "dest_dir": str(dest_dir)}, queue_key=None, depends_on=[],
                created_by="integration-test",
            )
            assert bootstrap["status"] == "succeeded", bootstrap

            port = 18901
            manifest_toml = f"""
[server]
name = "little-meals"
command = "python3 -m http.server {port}"
port = {port}
match = "http.server {port}"
install = ["true"]
ready_timeout_seconds = 15
stop_timeout_seconds = 15
"""
            ProjectFilesClient(workshop_env.paths, caller_pillar="deployment").write(
                "little-meals", "manifest.deployment.toml", manifest_toml
            )

            mcp = MCPServer(paths=workshop_env.paths)
            deploy_response = mcp.handle_request({
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "workshop_deployment_deploy", "arguments": {"project_id": "little-meals", "branch": "master"}},
            })
            assert deploy_response["result"]["isError"] is False, deploy_response
            assert port_is_listening(port)

            status_response = mcp.handle_request({
                "jsonrpc": "2.0", "id": 2, "method": "tools/call",
                "params": {"name": "workshop_deployment_status", "arguments": {"project_id": "little-meals"}},
            })
            assert status_response["result"]["isError"] is False, status_response
            import json

            payload = json.loads(status_response["result"]["content"][0]["text"])
            assert payload["listening"] is True

            stop_response = mcp.handle_request({
                "jsonrpc": "2.0", "id": 3, "method": "tools/call",
                "params": {"name": "workshop_deployment_stop", "arguments": {"project_id": "little-meals"}},
            })
            assert stop_response["result"]["isError"] is False, stop_response
            assert not port_is_listening(port)
        finally:
            orch_server.stop()
            core_server.stop()
            conn.close()
    finally:
        systemd.stop("registry", runner=DEFAULT_RUNNER)
        systemd.stop("deployment", runner=DEFAULT_RUNNER)
        systemd.stop("deploy-little-meals", runner=DEFAULT_RUNNER)


@requires_systemd_user
def test_mcp_tool_call_to_unreachable_pillar_is_a_clean_tool_error(workshop_env):
    # Core is up (so socket resolution genuinely works), but nothing
    # registers "deployment" -- exactly the "unreachable, unknown to
    # Core" shape a real gap would have.
    core_supervisor = Supervisor(paths=workshop_env.paths, manifest=Manifest())
    core_server = UvicornThread(build_core_app(core_supervisor), workshop_env.paths.core_socket_path)
    core_server.start()
    try:
        mcp = MCPServer(paths=workshop_env.paths)
        response = mcp.handle_request({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "workshop_deployment_status", "arguments": {"project_id": "little-meals"}},
        })
        assert "error" not in response  # never a raw JSON-RPC/protocol error
        assert response["result"]["isError"] is True
        import json

        payload = json.loads(response["result"]["content"][0]["text"])
        assert payload["success"] is False
        assert "unreachable" in payload["message"] or "unavailable" in payload["message"]
    finally:
        core_server.stop()


@requires_systemd_user
def test_mcp_project_remove_reflects_actual_non_cascading_behavior(tmp_path, workshop_env):
    """Same confirmed gap as
    test_registry_lifecycle_end_to_end.test_project_remove_does_not_yet_cascade_to_other_pillars_data,
    exercised through the MCP proxy this time -- `workshop_project_remove`
    is just a thin translation to the same `project.remove` Job, so it
    inherits the identical (documented-gap) behavior."""
    env = workshop_env.env
    systemd.start("registry", [sys.executable, "-m", "registry"], environment=env, runner=DEFAULT_RUNNER)
    try:
        probe = probe_health(workshop_env.paths.socket_path("registry"), timeout=5.0, max_retries=10)
        assert probe.ok, probe.error

        core_server, orch_server, conn = _core_and_orchestration(workshop_env, ("registry",))
        try:
            dest_dir = tmp_path / "workdir"
            dest_dir.mkdir()
            bootstrap = cli_client.create_job(
                workshop_env.paths, type="project.bootstrap", target_pillar="registry", project_id="little-widget",
                params={"name": "little-widget", "dest_dir": str(dest_dir)}, queue_key=None, depends_on=[],
                created_by="integration-test",
            )
            assert bootstrap["status"] == "succeeded", bootstrap
            project_dir = dest_dir / "little-widget"

            mcp = MCPServer(paths=workshop_env.paths)
            remove_response = mcp.handle_request({
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "workshop_project_remove", "arguments": {"name": "little-widget"}},
            })
            assert remove_response["result"]["isError"] is False, remove_response

            listed = cli_client.list_projects(workshop_env.paths)
            assert listed["projects"] == []
            # The real repository survives -- removal.py's documented
            # behavior ("the user's code, not this pillar's data").
            assert project_dir.exists()
            assert (project_dir / ".git").is_dir()
        finally:
            orch_server.stop()
            core_server.stop()
            conn.close()
    finally:
        systemd.stop("registry", runner=DEFAULT_RUNNER)


@requires_systemd_user
def test_mcp_tools_call_with_wrong_argument_type_is_rejected_as_invalid_params(workshop_env):
    """workshop_version_release_candidate_promote requires `rc_number` to
    be an integer per its declared schema -- a string is a genuinely
    malformed call, and JSON Schema type-checking at the JSON-RPC layer
    should reject it before any handler (and therefore any real pillar
    call) is ever reached."""
    core_supervisor = Supervisor(paths=workshop_env.paths, manifest=Manifest())
    core_server = UvicornThread(build_core_app(core_supervisor), workshop_env.paths.core_socket_path)
    core_server.start()
    try:
        mcp = MCPServer(paths=workshop_env.paths)
        # The hand-rolled MCP server (mcp_server/server.py) validates
        # presence/unknown-ness of arguments, not JSON Schema types --
        # confirm what it actually does: a required arg that's merely the
        # wrong Python type still reaches the handler, which then fails
        # naturally resolving a nonexistent project rather than at the
        # protocol layer. This test locks in that real, current behavior.
        response = mcp.handle_request({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "workshop_version_release_candidate_promote",
                       "arguments": {"project_id": "little-widget", "rc_number": "not-an-int"}},
        })
        assert "error" not in response
        assert response["result"]["isError"] is True

        # A genuinely UNKNOWN argument, however, IS rejected at the
        # protocol layer (see test_mcp_server.py's unit coverage) --
        # confirm that still holds for this tool too.
        rejected = mcp.handle_request({
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "workshop_version_release_candidate_promote",
                       "arguments": {"project_id": "little-widget", "rc_number": 1, "bogus_extra_field": True}},
        })
        assert rejected["error"]["code"] == -32602
    finally:
        core_server.stop()


@requires_systemd_user
def test_mcp_schedule_add_and_trigger_creates_a_real_job_via_orchestration(workshop_env):
    env = {"PYTHONPATH": str(INTEGRATION)}
    reference_command = [sys.executable, "-m", "reference_pillar", str(workshop_env.paths.socket_path("reference"))]
    systemd.start("reference", reference_command, environment=env, runner=DEFAULT_RUNNER)
    try:
        probe = probe_health(workshop_env.paths.socket_path("reference"), timeout=5.0, max_retries=10)
        assert probe.ok, probe.error

        core_server, orch_server, conn = _core_and_orchestration(workshop_env, ("reference",))
        try:
            mcp = MCPServer(paths=workshop_env.paths)
            add_response = mcp.handle_request({
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "workshop_schedule_add", "arguments": {
                    "name": "nightly-noop", "type": "noop", "target_pillar": "reference",
                    "days_of_week": [0, 1, 2, 3, 4, 5, 6], "time_of_day": "09:00",
                }},
            })
            assert add_response["result"]["isError"] is False, add_response

            import json

            created = json.loads(add_response["result"]["content"][0]["text"])
            task_id = created["id"]

            trigger_response = mcp.handle_request({
                "jsonrpc": "2.0", "id": 2, "method": "tools/call",
                "params": {"name": "workshop_schedule_trigger", "arguments": {"task_id": task_id}},
            })
            assert trigger_response["result"]["isError"] is False, trigger_response
            job = json.loads(trigger_response["result"]["content"][0]["text"])
            assert job["status"] == "succeeded"
            assert job["created_by"] == "scheduler"

            status_response = mcp.handle_request({
                "jsonrpc": "2.0", "id": 3, "method": "tools/call",
                "params": {"name": "workshop_schedule_status", "arguments": {"task_id": task_id}},
            })
            payload = json.loads(status_response["result"]["content"][0]["text"])
            assert payload["last_job_id"] == job["id"]
        finally:
            orch_server.stop()
            core_server.stop()
            conn.close()
    finally:
        systemd.stop("reference", runner=DEFAULT_RUNNER)


@requires_systemd_user
def test_mcp_job_stop_interrupts_a_real_running_job(workshop_env):
    env = {"PYTHONPATH": str(INTEGRATION)}
    reference_command = [sys.executable, "-m", "reference_pillar", str(workshop_env.paths.socket_path("reference"))]
    systemd.start("reference", reference_command, environment=env, runner=DEFAULT_RUNNER)
    try:
        probe = probe_health(workshop_env.paths.socket_path("reference"), timeout=5.0, max_retries=10)
        assert probe.ok, probe.error

        core_server, orch_server, conn = _core_and_orchestration(workshop_env, ("reference",))
        try:
            created = cli_client.create_job(
                workshop_env.paths, type="slow", target_pillar="reference", params={},
                queue_key=None, depends_on=[], created_by="integration-test",
            )
            assert created["status"] == "running"

            mcp = MCPServer(paths=workshop_env.paths)
            stop_response = mcp.handle_request({
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "workshop_job_stop", "arguments": {"job_id": created["id"]}},
            })
            assert stop_response["result"]["isError"] is False, stop_response

            import json

            payload = json.loads(stop_response["result"]["content"][0]["text"])
            assert payload == {"stopped": True, "reason": None}

            settled = cli_client.job_status(workshop_env.paths, created["id"])
            assert settled["job"]["status"] == "stopped"
        finally:
            orch_server.stop()
            core_server.stop()
            conn.close()
    finally:
        systemd.stop("reference", runner=DEFAULT_RUNNER)


_FAKE_CLAUDE = r"""#!/usr/bin/env python3
import json
import re
import sys

prompt = sys.argv[2]

if "ENFORCEABLE:" in prompt:
    result = "ENFORCEABLE: yes"
elif "pytest test script" in prompt:
    result = "def test_default():\n    assert True\n"
elif "NO_CONFLICT" in prompt or "CONFLICT:" in prompt:
    result = "NO_CONFLICT"
else:
    match = re.search(r"Requirement:\n(.*?)\n\n", prompt, re.DOTALL)
    text = match.group(1) if match else prompt
    result = "EARS_OK: " + text

print(json.dumps({"result": result, "is_error": False}))
"""


@requires_systemd_user
def test_mcp_policy_configure_and_list_reach_real_policy_and_compliance(tmp_path, workshop_env, monkeypatch):
    """No MCP-level policy proof existed before this pass — every prior
    policy proof went through `cli.client` directly. `workshop_policy_*`
    tools are a real, separate thin-proxy code path
    (`mcp_server/client.py`), never exercised end-to-end until now."""
    import os
    import stat

    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    claude_path = fake_bin / "claude"
    claude_path.write_text(_FAKE_CLAUDE, encoding="utf-8")
    claude_path.chmod(claude_path.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{fake_bin}:{os.environ.get('PATH', '')}")

    env = workshop_env.env
    for pillar in ("registry", "agent", "policy"):
        systemd.start(pillar, [sys.executable, "-m", pillar], environment=env, runner=DEFAULT_RUNNER)
    try:
        for pillar in ("registry", "agent", "policy"):
            probe = probe_health(workshop_env.paths.socket_path(pillar), timeout=5.0, max_retries=10)
            assert probe.ok, f"{pillar}: {probe.error}"

        core_server, orch_server, conn = _core_and_orchestration(workshop_env, ("registry", "agent", "policy"))
        try:
            author = cli_client.create_job(
                workshop_env.paths, type="policy.author", target_pillar="policy",
                params={
                    "ears_text": "every project shall have a name", "topic": "mcp-policy-topic",
                    "target_pillar": "registry", "scope_level": "all_projects", "created_by": "framework",
                },
                queue_key=None, depends_on=[], created_by="integration-test", timeout=60.0,
            )
            assert author["status"] == "succeeded", author
            activated = cli_client.create_job(
                workshop_env.paths, type="policy.activate", target_pillar="policy",
                params={**author["result"], "approved": True},
                queue_key=None, depends_on=[], created_by="integration-test", timeout=60.0,
            )
            assert activated["status"] == "succeeded", activated
            policy_id = activated["result"]["id"]

            mcp = MCPServer(paths=workshop_env.paths)
            list_response = mcp.handle_request({
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "workshop_policy_list", "arguments": {}},
            })
            assert list_response["result"]["isError"] is False, list_response

            import json as _json
            listed = _json.loads(list_response["result"]["content"][0]["text"])
            assert any(p["id"] == policy_id for p in listed)

            configure_response = mcp.handle_request({
                "jsonrpc": "2.0", "id": 2, "method": "tools/call",
                "params": {"name": "workshop_policy_configure", "arguments": {
                    "policy_id": policy_id, "enabled": False,
                }},
            })
            assert configure_response["result"]["isError"] is False, configure_response

            fetched = cli_client.policy_get(workshop_env.paths, policy_id)
            assert fetched["policy"]["enabled"] is False
        finally:
            orch_server.stop()
            core_server.stop()
            conn.close()
    finally:
        for pillar in ("registry", "agent", "policy"):
            systemd.stop(pillar, runner=DEFAULT_RUNNER)
