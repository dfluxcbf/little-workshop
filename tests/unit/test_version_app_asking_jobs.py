"""A version job that may stop and ask the user: answered in the dispatch call when it finishes at once,
otherwise accepted, finishing later through Orchestration's completion callback."""

from __future__ import annotations

import threading

import pytest
from fastapi.testclient import TestClient
from version_repo_helper import make_repo

from common.job_steps import StepAborted, StepFailed
from common.paths import Paths
from version import app as version_app
from version.common import CommandResult
from version.resolve import ProjectResolutionError


class Services:
    def __init__(self):
        self.completed = threading.Event()
        self.calls: list[dict] = []

    def complete(self, job_id, *, succeeded, result, error):
        self.calls.append({"job_id": job_id, "succeeded": succeeded, "result": result, "error": error})
        self.completed.set()


@pytest.fixture
def paths(tmp_path):
    return Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")


def _client(paths, tmp_path, services):
    repo = make_repo(tmp_path)

    def resolve(_paths, project_id):
        if project_id != "little-widget":
            raise ProjectResolutionError("unknown")
        return repo

    return TestClient(version_app.build_app(paths, resolve=resolve, release_services=services))


def _post(client):
    return client.post("/v1/jobs/version.feature_finish", json={"job_id": "j1", "project_id": "little-widget", "params": {}}).json()


# --- REQ-000000311: An action that needs the user asks, and times out after 12 hours ---

def test_a_job_that_finishes_within_the_grace_period_is_answered_in_the_call(paths, tmp_path, monkeypatch):
    monkeypatch.setitem(version_app.JOB_HANDLERS, "version.feature_finish", lambda repo, params: CommandResult(True, "done", {"k": 1}))
    body = _post(_client(paths, tmp_path, Services()))
    assert body == {"outcome": "succeeded", "result": {"k": 1}, "message": "done"}


def test_a_failure_within_the_grace_period_is_answered_in_the_call(paths, tmp_path, monkeypatch):
    client = _client(paths, tmp_path, Services())
    monkeypatch.setitem(version_app.JOB_HANDLERS, "version.feature_finish", lambda repo, params: CommandResult(False, "nope"))
    assert _post(client) == {"outcome": "failed", "error": "nope"}

    def explode(repo, params):
        raise StepFailed("stopped by the user")

    monkeypatch.setitem(version_app.JOB_HANDLERS, "version.feature_finish", explode)
    assert _post(client)["error"] == "stopped by the user"

    def crash(repo, params):
        raise RuntimeError("bug")

    monkeypatch.setitem(version_app.JOB_HANDLERS, "version.feature_finish", crash)
    assert "RuntimeError: bug" in _post(client)["error"]


def test_a_job_still_waiting_after_the_grace_period_is_accepted_and_completes_later(paths, tmp_path, monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(version_app, "SYNC_GRACE_SECONDS", 0.05)

    def waits(repo, params):
        release.wait(5)
        return CommandResult(True, "finished late", {"k": 2})

    monkeypatch.setitem(version_app.JOB_HANDLERS, "version.feature_finish", waits)
    services = Services()
    assert _post(_client(paths, tmp_path, services))["outcome"] == "accepted"
    release.set()
    assert services.completed.wait(5)
    assert services.calls == [{"job_id": "j1", "succeeded": True, "result": {"k": 2, "message": "finished late"}, "error": None}]


def test_a_late_failure_completes_the_job_as_failed(paths, tmp_path, monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(version_app, "SYNC_GRACE_SECONDS", 0.05)

    def waits(repo, params):
        release.wait(5)
        return CommandResult(False, "conflicts left")

    monkeypatch.setitem(version_app.JOB_HANDLERS, "version.feature_finish", waits)
    services = Services()
    assert _post(_client(paths, tmp_path, services))["outcome"] == "accepted"
    release.set()
    assert services.completed.wait(5)
    assert services.calls[0]["succeeded"] is False and services.calls[0]["error"] == "conflicts left"


def test_a_job_settled_elsewhere_while_it_waited_reports_nothing_more(paths, tmp_path, monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(version_app, "SYNC_GRACE_SECONDS", 0.05)

    def waits(repo, params):
        release.wait(5)
        raise StepAborted("stopped")

    monkeypatch.setitem(version_app.JOB_HANDLERS, "version.feature_finish", waits)
    services = Services()
    assert _post(_client(paths, tmp_path, services))["outcome"] == "accepted"
    release.set()
    assert not services.completed.wait(0.3) and services.calls == []


def test_a_job_settled_elsewhere_before_the_grace_ends_is_a_failure_answer(paths, tmp_path, monkeypatch):
    def aborted(repo, params):
        raise StepAborted("stopped")

    monkeypatch.setitem(version_app.JOB_HANDLERS, "version.feature_finish", aborted)
    assert _post(_client(paths, tmp_path, Services()))["outcome"] == "failed"
