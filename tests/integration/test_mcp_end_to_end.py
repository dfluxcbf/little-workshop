"""M7's end-to-end proof: a real MCP `tools/call` JSON-RPC request, driven
through `MCPServer.handle_request` exactly as `lws mcp serve`'s stdio loop
would, reaches Registry & Portfolio (a real `systemd --user` unit) via a
real Orchestration-dispatched Job — discovered through a real Core, never
hardcoded — and a real read-only GET reaches it the same way. This is the
"proxy pattern established" M7's own milestone text calls for: nothing
here is mocked except that Core and Orchestration run in-process (the
same simplification `test_version_end_to_end.py` already uses) rather
than as their own systemd units.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest
import uvicorn

INTEGRATION = Path(__file__).resolve().parent
SRC = INTEGRATION.parents[1] / "src"


def _systemd_user_available() -> bool:
    try:
        result = subprocess.run(
            ["systemctl", "--user", "is-system-running"], capture_output=True, text=True, timeout=5
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return result.stdout.strip() in ("running", "degraded")


requires_systemd_user = pytest.mark.skipif(
    not _systemd_user_available(), reason="systemd --user session not available"
)

from common import systemd
from common.paths import Paths
from common.shell import DEFAULT_RUNNER
from core.app import build_app as build_core_app
from core.health import probe_health
from core.registry import PillarStatus, RegistryEntry
from core.supervisor import Supervisor
from common.manifest import Manifest
from mcp_server.server import MCPServer
from orchestration.app import build_app as build_orchestration_app
from orchestration import db as orchestration_db


class _UvicornThread:
    def __init__(self, app, socket_path):
        self._config = uvicorn.Config(app, uds=str(socket_path), log_level="warning")
        self._server = uvicorn.Server(self._config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)

    def start(self) -> None:
        self._thread.start()
        for _ in range(100):
            if getattr(self._server, "started", False):
                return
            time.sleep(0.05)
        raise RuntimeError("test server did not start in time")

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=5)


@requires_systemd_user
def test_mcp_tool_call_bootstraps_and_lists_a_real_project(tmp_path):
    xdg_config = tmp_path / "xdgconfig"
    xdg_state = Path(tempfile.mkdtemp(prefix="lwsit-"))
    paths = Paths(config_home=xdg_config / "little-workshop", state_home=xdg_state / "little-workshop")
    paths.ensure_layout()

    env = {"PYTHONPATH": str(SRC), "XDG_CONFIG_HOME": str(xdg_config), "XDG_STATE_HOME": str(xdg_state)}
    systemd.start("registry", [sys.executable, "-m", "registry"], environment=env, runner=DEFAULT_RUNNER)

    try:
        probe = probe_health(paths.socket_path("registry"), timeout=5.0, max_retries=10)
        assert probe.ok, f"registry pillar never became healthy: {probe.error}"

        conn = orchestration_db.connect(paths.pillar_db_path("orchestration"))

        def socket_lookup(pillar: str):
            return paths.socket_path("registry") if pillar == "registry" else None

        orch_server = _UvicornThread(build_orchestration_app(conn, socket_lookup), paths.socket_path("orchestration"))
        orch_server.start()

        core_supervisor = Supervisor(paths=paths, manifest=Manifest())
        core_supervisor.registry.set(
            RegistryEntry(name="registry", socket_path=paths.socket_path("registry"), status=PillarStatus.HEALTHY)
        )
        core_supervisor.registry.set(
            RegistryEntry(name="orchestration", socket_path=paths.socket_path("orchestration"), status=PillarStatus.HEALTHY)
        )
        core_server = _UvicornThread(build_core_app(core_supervisor), paths.core_socket_path)
        core_server.start()

        try:
            mcp = MCPServer(paths=paths)

            dest_dir = tmp_path / "workdir"
            dest_dir.mkdir()
            call_response = mcp.handle_request({
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {
                    "name": "workshop_project_bootstrap",
                    "arguments": {"name": "little-widget", "dest_dir": str(dest_dir)},
                },
            })
            assert call_response["result"]["isError"] is False, call_response

            project_dir = dest_dir / "little-widget"
            assert (project_dir / ".git").is_dir()
            assert (project_dir / "docs" / "design.md").exists()

            list_response = mcp.handle_request({
                "jsonrpc": "2.0", "id": 2, "method": "tools/call",
                "params": {"name": "workshop_project_list", "arguments": {}},
            })
            assert list_response["result"]["isError"] is False, list_response
            import json

            payload = json.loads(list_response["result"]["content"][0]["text"])
            assert [p["name"] for p in payload] == ["little-widget"]
        finally:
            orch_server.stop()
            core_server.stop()
            conn.close()
    finally:
        systemd.stop("registry", runner=DEFAULT_RUNNER)
        shutil.rmtree(xdg_state, ignore_errors=True)
