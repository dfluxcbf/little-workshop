"""M8's end-to-end proof: a real, pre-existing git repository (never
touched by little-workshop, no `project.bootstrap` involved) gets
registered for real against a genuine `systemd --user` Registry unit —
via a real Orchestration-dispatched Job first, then via the actual
`workshop_project_import` MCP tool, proving both entry points the
milestone names reach the same real registration.
"""

from __future__ import annotations

import subprocess
import sys

from cli import client as cli_client
from common import systemd
from common.manifest import Manifest
from common.shell import DEFAULT_RUNNER
from core.app import build_app as build_core_app
from core.health import probe_health
from core.registry import PillarStatus, RegistryEntry
from core.supervisor import Supervisor
from helpers import UvicornThread, requires_systemd_user
from mcp_server.server import MCPServer
from orchestration.app import build_app as build_orchestration_app
from orchestration import db as orchestration_db


def _git(args: list[str], cwd) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _make_existing_repo(tmp_path, name: str):
    repo = tmp_path / name
    repo.mkdir()
    _git(["init", "-b", "master"], repo)
    _git(["config", "user.email", "t@example.com"], repo)
    _git(["config", "user.name", "T"], repo)
    (repo / "README.md").write_text(f"# {name}\n", encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-m", "pre-existing history, untouched by little-workshop"], repo)
    return repo


@requires_systemd_user
def test_orchestration_dispatches_a_real_import_job_for_a_pre_existing_repo(tmp_path, workshop_env):
    env = workshop_env.env
    systemd.start("registry", [sys.executable, "-m", "registry"], environment=env, runner=DEFAULT_RUNNER)
    try:
        probe = probe_health(workshop_env.paths.socket_path("registry"), timeout=5.0, max_retries=10)
        assert probe.ok, probe.error

        def socket_lookup(pillar: str):
            return workshop_env.paths.socket_path("registry") if pillar == "registry" else None

        conn = orchestration_db.connect(workshop_env.paths.pillar_db_path("orchestration"))
        orch_server = UvicornThread(build_orchestration_app(conn, socket_lookup), workshop_env.paths.socket_path("orchestration"))
        orch_server.start()
        try:
            repo = _make_existing_repo(tmp_path, "little-preexisting")
            before_head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout

            imported = cli_client.create_job(
                workshop_env.paths, type="project.import", target_pillar="registry", project_id="little-preexisting",
                params={"name": "little-preexisting", "path": str(repo)}, queue_key=None, depends_on=[],
                created_by="integration-test",
            )
            assert imported["status"] == "succeeded", imported
            assert imported["result"]["path"] == str(repo)

            listed = cli_client.list_projects(workshop_env.paths)
            assert [p["name"] for p in listed["projects"]] == ["little-preexisting"]

            # Never scaffolded, never committed to — the repo's own real
            # history is exactly what it was before import.
            after_head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout
            assert after_head == before_head
        finally:
            orch_server.stop()
            conn.close()
    finally:
        systemd.stop("registry", runner=DEFAULT_RUNNER)


@requires_systemd_user
def test_mcp_workshop_project_import_registers_a_real_repo_with_its_real_origin(tmp_path, workshop_env):
    env = workshop_env.env
    systemd.start("registry", [sys.executable, "-m", "registry"], environment=env, runner=DEFAULT_RUNNER)
    try:
        probe = probe_health(workshop_env.paths.socket_path("registry"), timeout=5.0, max_retries=10)
        assert probe.ok, probe.error

        def socket_lookup(pillar: str):
            return workshop_env.paths.socket_path("registry") if pillar == "registry" else None

        conn = orchestration_db.connect(workshop_env.paths.pillar_db_path("orchestration"))
        orch_server = UvicornThread(build_orchestration_app(conn, socket_lookup), workshop_env.paths.socket_path("orchestration"))
        orch_server.start()

        # mcp_server.client (unlike cli.client's create_job) resolves the
        # *orchestration* socket through Core's own registry too — Core
        # must know about "orchestration" itself, not just "registry", or
        # the MCP mutation fails "orchestration unreachable" before it
        # ever gets anywhere near the real target pillar.
        core_supervisor = Supervisor(paths=workshop_env.paths, manifest=Manifest())
        for pillar in ("registry", "orchestration"):
            core_supervisor.registry.set(
                RegistryEntry(name=pillar, socket_path=workshop_env.paths.socket_path(pillar), status=PillarStatus.HEALTHY)
            )
        core_server = UvicornThread(build_core_app(core_supervisor), workshop_env.paths.core_socket_path)
        core_server.start()
        try:
            repo = _make_existing_repo(tmp_path, "little-preexisting")
            bare = tmp_path / "upstream.git"
            subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True)
            _git(["remote", "add", "origin", str(bare)], repo)

            mcp = MCPServer(paths=workshop_env.paths)
            response = mcp.handle_request({
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "workshop_project_import", "arguments": {"name": "little-preexisting", "path": str(repo)}},
            })
            assert response["result"]["isError"] is False, response

            import json

            payload = json.loads(response["result"]["content"][0]["text"])
            assert payload["result"]["repo_url"] == str(bare)

            with_repo = cli_client.list_projects(workshop_env.paths)
            assert with_repo["projects"][0]["repo_url"] == str(bare)
        finally:
            orch_server.stop()
            core_server.stop()
            conn.close()
    finally:
        systemd.stop("registry", runner=DEFAULT_RUNNER)


