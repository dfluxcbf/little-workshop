"""A Version Job the pillar answered `accepted` runs on a thread that dies with the process. One that a
restart cut is failed at the next start, with an incident, since its repository may be part-way
through it; it is never run again on its own."""

from __future__ import annotations

import json
import threading

import pytest
from fastapi.testclient import TestClient
from version_repo_helper import make_repo

from common import serving
from common.paths import Paths
from version import app as version_app
from version.common import CommandResult
from version.resolve import ProjectResolutionError


class Services:
    """Orchestration's completion callback, as `PillarReleaseServices.complete` makes it."""

    def __init__(self, takes: bool = True):
        self.takes = takes  # False: Orchestration did not take the answer (it was down)
        self.calls: list[dict] = []
        self.completed = threading.Event()

    def complete(self, job_id, *, succeeded, result, error):
        self.calls.append({"job_id": job_id, "succeeded": succeeded, "result": result, "error": error})
        self.completed.set()
        return self.takes


@pytest.fixture
def paths(tmp_path):
    return Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")


@pytest.fixture
def reported(monkeypatch):
    sent = []
    monkeypatch.setattr(version_app, "report_problem", lambda target, **kw: sent.append(kw))
    return sent


def _app(paths, tmp_path, services):
    repo = make_repo(tmp_path)

    def resolve(_paths, project_id):
        if project_id != "little-widget":
            raise ProjectResolutionError("unknown")
        return repo

    return version_app.build_app(paths, resolve=resolve, release_services=services)


def _running(paths) -> dict:
    path = version_app.running_jobs_path(paths)
    return json.loads(path.read_text()) if path.exists() else {}


# --- REQ-000000410: A Version Job cut by a restart is failed at once ---


def test_a_release_is_kept_as_running_until_it_reports_its_end(paths, tmp_path, monkeypatch):
    release = threading.Event()

    def run_release(repo_root, project_id, params, services, job_id):
        release.wait(5)
        return {"released": "1.0.0"}

    monkeypatch.setattr(version_app, "run_release", run_release)
    services = Services()
    client = TestClient(_app(paths, tmp_path, services))

    answer = client.post("/v1/jobs/version.release", json={"job_id": "r1", "project_id": "little-widget", "params": {}}).json()

    assert answer["outcome"] == "accepted" and list(_running(paths)) == ["r1"]
    release.set()
    assert services.completed.wait(5)
    assert services.calls[0]["succeeded"] is True and _running(paths) == {}


def test_an_asking_job_that_answered_accepted_is_kept_as_running_until_it_ends(paths, tmp_path, monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(version_app, "SYNC_GRACE_SECONDS", 0.05)

    def waits(repo, params):  # waiting for the user's answer to a question
        release.wait(5)
        return CommandResult(True, "finished late", {})

    monkeypatch.setitem(version_app.JOB_HANDLERS, "version.feature_finish", waits)
    services = Services()
    client = TestClient(_app(paths, tmp_path, services))

    answer = client.post("/v1/jobs/version.feature_finish", json={"job_id": "f1", "project_id": "little-widget", "params": {}}).json()

    assert answer["outcome"] == "accepted" and list(_running(paths)) == ["f1"]
    release.set()
    assert services.completed.wait(5)
    assert _running(paths) == {}


def test_at_start_a_job_the_previous_process_left_running_is_failed_and_reported(paths, tmp_path, reported):
    version_app.RunningJobs(version_app.running_jobs_path(paths)).add("r9", "version.release", "little-widget")
    services = Services()

    assert _app(paths, tmp_path, services).state.settle_interrupted_jobs(tries=0) == []

    ((call),) = services.calls
    assert call["job_id"] == "r9" and call["succeeded"] is False
    assert "restarted" in call["error"] and "part-way" in call["error"]
    (kw,) = reported
    assert kw["kind"] == "unexpected" and kw["title"] == "InterruptedJob" and kw["pillar"] == "version"
    assert "version.release" in kw["message"] and "little-widget" in kw["message"] and "r9" in kw["context"]
    assert _running(paths) == {}


def test_a_cut_job_is_settled_again_until_orchestration_takes_the_answer(paths, tmp_path, reported):
    version_app.RunningJobs(version_app.running_jobs_path(paths)).add("f7", "version.feature_finish", "little-widget")
    services = Services(takes=False)  # Orchestration was down when Version started
    app = _app(paths, tmp_path, services)

    assert app.state.settle_interrupted_jobs(tries=0) == ["f7"]
    assert reported == [] and list(_running(paths)) == ["f7"]

    services.takes = True
    assert app.state.settle_interrupted_jobs(tries=0) == []
    assert len(reported) == 1 and _running(paths) == {}


def test_version_settles_them_before_it_serves(paths, monkeypatch, reported):
    import version.__main__ as version_main

    version_app.RunningJobs(version_app.running_jobs_path(paths)).add("r5", "version.release", "little-widget")
    completed = []
    monkeypatch.setattr(version_app.PillarReleaseServices, "complete", lambda self, job_id, **kw: completed.append(job_id) or True)
    monkeypatch.setattr(version_main.Paths, "from_env", staticmethod(lambda: paths))
    seen = {}

    class Server:
        def __init__(self, config):
            self.started = True

        def run(self):
            seen.update(running=_running(paths), completed=list(completed))

    monkeypatch.setattr(serving, "_Server", Server)

    version_main.main()

    assert seen == {"running": {}, "completed": ["r5"]}
