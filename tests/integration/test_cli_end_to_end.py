"""Testing-fortification pass: `lws` itself, driven exactly as a user
would type it, against real running daemons -- proving the CLI's success
path (not just its daemon-unavailable error path, already covered by
tests/unit/test_cli.py) actually reaches real pillars over real sockets.
"""

from __future__ import annotations

import json
import subprocess
import sys

from cli import main as cli_main
from common import systemd
from common.shell import DEFAULT_RUNNER
from core.app import build_app as build_core_app
from core.health import probe_health
from core.registry import PillarStatus, RegistryEntry
from core.supervisor import Supervisor
from common.manifest import Manifest
from orchestration.app import build_app as build_orchestration_app
from orchestration import db as orchestration_db

from helpers import UvicornThread, requires_systemd_user


@requires_systemd_user
def test_lws_project_bootstrap_produces_a_real_project_on_disk(tmp_path, workshop_env, capsys, monkeypatch):
    systemd.start("registry", [sys.executable, "-m", "registry"], environment=workshop_env.env, runner=DEFAULT_RUNNER)
    try:
        probe = probe_health(workshop_env.paths.socket_path("registry"), timeout=5.0, max_retries=10)
        assert probe.ok, probe.error

        conn = orchestration_db.connect(workshop_env.paths.pillar_db_path("orchestration"))

        def socket_lookup(pillar: str):
            return workshop_env.paths.socket_path("registry") if pillar == "registry" else None

        orch_server = UvicornThread(build_orchestration_app(conn, socket_lookup), workshop_env.paths.socket_path("orchestration"))
        orch_server.start()
        try:
            monkeypatch.setattr(cli_main.Paths, "from_env", staticmethod(lambda: workshop_env.paths))
            dest_dir = tmp_path / "workdir"
            dest_dir.mkdir()

            exit_code = cli_main.main(["project", "bootstrap", "little-widget", "--dest-dir", str(dest_dir)])
            assert exit_code == 0
            output = json.loads(capsys.readouterr().out)
            assert output["status"] == "succeeded"

            project_dir = dest_dir / "little-widget"
            assert (project_dir / ".git").is_dir()
            assert (project_dir / "docs" / "design.md").exists()
        finally:
            orch_server.stop()
            conn.close()
    finally:
        systemd.stop("registry", runner=DEFAULT_RUNNER)


@requires_systemd_user
def test_lws_version_feature_start_then_finish_completes_a_real_cycle(tmp_path, workshop_env, capsys, monkeypatch):
    env = workshop_env.env
    systemd.start("registry", [sys.executable, "-m", "registry"], environment=env, runner=DEFAULT_RUNNER)
    systemd.start("version", [sys.executable, "-m", "version"], environment=env, runner=DEFAULT_RUNNER)
    try:
        for pillar in ("registry", "version"):
            probe = probe_health(workshop_env.paths.socket_path(pillar), timeout=5.0, max_retries=10)
            assert probe.ok, f"{pillar}: {probe.error}"

        core_supervisor = Supervisor(paths=workshop_env.paths, manifest=Manifest())
        for pillar in ("registry", "version"):
            core_supervisor.registry.set(
                RegistryEntry(name=pillar, socket_path=workshop_env.paths.socket_path(pillar), status=PillarStatus.HEALTHY)
            )
        core_server = UvicornThread(build_core_app(core_supervisor), workshop_env.paths.core_socket_path)
        core_server.start()

        conn = orchestration_db.connect(workshop_env.paths.pillar_db_path("orchestration"))

        def socket_lookup(pillar: str):
            return workshop_env.paths.socket_path(pillar) if pillar in ("registry", "version") else None

        orch_server = UvicornThread(build_orchestration_app(conn, socket_lookup), workshop_env.paths.socket_path("orchestration"))
        orch_server.start()
        try:
            monkeypatch.setattr(cli_main.Paths, "from_env", staticmethod(lambda: workshop_env.paths))
            dest_dir = tmp_path / "workdir"
            dest_dir.mkdir()

            assert cli_main.main(["project", "bootstrap", "little-widget", "--dest-dir", str(dest_dir)]) == 0
            capsys.readouterr()
            project_dir = dest_dir / "little-widget"

            assert cli_main.main(["version", "init", "--project-id", "little-widget"]) == 0
            capsys.readouterr()
            subprocess.run(["git", "add", "-A"], cwd=project_dir, check=True)
            subprocess.run(["git", "commit", "-m", "chore: version init"], cwd=project_dir, check=True, capture_output=True)
            subprocess.run(["git", "checkout", "dev"], cwd=project_dir, check=True, capture_output=True)
            subprocess.run(["git", "merge", "--ff-only", "master"], cwd=project_dir, check=True, capture_output=True)

            assert cli_main.main(["version", "feature-start", "--project-id", "little-widget", "widgets"]) == 0
            capsys.readouterr()
            branches = subprocess.run(["git", "branch"], cwd=project_dir, capture_output=True, text=True).stdout
            assert "feature/widgets" in branches

            (project_dir / "widgets.py").write_text("x = 1\n", encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=project_dir, check=True)
            subprocess.run(["git", "commit", "-m", "add widgets"], cwd=project_dir, check=True, capture_output=True)

            assert cli_main.main(["version", "feature-finish", "--project-id", "little-widget"]) == 0
            capsys.readouterr()

            subprocess.run(["git", "checkout", "dev"], cwd=project_dir, check=True, capture_output=True)
            assert (project_dir / "widgets.py").exists()
            assert (project_dir / "VERSION").read_text().strip() == "0.1.1"
        finally:
            orch_server.stop()
            core_server.stop()
            conn.close()
    finally:
        systemd.stop("registry", runner=DEFAULT_RUNNER)
        systemd.stop("version", runner=DEFAULT_RUNNER)


@requires_systemd_user
def test_lws_core_status_and_job_list_report_daemon_source_when_live(workshop_env, capsys, monkeypatch):
    systemd.start("registry", [sys.executable, "-m", "registry"], environment=workshop_env.env, runner=DEFAULT_RUNNER)
    try:
        probe = probe_health(workshop_env.paths.socket_path("registry"), timeout=5.0, max_retries=10)
        assert probe.ok, probe.error

        core_supervisor = Supervisor(paths=workshop_env.paths, manifest=Manifest())
        core_supervisor.registry.set(
            RegistryEntry(name="registry", socket_path=workshop_env.paths.socket_path("registry"), status=PillarStatus.HEALTHY)
        )
        core_server = UvicornThread(build_core_app(core_supervisor), workshop_env.paths.core_socket_path)
        core_server.start()

        conn = orchestration_db.connect(workshop_env.paths.pillar_db_path("orchestration"))

        def socket_lookup(pillar: str):
            return workshop_env.paths.socket_path("registry") if pillar == "registry" else None

        orch_server = UvicornThread(build_orchestration_app(conn, socket_lookup), workshop_env.paths.socket_path("orchestration"))
        orch_server.start()
        try:
            monkeypatch.setattr(cli_main.Paths, "from_env", staticmethod(lambda: workshop_env.paths))

            assert cli_main.main(["core", "status"]) == 0
            status = json.loads(capsys.readouterr().out)
            assert status["source"] == "daemon"
            assert status["pillars"]["registry"]["status"] == "healthy"

            assert cli_main.main(["job", "list"]) == 0
            jobs = json.loads(capsys.readouterr().out)
            assert jobs["source"] == "daemon"
        finally:
            orch_server.stop()
            core_server.stop()
            conn.close()
    finally:
        systemd.stop("registry", runner=DEFAULT_RUNNER)
