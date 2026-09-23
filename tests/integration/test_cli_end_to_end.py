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
from reference_pillar.app import build_app as build_reference_app

from helpers import INTEGRATION, UvicornThread, requires_systemd_user


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

            # project.bootstrap now runs version.init (and the resulting
            # git add/commit/checkout-dev/merge dance) automatically --
            # see registry/app.py's job_bootstrap. Doing it again by hand
            # here would just fail on "nothing to commit".
            assert cli_main.main(["project", "bootstrap", "little-widget", "--dest-dir", str(dest_dir)]) == 0
            capsys.readouterr()
            project_dir = dest_dir / "little-widget"

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


@requires_systemd_user
def test_lws_schedule_add_and_trigger_fires_a_real_job(workshop_env, capsys, monkeypatch):
    env = {"PYTHONPATH": str(INTEGRATION)}
    reference_command = [sys.executable, "-m", "reference_pillar", str(workshop_env.paths.socket_path("reference"))]
    systemd.start("reference", reference_command, environment=env, runner=DEFAULT_RUNNER)
    try:
        probe = probe_health(workshop_env.paths.socket_path("reference"), timeout=5.0, max_retries=10)
        assert probe.ok, probe.error

        conn = orchestration_db.connect(workshop_env.paths.pillar_db_path("orchestration"))

        def socket_lookup(pillar: str):
            return workshop_env.paths.socket_path("reference") if pillar == "reference" else None

        orch_server = UvicornThread(build_orchestration_app(conn, socket_lookup), workshop_env.paths.socket_path("orchestration"))
        orch_server.start()
        try:
            monkeypatch.setattr(cli_main.Paths, "from_env", staticmethod(lambda: workshop_env.paths))

            exit_code = cli_main.main([
                "schedule", "add", "nightly-noop", "--type", "noop", "--target-pillar", "reference",
                "--days", "0,1,2,3,4,5,6", "--time-of-day", "09:00",
            ])
            assert exit_code == 0
            created = json.loads(capsys.readouterr().out)
            task_id = created["id"]
            assert created["schedule"]["kind"] == "recurring"

            assert cli_main.main(["schedule", "list"]) == 0
            listed = json.loads(capsys.readouterr().out)
            assert [t["id"] for t in listed["scheduled_tasks"]] == [task_id]

            exit_code = cli_main.main(["schedule", "trigger", task_id])
            assert exit_code == 0
            job = json.loads(capsys.readouterr().out)
            assert job["status"] == "succeeded"
            assert job["created_by"] == "scheduler"

            assert cli_main.main(["schedule", "status", task_id]) == 0
            status = json.loads(capsys.readouterr().out)
            assert status["scheduled_task"]["last_job_id"] == job["id"]

            assert cli_main.main(["schedule", "remove", task_id]) == 0
            removed = json.loads(capsys.readouterr().out)
            assert removed == {"id": task_id, "removed": True}
        finally:
            orch_server.stop()
            conn.close()
    finally:
        systemd.stop("reference", runner=DEFAULT_RUNNER)


@requires_systemd_user
def test_lws_job_stop_interrupts_a_real_running_job(workshop_env, capsys, monkeypatch):
    env = {"PYTHONPATH": str(INTEGRATION)}
    reference_command = [sys.executable, "-m", "reference_pillar", str(workshop_env.paths.socket_path("reference"))]
    systemd.start("reference", reference_command, environment=env, runner=DEFAULT_RUNNER)
    try:
        probe = probe_health(workshop_env.paths.socket_path("reference"), timeout=5.0, max_retries=10)
        assert probe.ok, probe.error

        conn = orchestration_db.connect(workshop_env.paths.pillar_db_path("orchestration"))

        def socket_lookup(pillar: str):
            return workshop_env.paths.socket_path("reference") if pillar == "reference" else None

        orch_server = UvicornThread(build_orchestration_app(conn, socket_lookup), workshop_env.paths.socket_path("orchestration"))
        orch_server.start()
        try:
            monkeypatch.setattr(cli_main.Paths, "from_env", staticmethod(lambda: workshop_env.paths))

            from cli import client as cli_client

            created = cli_client.create_job(
                workshop_env.paths, type="slow", target_pillar="reference", params={},
                queue_key=None, depends_on=[], created_by="integration-test",
            )
            assert created["status"] == "running"

            exit_code = cli_main.main(["job", "stop", created["id"]])
            assert exit_code == 0
            result = json.loads(capsys.readouterr().out)
            assert result == {"stopped": True, "reason": None}

            assert cli_main.main(["job", "status", created["id"]]) == 0
            status = json.loads(capsys.readouterr().out)
            assert status["job"]["status"] == "stopped"
        finally:
            orch_server.stop()
            conn.close()
    finally:
        systemd.stop("reference", runner=DEFAULT_RUNNER)
