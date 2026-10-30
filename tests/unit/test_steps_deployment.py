"""Deployment's deploy and pillar-redeploy jobs as checklists: the keys the code reports are the keys
the spec declares, in order, and a failing action is reported failed with its message."""

from __future__ import annotations

import pytest
from test_deployment_flow import FakeRunner, make_flow, make_repo, manifest
from test_deployment_self_redeploy import FakeRunner as PillarRunner
from test_deployment_self_redeploy import make_flow as make_pillar_flow
from test_deployment_self_redeploy import make_workshop_repo

from common.paths import Paths
from common.job_steps import StepReporter, reporting
from common.step_specs import steps_for
from deployment import db
from deployment.flow import DeploymentError


class Recorder:
    """A StepReporter `call` that records every POST to /v1/jobs/<id>/steps/<key>."""

    def __init__(self):
        self.events: list[tuple[str, str, str | None]] = []

    def __call__(self, method, path, json=None):
        key = path.rsplit("/", 1)[-1]
        self.events.append((key, json["state"], json.get("detail")))
        return {}

    def reporter(self):
        return StepReporter(None, "job", call=self)

    def keys(self, state):
        return [key for key, st, _ in self.events if st == state]


# --- REQ-000000310: Every job has a checklist of its actions ---

@pytest.fixture
def paths(tmp_path):
    p = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    p.ensure_layout()
    return p


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "deployment.db")
    yield connection
    connection.close()


# --- REQ-NONE: a job's checklist is declared by its pillar ---


def test_deploy_reports_exactly_the_declared_steps_in_order(paths, conn, tmp_path):
    recorder = Recorder()
    flow = make_flow(paths, FakeRunner())
    with reporting(recorder.reporter()):
        flow.up(conn, "little-meals", manifest(), make_repo(tmp_path), "main")
    assert recorder.keys("succeeded") == [s.key for s in steps_for("deployment.deploy")]
    assert recorder.keys("failed") == []


def test_deploy_with_a_confirm_gate_reports_the_window(paths, conn, tmp_path):
    recorder = Recorder()
    flow = make_flow(paths, FakeRunner())
    with reporting(recorder.reporter()):
        flow.up(conn, "little-meals", manifest(confirm_timeout_seconds=60), make_repo(tmp_path), "main")
    detail = next(d for k, st, d in recorder.events if k == "confirm_gate" and st == "succeeded")
    assert "awaiting confirmation" in detail


def test_a_failed_install_fails_its_step_and_leaves_the_later_ones_unreported(paths, conn, tmp_path):
    recorder = Recorder()
    flow = make_flow(paths, FakeRunner(install_ok=False))
    with reporting(recorder.reporter()), pytest.raises(DeploymentError):
        flow.up(conn, "little-meals", manifest(install=["bazel", "run", "//:install"]), make_repo(tmp_path), "main")
    assert recorder.keys("failed") == ["install"]
    assert "install failed" in next(d for k, st, d in recorder.events if st == "failed")
    assert recorder.keys("running")[-1] == "install"


def test_an_unknown_branch_fails_the_fetch_step(paths, conn, tmp_path):
    recorder = Recorder()
    flow = make_flow(paths, FakeRunner())
    with reporting(recorder.reporter()), pytest.raises(DeploymentError):
        flow.up(conn, "little-meals", manifest(), make_repo(tmp_path), "nope")
    assert recorder.keys("failed") == ["fetch_branch"]


def test_redeploy_pillar_reports_exactly_the_declared_steps_in_order(paths, conn, tmp_path):
    recorder = Recorder()
    flow = make_pillar_flow(paths, PillarRunner())
    with reporting(recorder.reporter()):
        flow.redeploy_pillar(conn, "registry", make_workshop_repo(tmp_path, pillar="registry"), "main")
    assert recorder.keys("succeeded") == [s.key for s in steps_for("deployment.redeploy_pillar")]


def test_a_commit_that_does_not_import_fails_the_smoke_check(paths, conn, tmp_path):
    recorder = Recorder()
    runner = PillarRunner()
    flow = make_pillar_flow(paths, runner)
    real = runner.run

    def failing(argv, timeout=None, cwd=None):
        result = real(argv, timeout=timeout, cwd=cwd)
        if argv[:2] == ["python3", "-c"]:
            from common.shell import CommandResult
            return CommandResult(1, "", "ImportError: boom")
        return result

    runner.run = failing
    with reporting(recorder.reporter()), pytest.raises(DeploymentError):
        flow.redeploy_pillar_direct(conn, "registry", make_workshop_repo(tmp_path, pillar="registry"), "main")
    assert recorder.keys("failed") == ["smoke_check"]
    assert "does not even import" in next(d for k, st, d in recorder.events if st == "failed")


def test_types_that_are_one_action_keep_the_default_step():
    assert [s.key for s in steps_for("deployment.stop")] == ["run"]
