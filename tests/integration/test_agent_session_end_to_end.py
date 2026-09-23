"""M16 `little-runner` parity: `lrun session start/stop/list` as
`agent.session_start`/`session_stop` -- real `dtach` and real
`systemd-run --user --scope` mechanics, against a real Registry and
Agentic Development `systemd --user` pair. Only the Claude CLI itself is
faked (a `claude` stub on PATH that just sleeps, standing in for an open
interactive session -- never the real, expensive, non-deterministic
CLI this suite always fakes elsewhere), and the workspace-trust check
reads a fake `.claude.json` via `LITTLE_WORKSHOP_CLAUDE_CONFIG_PATH`
rather than the real user's own file, since a test must never touch a
developer's actual Claude Code trust state.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from cli import client as cli_client
from common import systemd
from common.manifest import Manifest
from common.shell import DEFAULT_RUNNER
from core.app import build_app as build_core_app
from core.health import probe_health
from core.registry import PillarStatus, RegistryEntry
from core.supervisor import Supervisor
from helpers import UvicornThread, requires_systemd_user
from orchestration import db as orchestration_db
from orchestration.app import build_app as build_orchestration_app


PILLARS = ("registry", "agent")

FAKE_CLAUDE_SCRIPT = """#!/usr/bin/env python3
import time
time.sleep(30)
"""


class _Session:
    def __init__(self, workshop_env, *, extra_env: dict | None = None):
        self.workshop_env = workshop_env
        self.extra_env = extra_env or {}

    def __enter__(self):
        env = {**self.workshop_env.env, **self.extra_env}
        for pillar in PILLARS:
            systemd.start(pillar, [sys.executable, "-m", pillar], environment=env, runner=DEFAULT_RUNNER)
        for pillar in PILLARS:
            probe = probe_health(self.workshop_env.paths.socket_path(pillar), timeout=5.0, max_retries=10)
            assert probe.ok, f"{pillar}: {probe.error}"

        core_supervisor = Supervisor(paths=self.workshop_env.paths, manifest=Manifest())
        for pillar in PILLARS:
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
        return self

    def __exit__(self, *exc):
        self.orch_server.stop()
        self.core_server.stop()
        self.conn.close()
        for pillar in PILLARS:
            systemd.stop(pillar, runner=DEFAULT_RUNNER)


def _job(workshop_env, job_type, target_pillar, project_id, params):
    return cli_client.create_job(
        workshop_env.paths, type=job_type, target_pillar=target_pillar, project_id=project_id,
        params=params, queue_key=None, depends_on=[], created_by="integration-test",
    )


def _wait_until(predicate, timeout=15.0, interval=0.2):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


@requires_systemd_user
def test_session_start_stop_uses_real_dtach_and_a_real_systemd_scope(tmp_path, workshop_env, monkeypatch):
    fake_bin = Path(tempfile.mkdtemp(prefix="lwsit-claude-bin-"))
    claude_path = fake_bin / "claude"
    claude_path.write_text(FAKE_CLAUDE_SCRIPT, encoding="utf-8")
    claude_path.chmod(claude_path.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{fake_bin}:{os.environ.get('PATH', '')}")

    project_dir = tmp_path / "little-widget"
    project_dir.mkdir()
    trust_config = tmp_path / "claude.json"
    trust_config.write_text(json.dumps({"projects": {str(project_dir): {"hasTrustDialogAccepted": True}}}), encoding="utf-8")

    extra_env = {"LITTLE_WORKSHOP_CLAUDE_CONFIG_PATH": str(trust_config)}

    # project.import only requires a real git repo -- a session doesn't
    # need gitflow at all, so a bare `git init` is enough to satisfy it.
    subprocess.run(["git", "init"], cwd=project_dir, check=True, capture_output=True)

    with _Session(workshop_env, extra_env=extra_env):
        imported = _job(workshop_env, "project.import", "registry", "little-widget", {
            "name": "little-widget", "path": str(project_dir),
        })
        assert imported["status"] == "succeeded", imported

        status_before = cli_client.agent_session_status(workshop_env.paths, "little-widget")
        assert status_before["running"] is False

        started = _job(workshop_env, "agent.session_start", "agent", "little-widget", {})
        assert started["status"] == "succeeded", started
        assert started["result"]["session_name"] == "lws-little-widget-session"

        status_running = cli_client.agent_session_status(workshop_env.paths, "little-widget")
        assert status_running == {"project_id": "little-widget", "running": True, "trusted": True, "session_name": "lws-little-widget-session"}

        # A second start is refused while one is already running.
        duplicate = _job(workshop_env, "agent.session_start", "agent", "little-widget", {})
        assert duplicate["status"] == "failed", duplicate
        assert "already running" in duplicate["error"]

        stopped = _job(workshop_env, "agent.session_stop", "agent", "little-widget", {})
        assert stopped["status"] == "succeeded", stopped
        assert stopped["result"]["stopped"] is True

        assert _wait_until(
            lambda: cli_client.agent_session_status(workshop_env.paths, "little-widget")["running"] is False, 10.0
        )

        stopped_again = _job(workshop_env, "agent.session_stop", "agent", "little-widget", {})
        assert stopped_again["result"]["stopped"] is False
