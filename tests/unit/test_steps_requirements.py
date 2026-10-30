"""The Requirements & Quality pillar reports the steps its step spec declares."""

import pytest
from fastapi.testclient import TestClient

from common import job_steps
from common.paths import Paths
from common.step_specs import steps_for
from requirements import app as app_module
from requirements import db
from requirements.app import build_app


# --- REQ-000000310: Every job has a checklist of its actions ---

@pytest.fixture
def paths(tmp_path):
    p = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    p.ensure_layout()
    return p


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "requirements.db")
    yield connection
    connection.close()


@pytest.fixture
def client(conn, paths):
    return TestClient(build_app(conn, paths))


@pytest.fixture
def reports(monkeypatch):
    seen = []

    def fake_socket_call(paths):
        def call(method, path, json=None):
            if method == "POST" and "/steps/" in path:
                seen.append((path.rsplit("/", 1)[1], json["state"], json["detail"]))
            return {}
        return call

    monkeypatch.setattr(job_steps, "_socket_call", fake_socket_call)
    return seen


def _declared(job_type):
    return [s.key for s in steps_for(job_type)]


def _suite(client):
    return client.post("/v1/jobs/requirements.create_test_suite", json={
        "job_id": "j", "project_id": "p1", "params": {"name": "core"},
    }).json()["result"]


def test_import_junit_reports_every_declared_step(client, reports):
    suite = _suite(client)
    xml = '<testsuite><testcase classname="t" name="a" /></testsuite>'
    response = client.post("/v1/jobs/requirements.import_junit", json={
        "job_id": "j", "project_id": "p1", "params": {"suite_id": suite["id"], "xml": xml},
    })
    assert response.json()["outcome"] == "succeeded"
    assert [k for k, s, _ in reports if s == "succeeded"] == _declared("requirements.import_junit")


def test_malformed_junit_fails_the_parse_step_with_the_message(client, reports):
    suite = _suite(client)
    response = client.post("/v1/jobs/requirements.import_junit", json={
        "job_id": "j", "project_id": "p1", "params": {"suite_id": suite["id"], "xml": "not xml <<<"},
    })
    assert response.json()["outcome"] == "failed"
    assert [(k, d) for k, s, d in reports if s == "failed"] == [("parse_report", response.json()["error"])]


def test_run_tests_for_an_unknown_suite_fails_its_first_step(client, reports):
    response = client.post("/v1/jobs/requirements.run_tests", json={
        "job_id": "j", "project_id": "p1", "params": {"suite_id": "ghost", "commitish": "HEAD"},
    })
    assert response.json()["outcome"] == "failed"
    assert [k for k, s, _ in reports if s == "failed"] == ["validate_request"]


def test_run_tests_reports_run_and_record_steps(client, reports, monkeypatch):
    suite = _suite(client)
    monkeypatch.setattr(app_module, "run_sandboxed", lambda *a, **kw: [])
    response = client.post("/v1/jobs/requirements.run_tests", json={
        "job_id": "j", "project_id": "p1", "params": {"suite_id": suite["id"], "commitish": "HEAD"},
    })
    assert response.json()["outcome"] == "succeeded"
    assert [k for k, s, _ in reports if s == "succeeded"] == _declared("requirements.run_tests")


def test_discover_tests_reports_every_declared_step(client, reports, monkeypatch):
    monkeypatch.setattr(app_module, "collect_sandboxed", lambda *a, **kw: ["tests/test_a.py::test_x"])
    response = client.post("/v1/jobs/requirements.discover_tests", json={"job_id": "j", "project_id": "p1", "params": {}})
    assert response.json()["outcome"] == "succeeded"
    assert [k for k, s, _ in reports if s == "succeeded"] == _declared("requirements.discover_tests")
