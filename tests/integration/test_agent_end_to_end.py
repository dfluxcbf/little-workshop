"""M6's end-to-end proof: Registry & Portfolio, Version Control &
Release, and Agentic Development, all running as genuine `systemd
--user` units, carry a task through the real `agent.run_task` flow —
real git branching (via a real `version.feature_start` Job call), a
real seven-section report, and a real completion callback to
Orchestration — everything except the agent session itself, which is
stubbed by a fake `claude` executable (never the real, expensive,
non-deterministic CLI) that still exercises the exact same completion-
marker/result-file contract a genuine session would.
"""

from __future__ import annotations

import os
import shutil
import stat
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

from cli import client as cli_client
from common import systemd
from common.paths import Paths
from common.shell import DEFAULT_RUNNER
from core.app import build_app as build_core_app
from core.health import probe_health
from core.registry import PillarStatus, RegistryEntry
from core.supervisor import Supervisor
from common.manifest import Manifest
from orchestration.app import build_app as build_orchestration_app
from orchestration import db as orchestration_db


FAKE_CLAUDE_SCRIPT = """#!/usr/bin/env python3
import json
import re
import sys
from pathlib import Path

prompt = sys.argv[2]
for line in prompt.splitlines():
    stripped = line.strip()
    if stripped.startswith("/") and stripped.endswith(".json"):
        path = Path(stripped)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "status": "done",
            "background": "the project needed this change",
            "goal": "make it work",
            "technical_decisions": "kept it simple",
            "executed_tasks": "wrote the file",
            "test_results": "1 passed",
        }))
        break

print(json.dumps({"result": "did the work\\nAGENT_STATUS: done", "is_error": False}))
"""


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


def _wait_until(predicate, timeout=60.0, interval=0.3):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


@requires_systemd_user
def test_agent_run_task_end_to_end(tmp_path, monkeypatch):
    xdg_config = tmp_path / "xdgconfig"
    xdg_state = Path(tempfile.mkdtemp(prefix="lwsit-"))
    paths = Paths(config_home=xdg_config / "little-workshop", state_home=xdg_state / "little-workshop")
    paths.ensure_layout()

    fake_bin = Path(tempfile.mkdtemp(prefix="lwsit-bin-"))
    claude_path = fake_bin / "claude"
    claude_path.write_text(FAKE_CLAUDE_SCRIPT, encoding="utf-8")
    claude_path.chmod(claude_path.stat().st_mode | stat.S_IEXEC)
    # common.systemd.start() always reads PATH from *this* process's own
    # environment (user_path_env()) and forwards it to the launched unit —
    # prepending the fake claude here is what makes the real `agent`
    # daemon find it instead of a real (expensive, non-deterministic)
    # claude installation.
    monkeypatch.setenv("PATH", f"{fake_bin}:{os.environ.get('PATH', '')}")

    env = {"PYTHONPATH": str(SRC), "XDG_CONFIG_HOME": str(xdg_config), "XDG_STATE_HOME": str(xdg_state)}
    for pillar in ("registry", "version", "agent"):
        systemd.start(pillar, [sys.executable, "-m", pillar], environment=env, runner=DEFAULT_RUNNER)

    try:
        for pillar in ("registry", "version", "agent"):
            probe = probe_health(paths.socket_path(pillar), timeout=5.0, max_retries=10)
            assert probe.ok, f"{pillar} pillar never became healthy: {probe.error}"

        core_supervisor = Supervisor(paths=paths, manifest=Manifest())
        for pillar in ("registry", "version", "agent", "orchestration"):
            core_supervisor.registry.set(
                RegistryEntry(name=pillar, socket_path=paths.socket_path(pillar), status=PillarStatus.HEALTHY)
            )
        core_server = _UvicornThread(build_core_app(core_supervisor), paths.core_socket_path)
        core_server.start()

        def socket_lookup(pillar: str):
            return paths.socket_path(pillar) if pillar in ("registry", "version", "agent") else None

        conn = orchestration_db.connect(paths.pillar_db_path("orchestration"))
        orch_server = _UvicornThread(build_orchestration_app(conn, socket_lookup), paths.socket_path("orchestration"))
        orch_server.start()

        try:
            dest_dir = tmp_path / "workdir"
            dest_dir.mkdir()
            bootstrap = cli_client.create_job(
                paths, type="project.bootstrap", target_pillar="registry", project_id="little-widget",
                params={"name": "little-widget", "dest_dir": str(dest_dir)}, queue_key=None, depends_on=[],
                created_by="integration-test",
            )
            assert bootstrap["status"] == "succeeded", bootstrap
            project_dir = dest_dir / "little-widget"

            init = cli_client.create_job(
                paths, type="version.init", target_pillar="version", project_id="little-widget",
                params={"production_branch": "master", "integration_branch": "dev"}, queue_key="little-widget",
                depends_on=[], created_by="integration-test",
            )
            assert init["status"] == "succeeded", init
            subprocess.run(["git", "add", "-A"], cwd=project_dir, check=True)
            subprocess.run(["git", "commit", "-m", "chore: version init"], cwd=project_dir, check=True, capture_output=True)
            subprocess.run(["git", "checkout", "dev"], cwd=project_dir, check=True, capture_output=True)
            subprocess.run(["git", "merge", "--ff-only", "master"], cwd=project_dir, check=True, capture_output=True)

            created = cli_client.create_job(
                paths, type="agent.run_task", target_pillar="agent", project_id="little-widget",
                params={
                    "title": "add the widget", "intent": "task", "review_mode": "manual",
                    "branching": {"mode": "new", "branch_type": "improvement"},
                },
                queue_key="little-widget", depends_on=[], created_by="integration-test",
            )
            assert created["status"] == "running", created  # accepted: the flow runs in a background thread
            job_id = created["id"]

            def job_settled():
                status = cli_client.job_status(paths, job_id)
                return status["job"] is not None and status["job"]["status"] in ("succeeded", "failed")

            assert _wait_until(job_settled, timeout=60.0), "agent.run_task Job never completed"
            final = cli_client.job_status(paths, job_id)["job"]
            assert final["status"] == "succeeded", final
            run_id = final["result"]["run_id"]
            assert final["result"]["completion_status"] == "done"

            with client_for(paths, "agent") as agent_client:
                run = agent_client.get(f"/v1/runs/little-widget/{run_id}").json()
            assert run["status"] == "awaiting_finish"  # manual review mode never auto-finishes
            assert "Background" in run["report"]
            assert run["branch"].startswith("improvement/")

            branches = subprocess.run(["git", "branch"], cwd=project_dir, capture_output=True, text=True).stdout
            assert run["branch"] in branches
        finally:
            orch_server.stop()
            core_server.stop()
            conn.close()
    finally:
        for pillar in ("registry", "version", "agent"):
            systemd.stop(pillar, runner=DEFAULT_RUNNER)
        shutil.rmtree(xdg_state, ignore_errors=True)
        shutil.rmtree(fake_bin, ignore_errors=True)


def client_for(paths: Paths, pillar: str):
    from common.uds_client import client_for_socket

    return client_for_socket(paths.socket_path(pillar), timeout=5.0)
