"""M11's end-to-end proof: Registry & Portfolio bootstraps a real
project (a genuine `systemd --user` unit), Build & Packages (also real)
runs a real build/test command inside a real Deployment & Servers
worktree (a third real `systemd --user` unit) — never the project's own
working checkout — then publishes a real artifact to the internal PEP
503 index and a real `.vsix` to its own namespace, both downloadable for
real over HTTP. The fork-safety gate is proven against a real,
fork-flagged build run.
"""

from __future__ import annotations

import base64
import subprocess
import sys

from cli import client as cli_client
from common import systemd
from common.manifest import Manifest
from common.shell import DEFAULT_RUNNER
from common.uds_client import client_for_socket
from core.app import build_app as build_core_app
from core.health import probe_health
from core.registry import PillarStatus, RegistryEntry
from core.supervisor import Supervisor
from helpers import UvicornThread, requires_systemd_user
from orchestration import db as orchestration_db
from orchestration.app import build_app as build_orchestration_app


PILLARS = ("registry", "deployment", "build")


class _Session:
    def __init__(self, workshop_env):
        self.workshop_env = workshop_env

    def __enter__(self):
        env = self.workshop_env.env
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


def _job(workshop_env, job_type, project_id, params, *, timeout=60.0):
    return cli_client.create_job(
        workshop_env.paths, type=job_type, target_pillar="build", project_id=project_id,
        params=params, queue_key=None, depends_on=[], created_by="integration-test", timeout=timeout,
    )


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
def test_build_run_executes_a_real_command_inside_a_real_deployment_worktree(tmp_path, workshop_env):
    with _Session(workshop_env):
        project_id = "little-widget"
        project_dir = _bootstrap(tmp_path, workshop_env, project_id)

        marker_script = "import pathlib; pathlib.Path('build-ran-here.txt').write_text('yes')"
        (project_dir / "build.py").write_text(marker_script, encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=project_dir, check=True)
        subprocess.run(["git", "commit", "-m", "add build script"], cwd=project_dir, check=True, capture_output=True)
        subprocess.run(["git", "checkout", "dev"], cwd=project_dir, check=True, capture_output=True)
        subprocess.run(["git", "merge", "--ff-only", "master"], cwd=project_dir, check=True, capture_output=True)

        run = _job(workshop_env, "build.run", project_id, {
            "commitish": "dev", "command": [sys.executable, "build.py"],
        })
        assert run["status"] == "succeeded", run
        assert run["result"]["exit_code"] == 0

        # Never touched the project's own working checkout — the isolated
        # worktree is where the command actually ran.
        assert not (project_dir / "build-ran-here.txt").exists()

        fetched = cli_client.build_get_run(workshop_env.paths, run["result"]["id"])
        assert fetched["build_run"]["status"] == "succeeded"


@requires_systemd_user
def test_build_run_reports_failure_for_a_real_nonzero_exit(tmp_path, workshop_env):
    with _Session(workshop_env):
        project_id = "little-widget"
        _bootstrap(tmp_path, workshop_env, project_id)

        run = _job(workshop_env, "build.run", project_id, {
            "commitish": "master", "command": [sys.executable, "-c", "import sys; sys.exit(1)"],
        })
        assert run["status"] == "failed", run

        listed = cli_client.build_list_runs(workshop_env.paths, project_id)
        matching = [r for r in listed["build_runs"] if r["status"] == "failed"]
        assert matching, listed
        assert matching[0]["exit_code"] == 1


@requires_systemd_user
def test_publish_pypi_and_download_through_the_real_pep503_index(tmp_path, workshop_env):
    with _Session(workshop_env):
        project_id = "little-widget"
        _bootstrap(tmp_path, workshop_env, project_id)

        run = _job(workshop_env, "build.run", project_id, {
            "commitish": "master", "command": [sys.executable, "-c", "print('built')"],
        })
        assert run["status"] == "succeeded", run

        content = base64.b64encode(b"fake wheel content").decode()
        published = _job(workshop_env, "build.publish_pypi", project_id, {
            "build_run_id": run["result"]["id"], "name": "Little_Widget", "version": "1.0.0",
            "filename": "little_widget-1.0.0-py3-none-any.whl", "content_base64": content,
        })
        assert published["status"] == "succeeded", published

        with client_for_socket(workshop_env.paths.socket_path("build"), timeout=5.0) as http:
            root_index = http.get("/simple/")
            assert 'href="little-widget/"' in root_index.text

            project_index = http.get("/simple/Little.Widget/")
            assert project_index.status_code == 200
            assert "little_widget-1.0.0-py3-none-any.whl" in project_index.text

            download = http.get("/packages/little_widget-1.0.0-py3-none-any.whl")
            assert download.status_code == 200
            assert download.content == b"fake wheel content"


@requires_systemd_user
def test_publish_vsix_and_download_through_its_own_namespace(tmp_path, workshop_env):
    with _Session(workshop_env):
        project_id = "little-widget"
        _bootstrap(tmp_path, workshop_env, project_id)

        run = _job(workshop_env, "build.run", project_id, {
            "commitish": "master", "command": [sys.executable, "-c", "print('built')"],
        })
        assert run["status"] == "succeeded", run

        content = base64.b64encode(b"fake vsix content").decode()
        published = _job(workshop_env, "build.publish_vsix", project_id, {
            "build_run_id": run["result"]["id"], "name": "little-widget-ext", "version": "0.1.0",
            "filename": "little-widget-ext-0.1.0.vsix", "content_base64": content,
        })
        assert published["status"] == "succeeded", published

        with client_for_socket(workshop_env.paths.socket_path("build"), timeout=5.0) as http:
            versions = http.get("/vsix/little-widget-ext").json()
            assert [v["version"] for v in versions] == ["0.1.0"]

            download = http.get("/vsix/little-widget-ext/0.1.0")
            assert download.status_code == 200
            assert download.content == b"fake vsix content"

            # never appears in the PEP 503 index — its own namespace only
            root_index = http.get("/simple/")
            assert "little-widget-ext" not in root_index.text


@requires_systemd_user
def test_fork_safety_gate_refuses_to_publish_a_real_fork_triggered_run(tmp_path, workshop_env):
    with _Session(workshop_env):
        project_id = "little-widget"
        _bootstrap(tmp_path, workshop_env, project_id)

        fork_run = _job(workshop_env, "build.run", project_id, {
            "commitish": "master", "command": [sys.executable, "-c", "print('built from a fork PR')"],
            "from_fork": True,
        })
        assert fork_run["status"] == "succeeded", fork_run

        content = base64.b64encode(b"should never be published").decode()
        blocked = _job(workshop_env, "build.publish_pypi", project_id, {
            "build_run_id": fork_run["result"]["id"], "name": "untrusted", "version": "1.0.0",
            "filename": "untrusted-1.0.0.whl", "content_base64": content,
        })
        assert blocked["status"] == "failed", blocked
        assert "fork-safety gate" in blocked["error"]

        with client_for_socket(workshop_env.paths.socket_path("build"), timeout=5.0) as http:
            download = http.get("/packages/untrusted-1.0.0.whl")
            assert download.status_code == 404
