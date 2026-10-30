"""Build's run and publish jobs as checklists: reported keys equal the declared ones, and a refused
publish or a failing build command is reported failed with its message."""

from __future__ import annotations

import base64

import pytest
from fastapi.testclient import TestClient

from build import app as app_module
from build import db, runner
from build.app import build_app
from build.runner import RunOutcome, run_build
from common.job_steps import StepReporter, reporting
from common.paths import Paths
from common.shell import CommandResult
from common.step_specs import steps_for


class Recorder:
    def __init__(self):
        self.events: list[tuple[str, str, str | None]] = []

    def __call__(self, method, path, json=None):
        self.events.append((path.rsplit("/", 1)[-1], json["state"], json.get("detail")))
        return {}

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
    connection = db.connect(tmp_path / "build.db")
    yield connection
    connection.close()


@pytest.fixture
def recorder(monkeypatch):
    rec = Recorder()
    monkeypatch.setattr(app_module, "StepReporter", lambda paths, job_id: StepReporter(None, job_id, call=rec))
    return rec


class FakeShell:
    def __init__(self, returncode=0):
        self.returncode = returncode

    def run(self, argv, cwd=None, timeout=None):
        return CommandResult(self.returncode, "out", "err" if self.returncode else "")


def _patch_worktrees(monkeypatch, released):
    monkeypatch.setattr(runner, "provision_worktree", lambda paths, project_id, commitish: ("w1", "/tmp"))
    monkeypatch.setattr(runner, "release_worktree", lambda paths, project_id, worktree_id: released.append(worktree_id))


# --- REQ-NONE: a job's checklist is declared by its pillar ---


def test_run_build_reports_the_declared_run_steps(paths, monkeypatch):
    rec, released = Recorder(), []
    _patch_worktrees(monkeypatch, released)
    with reporting(StepReporter(None, "j", call=rec)):
        run_build(paths, "p1", "HEAD", ["true"], runner=FakeShell())
    declared = [s.key for s in steps_for("build.run")]
    assert rec.keys("succeeded") == [k for k in declared if k != "save_result"]
    assert released == ["w1"]


def test_a_failing_build_command_fails_its_step_but_still_returns_the_outcome(paths, monkeypatch):
    rec, released = Recorder(), []
    _patch_worktrees(monkeypatch, released)
    with reporting(StepReporter(None, "j", call=rec)):
        outcome = run_build(paths, "p1", "HEAD", ["false"], runner=FakeShell(returncode=2))
    assert outcome.exit_code == 2
    assert rec.keys("failed") == ["run_command"]
    assert "exited 2" in next(d for k, st, d in rec.events if st == "failed")
    assert rec.keys("succeeded")[-1] == "release" and released == ["w1"]


def test_build_run_job_reports_save_result(conn, paths, recorder, monkeypatch):
    monkeypatch.setattr(app_module, "run_build", lambda *a, **kw: RunOutcome(exit_code=0, log="ok"))
    client = TestClient(build_app(conn, paths))
    client.post("/v1/jobs/build.run", json={"job_id": "j", "project_id": "p1", "params": {"commitish": "HEAD", "command": ["true"]}})
    assert recorder.keys("succeeded") == ["save_result"]


def _publish(client, build_run_id, **overrides):
    params = {"build_run_id": build_run_id, "filename": "pkg-1.0.0.whl", "content_base64": base64.b64encode(b"x").decode(), **overrides}
    return client.post("/v1/jobs/build.publish_pypi", json={"job_id": "j", "project_id": "p1", "params": params}).json()


def _successful_run(client, monkeypatch):
    monkeypatch.setattr(app_module, "run_build", lambda *a, **kw: RunOutcome(exit_code=0, log="ok"))
    return client.post("/v1/jobs/build.run", json={
        "job_id": "j", "project_id": "p1", "params": {"commitish": "HEAD", "command": ["true"]},
    }).json()["result"]["id"]


def test_publish_reports_exactly_the_declared_steps(conn, paths, recorder, monkeypatch):
    client = TestClient(build_app(conn, paths))
    run_id = _successful_run(client, monkeypatch)
    recorder.events.clear()
    assert _publish(client, run_id)["outcome"] == "succeeded"
    assert recorder.keys("succeeded") == [s.key for s in steps_for("build.publish_pypi")]


def test_a_publish_from_a_fork_run_fails_the_gate_step_with_its_message(conn, paths, recorder, monkeypatch):
    client = TestClient(build_app(conn, paths))
    monkeypatch.setattr(app_module, "run_build", lambda *a, **kw: RunOutcome(exit_code=0, log="ok"))
    run_id = client.post("/v1/jobs/build.run", json={
        "job_id": "j", "project_id": "p1", "params": {"commitish": "HEAD", "command": ["true"], "from_fork": True},
    }).json()["result"]["id"]
    recorder.events.clear()
    answer = _publish(client, run_id)
    assert answer["outcome"] == "failed" and "fork-safety gate" in answer["error"]
    assert recorder.keys("failed") == ["check_build_run"]
    assert "fork-safety gate" in next(d for k, st, d in recorder.events if st == "failed")


def test_a_filename_without_a_version_fails_the_input_step(conn, paths, recorder):
    client = TestClient(build_app(conn, paths))
    answer = _publish(client, "r1", filename="pkg.whl")
    assert answer["outcome"] == "failed"
    assert recorder.keys("failed") == ["check_input"]


def test_delete_package_keeps_the_default_step():
    assert [s.key for s in steps_for("build.delete_package")] == ["run"]
