"""M10's end-to-end proof: Registry & Portfolio bootstraps a real
project (a genuine `systemd --user` unit), Requirements & Quality (also
real) tracks a test suite and a requirement against it, and
`requirements.run_tests` genuinely provisions an isolated worktree
through Deployment & Servers' own real Pillar Interface (a third real
`systemd --user` unit) and runs a real `pytest` inside it — never the
project's own working checkout — ingesting the real JUnit-XML report
that produces. The definition-of-done report reflects all of it.
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
from orchestration import db as orchestration_db
from orchestration.app import build_app as build_orchestration_app


PILLARS = ("registry", "deployment", "requirements")


class _Session:
    """Real `registry`, `deployment`, and `requirements` systemd --user
    units, plus a real Core+Orchestration pair in-process — the same
    simplification test_version_end_to_end.py's own `_Session`-style
    fixtures already use for Core/Orchestration specifically."""

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


def _job(workshop_env, job_type, project_id, params, *, timeout=30.0):
    return cli_client.create_job(
        workshop_env.paths, type=job_type, target_pillar="requirements", project_id=project_id,
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
def test_requirement_lifecycle_and_definition_of_done_report_against_real_pillars(tmp_path, workshop_env):
    with _Session(workshop_env):
        project_id = "little-widget"
        _bootstrap(tmp_path, workshop_env, project_id)

        suite = _job(workshop_env, "requirements.create_test_suite", project_id, {"name": "core"})
        assert suite["status"] == "succeeded", suite
        suite_id = suite["result"]["id"]

        requirement = _job(workshop_env, "requirements.create_requirement", project_id, {
            "key": "REQ-1", "title": "the widget actually works",
        })
        assert requirement["status"] == "succeeded", requirement
        requirement_id = requirement["result"]["id"]

        link = _job(workshop_env, "requirements.link_requirement_to_suite", project_id, {
            "requirement_id": requirement_id, "suite_id": suite_id,
        })
        assert link["status"] == "succeeded", link

        # Not done yet: not tested, not published.
        report = cli_client.requirements_report(workshop_env.paths, project_id)
        assert report["requirements"][0]["done"] is False

        record = _job(workshop_env, "requirements.record_test_result", project_id, {
            "suite_id": suite_id, "test_name": "test_widget_works", "verdict": "passed",
        })
        assert record["status"] == "succeeded", record

        publish = _job(workshop_env, "requirements.publish_requirement", project_id, {"requirement_id": requirement_id})
        assert publish["status"] == "succeeded", publish
        assert publish["result"]["status"] == "published"

        report = cli_client.requirements_report(workshop_env.paths, project_id)
        assert report["requirements"][0]["done"] is True
        assert report["done_count"] == 1

        listed = cli_client.requirements_list_requirements(workshop_env.paths, project_id)
        assert listed["source"] == "daemon"
        assert [r["key"] for r in listed["requirements"]] == ["REQ-1"]


@requires_systemd_user
def test_run_tests_provisions_a_real_worktree_via_deployment_and_ingests_real_pytest_results(tmp_path, workshop_env):
    with _Session(workshop_env):
        project_id = "little-widget"
        project_dir = _bootstrap(tmp_path, workshop_env, project_id)

        # A real test file, committed to `dev` — run_tests must see it in
        # the isolated worktree Deployment & Servers provisions, never in
        # this checkout directly.
        (project_dir / "test_sample.py").write_text(
            "def test_pass():\n    assert True\n\n\ndef test_fail():\n    assert False\n",
            encoding="utf-8",
        )
        subprocess.run(["git", "add", "-A"], cwd=project_dir, check=True)
        subprocess.run(["git", "commit", "-m", "add a real test file"], cwd=project_dir, check=True, capture_output=True)
        subprocess.run(["git", "checkout", "dev"], cwd=project_dir, check=True, capture_output=True)
        subprocess.run(["git", "merge", "--ff-only", "master"], cwd=project_dir, check=True, capture_output=True)

        suite = _job(workshop_env, "requirements.create_test_suite", project_id, {"name": "core"})
        suite_id = suite["result"]["id"]

        ran = _job(workshop_env, "requirements.run_tests", project_id, {
            "suite_id": suite_id, "commitish": "dev",
        }, timeout=120.0)
        assert ran["status"] == "succeeded", ran
        assert ran["result"]["ran"] == 2

        results = cli_client.requirements_list_test_results(workshop_env.paths, project_id, suite_id)
        by_name = {r["test_name"].rsplit("::", 1)[-1]: r for r in results["test_results"]}
        assert by_name["test_pass"]["verdict"] == "passed"
        assert by_name["test_pass"]["source"] == "pytest"
        assert by_name["test_fail"]["verdict"] == "failed"

        # The project's own working checkout is untouched by the sandboxed
        # run — no stray JUnit report left behind in it.
        assert list(project_dir.glob(".requirements-junit-*.xml")) == []


@requires_systemd_user
def test_import_junit_ingests_an_existing_report_against_a_real_requirements_unit(tmp_path, workshop_env):
    with _Session(workshop_env):
        project_id = "little-widget"
        _bootstrap(tmp_path, workshop_env, project_id)

        suite = _job(workshop_env, "requirements.create_test_suite", project_id, {"name": "external"})
        suite_id = suite["result"]["id"]

        xml_report = (
            '<testsuite><testcase classname="ext" name="already_ran">'
            '<failure message="broke elsewhere"/></testcase></testsuite>'
        )
        imported = _job(workshop_env, "requirements.import_junit", project_id, {
            "suite_id": suite_id, "xml": xml_report,
        })
        assert imported["status"] == "succeeded", imported
        assert imported["result"]["imported"] == 1

        results = cli_client.requirements_list_test_results(workshop_env.paths, project_id, suite_id)
        assert results["test_results"][0]["source"] == "junit"
        assert results["test_results"][0]["verdict"] == "failed"


@requires_systemd_user
def test_a_published_requirement_can_no_longer_be_edited(tmp_path, workshop_env):
    """A locked record, for real: once published, `requirements.
    update_requirement` refuses — the definition-of-done report's own
    `published` status would otherwise mean nothing if the text behind
    it could still silently change."""
    with _Session(workshop_env):
        project_id = "little-widget"
        _bootstrap(tmp_path, workshop_env, project_id)

        requirement = _job(workshop_env, "requirements.create_requirement", project_id, {
            "key": "REQ-1", "title": "the widget actually works",
        })
        requirement_id = requirement["result"]["id"]
        publish = _job(workshop_env, "requirements.publish_requirement", project_id, {"requirement_id": requirement_id})
        assert publish["status"] == "succeeded", publish

        edit = _job(workshop_env, "requirements.update_requirement", project_id, {
            "requirement_id": requirement_id, "title": "a rewritten title after publishing",
        })
        assert edit["status"] == "failed", edit
        assert "draft" in edit["error"]

        fetched = cli_client.requirements_get_requirement(workshop_env.paths, requirement_id)
        assert fetched["requirement"]["title"] == "the widget actually works"


@requires_systemd_user
def test_unlinking_a_requirement_from_a_suite_removes_it_from_the_report(tmp_path, workshop_env):
    with _Session(workshop_env):
        project_id = "little-widget"
        _bootstrap(tmp_path, workshop_env, project_id)

        suite = _job(workshop_env, "requirements.create_test_suite", project_id, {"name": "core"})
        suite_id = suite["result"]["id"]
        requirement = _job(workshop_env, "requirements.create_requirement", project_id, {
            "key": "REQ-1", "title": "the widget actually works",
        })
        requirement_id = requirement["result"]["id"]

        link = _job(workshop_env, "requirements.link_requirement_to_suite", project_id, {
            "requirement_id": requirement_id, "suite_id": suite_id,
        })
        assert link["status"] == "succeeded", link
        linked_report = cli_client.requirements_report(workshop_env.paths, project_id)
        assert linked_report["requirements"][0]["suite_ids"] == [suite_id]

        unlink = _job(workshop_env, "requirements.unlink_requirement_from_suite", project_id, {
            "requirement_id": requirement_id, "suite_id": suite_id,
        })
        assert unlink["status"] == "succeeded", unlink

        unlinked_report = cli_client.requirements_report(workshop_env.paths, project_id)
        assert unlinked_report["requirements"][0]["suite_ids"] == []
