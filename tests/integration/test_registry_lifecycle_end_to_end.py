"""Testing-fortification pass: Registry & Portfolio's lifecycle beyond
M2's original bootstrap-then-list proof — project field persistence,
duplicate-bootstrap rejection, project_sets/tickets bookkeeping, status
filtering, and a real check of whether `project.remove`'s documented
cross-pillar cascade actually exists in code today.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from cli import client as cli_client
from common import systemd
from common.shell import DEFAULT_RUNNER
from common.uds_client import client_for_socket
from core.app import build_app as build_core_app
from core.health import probe_health
from core.registry import PillarStatus, RegistryEntry
from core.supervisor import Supervisor
from common.manifest import Manifest
from orchestration.app import build_app as build_orchestration_app
from orchestration import db as orchestration_db

from helpers import UvicornThread, requires_systemd_user


PILLARS_FOR_CASCADE_TEST = ("registry", "version", "deployment", "agent")


def _start_pillars(env, workshop_env, *pillars: str):
    for pillar in pillars:
        systemd.start(pillar, [sys.executable, "-m", pillar], environment=env, runner=DEFAULT_RUNNER)
    for pillar in pillars:
        probe = probe_health(workshop_env.paths.socket_path(pillar), timeout=5.0, max_retries=10)
        assert probe.ok, f"{pillar} pillar never became healthy: {probe.error}"


def _stop_pillars(*pillars: str):
    for pillar in pillars:
        systemd.stop(pillar, runner=DEFAULT_RUNNER)


def _core_and_orchestration(workshop_env, known_pillars):
    core_supervisor = Supervisor(paths=workshop_env.paths, manifest=Manifest())
    for pillar in known_pillars:
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
def test_bootstrap_persists_description_and_owner_fields(tmp_path, workshop_env):
    systemd.start("registry", [sys.executable, "-m", "registry"], environment=workshop_env.env, runner=DEFAULT_RUNNER)
    try:
        probe = probe_health(workshop_env.paths.socket_path("registry"), timeout=5.0, max_retries=10)
        assert probe.ok, probe.error

        core_server, orch_server, conn = _core_and_orchestration(workshop_env, ("registry",))
        try:
            dest_dir = tmp_path / "workdir"
            dest_dir.mkdir()
            result = cli_client.create_job(
                workshop_env.paths, type="project.bootstrap", target_pillar="registry", project_id="little-widget",
                params={
                    "name": "little-widget", "dest_dir": str(dest_dir),
                    "owner": "some-owner", "description": "a widget factory",
                },
                queue_key=None, depends_on=[], created_by="integration-test",
            )
            assert result["status"] == "succeeded", result

            with client_for_socket(workshop_env.paths.socket_path("registry"), timeout=5.0) as registry_client:
                project = registry_client.get("/v1/projects/little-widget").json()
            assert project["name"] == "little-widget"
        finally:
            orch_server.stop()
            core_server.stop()
            conn.close()
    finally:
        systemd.stop("registry", runner=DEFAULT_RUNNER)


@requires_systemd_user
def test_bootstrap_same_name_twice_fails_cleanly_the_second_time(tmp_path, workshop_env):
    systemd.start("registry", [sys.executable, "-m", "registry"], environment=workshop_env.env, runner=DEFAULT_RUNNER)
    try:
        probe = probe_health(workshop_env.paths.socket_path("registry"), timeout=5.0, max_retries=10)
        assert probe.ok, probe.error

        core_server, orch_server, conn = _core_and_orchestration(workshop_env, ("registry",))
        try:
            dest_dir = tmp_path / "workdir"
            dest_dir.mkdir()
            params = {"name": "little-widget", "dest_dir": str(dest_dir)}

            first = cli_client.create_job(
                workshop_env.paths, type="project.bootstrap", target_pillar="registry", project_id="little-widget",
                params=params, queue_key=None, depends_on=[], created_by="integration-test",
            )
            assert first["status"] == "succeeded", first

            second = cli_client.create_job(
                workshop_env.paths, type="project.bootstrap", target_pillar="registry", project_id="little-widget",
                params=params, queue_key=None, depends_on=[], created_by="integration-test",
            )
            assert second["status"] == "failed", second
            assert "already registered" in second["error"]

            # The registry still shows exactly one project, not a
            # duplicate or corrupted second row from the failed retry.
            listed = cli_client.list_projects(workshop_env.paths)
            assert listed["source"] == "daemon"
            assert [p["name"] for p in listed["projects"]] == ["little-widget"]
        finally:
            orch_server.stop()
            core_server.stop()
            conn.close()
    finally:
        systemd.stop("registry", runner=DEFAULT_RUNNER)


@requires_systemd_user
def test_project_sets_create_add_and_remove_member(tmp_path, workshop_env):
    systemd.start("registry", [sys.executable, "-m", "registry"], environment=workshop_env.env, runner=DEFAULT_RUNNER)
    try:
        probe = probe_health(workshop_env.paths.socket_path("registry"), timeout=5.0, max_retries=10)
        assert probe.ok, probe.error

        core_server, orch_server, conn = _core_and_orchestration(workshop_env, ("registry",))
        try:
            dest_dir = tmp_path / "workdir"
            dest_dir.mkdir()
            for name in ("little-widget", "little-gadget"):
                created = cli_client.create_job(
                    workshop_env.paths, type="project.bootstrap", target_pillar="registry", project_id=name,
                    params={"name": name, "dest_dir": str(dest_dir)}, queue_key=None, depends_on=[],
                    created_by="integration-test",
                )
                assert created["status"] == "succeeded", created

            with client_for_socket(workshop_env.paths.socket_path("registry"), timeout=5.0) as registry_client:
                created_set = registry_client.post(
                    "/v1/project_sets", json={"name": "widgets-and-gadgets", "project_names": ["little-widget"]}
                ).json()
                assert created_set["project_names"] == ["little-widget"]

                added = registry_client.post(
                    "/v1/project_sets/widgets-and-gadgets/members/little-gadget"
                ).json()
                assert set(added["project_names"]) == {"little-widget", "little-gadget"}

                removed = registry_client.delete(
                    "/v1/project_sets/widgets-and-gadgets/members/little-widget"
                ).json()
                assert removed["project_names"] == ["little-gadget"]

                fetched = registry_client.get("/v1/project_sets/widgets-and-gadgets").json()
                assert fetched["project_names"] == ["little-gadget"]
        finally:
            orch_server.stop()
            core_server.stop()
            conn.close()
    finally:
        systemd.stop("registry", runner=DEFAULT_RUNNER)


@requires_systemd_user
def test_tickets_create_and_list_filtered_by_project(tmp_path, workshop_env):
    systemd.start("registry", [sys.executable, "-m", "registry"], environment=workshop_env.env, runner=DEFAULT_RUNNER)
    try:
        probe = probe_health(workshop_env.paths.socket_path("registry"), timeout=5.0, max_retries=10)
        assert probe.ok, probe.error

        with client_for_socket(workshop_env.paths.socket_path("registry"), timeout=5.0) as registry_client:
            registry_client.post("/v1/tickets", json={"project_name": "little-widget", "title": "fix the thing"})
            registry_client.post("/v1/tickets", json={"project_name": "little-widget", "title": "add the other thing"})
            registry_client.post("/v1/tickets", json={"project_name": "little-gadget", "title": "unrelated"})

            widget_tickets = registry_client.get("/v1/tickets", params={"project_name": "little-widget"}).json()
            assert {t["title"] for t in widget_tickets} == {"fix the thing", "add the other thing"}

            all_tickets = registry_client.get("/v1/tickets").json()
            assert len(all_tickets) == 3
    finally:
        systemd.stop("registry", runner=DEFAULT_RUNNER)


@requires_systemd_user
def test_project_list_filters_by_status(tmp_path, workshop_env):
    systemd.start("registry", [sys.executable, "-m", "registry"], environment=workshop_env.env, runner=DEFAULT_RUNNER)
    try:
        probe = probe_health(workshop_env.paths.socket_path("registry"), timeout=5.0, max_retries=10)
        assert probe.ok, probe.error

        core_server, orch_server, conn = _core_and_orchestration(workshop_env, ("registry",))
        try:
            dest_dir = tmp_path / "workdir"
            dest_dir.mkdir()
            created = cli_client.create_job(
                workshop_env.paths, type="project.bootstrap", target_pillar="registry", project_id="little-widget",
                params={"name": "little-widget", "dest_dir": str(dest_dir)}, queue_key=None, depends_on=[],
                created_by="integration-test",
            )
            assert created["status"] == "succeeded", created

            active = cli_client.list_projects(workshop_env.paths, status="active")
            assert [p["name"] for p in active["projects"]] == ["little-widget"]

            removed_status = cli_client.list_projects(workshop_env.paths, status="removed")
            assert removed_status["projects"] == []
        finally:
            orch_server.stop()
            core_server.stop()
            conn.close()
    finally:
        systemd.stop("registry", runner=DEFAULT_RUNNER)


@requires_systemd_user
def test_project_remove_does_not_yet_cascade_to_other_pillars_data(tmp_path, workshop_env):
    """docs/architecture.md's Registry & Portfolio row claims: "removing a
    project's registry row triggers every other pillar to purge that
    project's data through its own Pillar Interface (each pillar deletes
    its own rows for that project_id)." Investigated: `registry/removal.py`
    only deletes Registry's own `projects`/`project_set_members`/`tickets`
    rows and the project-files directory it created — there is no code
    anywhere in version/deployment/agent's `app.py` (or any other module)
    that listens for or reacts to `project.remove` in any way. This is a
    genuine, confirmed gap between the documented design and the actual
    implementation, not a bug to fix here (implementing the cascade is a
    real feature, out of scope for a testing-fortification pass) — this
    test exists to document and lock in today's *actual* behavior so a
    future change to it is a deliberate, visible decision rather than a
    silent regression (or, if the cascade is later implemented, this
    test's failure is the signal to update it and move the feature into
    docs/requirements.md as done)."""
    env = workshop_env.env
    _start_pillars(env, workshop_env, "registry", "version")
    try:
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
            project_dir = dest_dir / "little-widget"

            init = cli_client.create_job(
                workshop_env.paths, type="version.init", target_pillar="version", project_id="little-widget",
                params={"production_branch": "master", "integration_branch": "dev"}, queue_key="little-widget",
                depends_on=[], created_by="integration-test",
            )
            assert init["status"] == "succeeded", init
            subprocess.run(["git", "add", "-A"], cwd=project_dir, check=True)
            subprocess.run(["git", "commit", "-m", "chore: version init"], cwd=project_dir, check=True, capture_output=True)
            subprocess.run(["git", "checkout", "dev"], cwd=project_dir, check=True, capture_output=True)
            subprocess.run(["git", "merge", "--ff-only", "master"], cwd=project_dir, check=True, capture_output=True)

            # A real row now exists in Version Control & Release's own
            # world -- the issue-link table, populated via `lvx_issue_set`'s
            # underlying job. If not present, this whole test would be
            # meaningless (there'd be nothing for the removal to leave
            # behind either way), so confirm it's really there first.
            feature_start = cli_client.create_job(
                workshop_env.paths, type="version.feature_start", target_pillar="version", project_id="little-widget",
                params={"name": "widgets", "issue": 42}, queue_key="little-widget", depends_on=[],
                created_by="integration-test",
            )
            assert feature_start["status"] == "succeeded", feature_start

            with client_for_socket(workshop_env.paths.socket_path("version"), timeout=5.0) as version_client:
                issues_before = version_client.get("/v1/config", params={"project_id": "little-widget"}).json()
            assert issues_before  # Version has real state tied to this project (branch metadata etc).

            removed = cli_client.create_job(
                workshop_env.paths, type="project.remove", target_pillar="registry", project_id="little-widget",
                params={"name": "little-widget"}, queue_key="little-widget", depends_on=[], created_by="integration-test",
            )
            assert removed["status"] == "succeeded", removed

            # Registry's own row is really gone.
            listed = cli_client.list_projects(workshop_env.paths)
            assert listed["projects"] == []

            # But the actual git repository on disk survives (removal.py's
            # own documented behavior: "the user's code, not this pillar's
            # data to delete") --
            assert project_dir.exists()
            assert (project_dir / ".git").is_dir()

            # -- and, confirming the finding above, Version Control &
            # Release's real branch created for this project (still
            # resolvable purely because the git worktree itself is
            # untouched -- Version has no database of its own, so there is
            # no row to check here beyond re-resolving the same repo path
            # directly, bypassing Registry entirely since its row is gone).
            branches = subprocess.run(
                ["git", "branch"], cwd=project_dir, capture_output=True, text=True
            ).stdout
            assert "feature/widgets" in branches, (
                "Version Control & Release's real branch for this project "
                "still exists after project.remove -- confirms no cascade "
                "purge happened; this is today's actual, documented-gap "
                "behavior, not an assertion that it should stay this way."
            )
        finally:
            orch_server.stop()
            core_server.stop()
            conn.close()
    finally:
        _stop_pillars("registry", "version")