@requires_systemd_user
def test_importing_the_same_name_twice_fails_the_second_time(tmp_path, workshop_env):
    env = workshop_env.env
    systemd.start("registry", [sys.executable, "-m", "registry"], environment=env, runner=DEFAULT_RUNNER)
    try:
        probe = probe_health(workshop_env.paths.socket_path("registry"), timeout=5.0, max_retries=10)
        assert probe.ok, probe.error

        def socket_lookup(pillar: str):
            return workshop_env.paths.socket_path("registry") if pillar == "registry" else None

        conn = orchestration_db.connect(workshop_env.paths.pillar_db_path("orchestration"))
        orch_server = UvicornThread(build_orchestration_app(conn, socket_lookup), workshop_env.paths.socket_path("orchestration"))
        orch_server.start()
        try:
            repo = _make_existing_repo(tmp_path, "little-preexisting")
            params = {"name": "little-preexisting", "path": str(repo)}

            first = cli_client.create_job(
                workshop_env.paths, type="project.import", target_pillar="registry", project_id="little-preexisting",
                params=params, queue_key=None, depends_on=[], created_by="integration-test",
            )
            assert first["status"] == "succeeded", first

            second = cli_client.create_job(
                workshop_env.paths, type="project.import", target_pillar="registry", project_id="little-preexisting",
                params=params, queue_key=None, depends_on=[], created_by="integration-test",
            )
            assert second["status"] == "failed", second
            assert "already registered" in second["error"]
        finally:
            orch_server.stop()
            conn.close()
    finally:
        systemd.stop("registry", runner=DEFAULT_RUNNER)


@requires_systemd_user
def test_import_fails_cleanly_when_path_is_not_a_git_repository(tmp_path, workshop_env):
    env = workshop_env.env
    systemd.start("registry", [sys.executable, "-m", "registry"], environment=env, runner=DEFAULT_RUNNER)
    try:
        probe = probe_health(workshop_env.paths.socket_path("registry"), timeout=5.0, max_retries=10)
        assert probe.ok, probe.error

        def socket_lookup(pillar: str):
            return workshop_env.paths.socket_path("registry") if pillar == "registry" else None

        conn = orchestration_db.connect(workshop_env.paths.pillar_db_path("orchestration"))
        orch_server = UvicornThread(build_orchestration_app(conn, socket_lookup), workshop_env.paths.socket_path("orchestration"))
        orch_server.start()
        try:
            not_a_repo = tmp_path / "just-a-directory"
            not_a_repo.mkdir()
            (not_a_repo / "README.md").write_text("no .git here\n", encoding="utf-8")

            imported = cli_client.create_job(
                workshop_env.paths, type="project.import", target_pillar="registry", project_id="not-a-repo",
                params={"name": "not-a-repo", "path": str(not_a_repo)}, queue_key=None, depends_on=[],
                created_by="integration-test",
            )
            assert imported["status"] == "failed", imported

            listed = cli_client.list_projects(workshop_env.paths)
            assert listed["projects"] == []
        finally:
            orch_server.stop()
            conn.close()
    finally:
        systemd.stop("registry", runner=DEFAULT_RUNNER)


@requires_systemd_user
def test_importing_a_name_already_taken_by_a_bootstrapped_project_fails_the_same_way(tmp_path, workshop_env):
    """The existing duplicate-name proof only exercises import-vs-import.
    Registry & Portfolio's own registry is one shared namespace regardless
    of which of the two entry points (`project.bootstrap`, `project.
    import`) created the first row — a real proof that the check is
    keyed on the name itself, not on which path produced it."""
    env = workshop_env.env
    systemd.start("registry", [sys.executable, "-m", "registry"], environment=env, runner=DEFAULT_RUNNER)
    try:
        probe = probe_health(workshop_env.paths.socket_path("registry"), timeout=5.0, max_retries=10)
        assert probe.ok, probe.error

        def socket_lookup(pillar: str):
            return workshop_env.paths.socket_path("registry") if pillar == "registry" else None

        conn = orchestration_db.connect(workshop_env.paths.pillar_db_path("orchestration"))
        orch_server = UvicornThread(build_orchestration_app(conn, socket_lookup), workshop_env.paths.socket_path("orchestration"))
        orch_server.start()
        try:
            dest_dir = tmp_path / "workdir"
            dest_dir.mkdir()
            bootstrapped = cli_client.create_job(
                workshop_env.paths, type="project.bootstrap", target_pillar="registry", project_id="little-widget",
                params={"name": "little-widget", "dest_dir": str(dest_dir)}, queue_key=None, depends_on=[],
                created_by="integration-test",
            )
            assert bootstrapped["status"] == "succeeded", bootstrapped

            pre_existing = _make_existing_repo(tmp_path, "unrelated-repo-name")
            blocked = cli_client.create_job(
                workshop_env.paths, type="project.import", target_pillar="registry", project_id="little-widget",
                params={"name": "little-widget", "path": str(pre_existing)}, queue_key=None, depends_on=[],
                created_by="integration-test",
            )
            assert blocked["status"] == "failed", blocked
            assert "already registered" in blocked["error"]

            listed = cli_client.list_projects(workshop_env.paths)
            assert len(listed["projects"]) == 1
            assert listed["projects"][0]["path"] == str(dest_dir / "little-widget")
        finally:
            orch_server.stop()
            conn.close()
    finally:
        systemd.stop("registry", runner=DEFAULT_RUNNER)
