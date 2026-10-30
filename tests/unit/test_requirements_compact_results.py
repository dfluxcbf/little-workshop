"""A test run answers its Job with counts and the failures worth reading, never every result: the
Requirements pillar keeps every result in its own store, and a Job carrying 3,317 of them (1.1 MB) made
every list of Jobs, and every Claude session reading the run's Job, carry them too."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from common.paths import Paths
from requirements import app as app_module
from requirements import db
from requirements.junit import ParsedResult
from requirements.models import TestVerdict


@pytest.fixture
def client(tmp_path):
    paths = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    paths.ensure_layout()
    conn = db.connect(tmp_path / "requirements.db")
    yield TestClient(app_module.build_app(conn, paths))
    conn.close()


def _suite(client, name="Unit tests"):
    return client.post("/v1/jobs/requirements.create_test_suite",
                       json={"job_id": "j", "project_id": "p1", "params": {"name": name}}).json()["result"]


def _parsed(passed=120, failed=60, errors=1, skipped=2):
    items = [ParsedResult(f"tests.test_a::ok{n}", TestVerdict.PASSED, "") for n in range(passed)]
    items += [ParsedResult(f"tests.test_a::bad{n}", TestVerdict.FAILED, "AssertionError: " + "x" * 2000) for n in range(failed)]
    items += [ParsedResult(f"tests.test_b::err{n}", TestVerdict.ERROR, "boom") for n in range(errors)]
    items += [ParsedResult(f"tests.test_b::skip{n}", TestVerdict.SKIPPED, "no docker") for n in range(skipped)]
    return items


# --- REQ-000000384: Test runs answer with counts, not every result ---


def test_a_suite_run_answers_with_counts_and_at_most_fifty_short_failures(client, monkeypatch):
    suite = _suite(client)
    monkeypatch.setattr(app_module, "run_sandboxed", lambda *a, **kw: _parsed())
    body = client.post("/v1/jobs/requirements.run_tests", json={
        "job_id": "j", "project_id": "p1", "params": {"suite_id": suite["id"], "commitish": "dev"}}).json()
    result = body["result"]
    assert body["outcome"] == "succeeded" and "results" not in result
    assert (result["suite_id"], result["ran"]) == (suite["id"], 183)
    assert result["verdicts"] == {"passed": 120, "failed": 60, "error": 1, "skipped": 2}
    assert result["failing_total"] == 61 and len(result["failing"]) == 50
    assert {f["verdict"] for f in result["failing"]} <= {"failed", "error"} and all(len(f["detail"]) <= 500 for f in result["failing"])
    # Every result is still recorded, where it belongs.
    assert len(client.get("/v1/test_results", params={"suite_id": suite["id"]}).json()) == 183


def test_a_run_of_several_suites_adds_the_totals(client, monkeypatch):
    a, b = _suite(client, "test_a"), _suite(client, "test_b")
    monkeypatch.setattr(app_module, "run_suites_sandboxed", lambda paths, project, commitish, scopes, test_command=None: {
        a["id"]: [i for i in _parsed(3, 1, 0, 0)], b["id"]: [i for i in _parsed(2, 0, 1, 1) if i.test_name.startswith("tests.test_b")]})
    result = client.post("/v1/jobs/requirements.run_tests", json={
        "job_id": "j", "project_id": "p1", "params": {"suite_ids": [a["id"], b["id"]]}}).json()["result"]
    assert {s["name"]: s["verdicts"] for s in result["suites"]} == {"test_a": {"passed": 3, "failed": 1}, "test_b": {"error": 1, "skipped": 1}}
    assert result["ran"] == 6 and result["verdicts"] == {"passed": 3, "failed": 1, "error": 1, "skipped": 1}
    assert [f["test_name"] for f in result["failing"]] == ["tests.test_a::bad0", "tests.test_b::err0"]


def test_a_junit_import_answers_the_same_way(client):
    suite = _suite(client)
    xml = '<testsuite><testcase classname="t" name="a" /><testcase classname="t" name="b"><failure message="boom"/></testcase></testsuite>'
    result = client.post("/v1/jobs/requirements.import_junit", json={
        "job_id": "j", "project_id": "p1", "params": {"suite_id": suite["id"], "xml": xml}}).json()["result"]
    assert result["imported"] == 2 and result["verdicts"] == {"passed": 1, "failed": 1} and "results" not in result
    assert result["failing"][0]["test_name"].endswith("b")


def test_a_ci_check_reads_the_counts(tmp_path, monkeypatch):
    from agent import db as agent_db
    from agent import flow as flow_module
    from agent.flow import AgentFlow

    class Github:
        statuses = []

        def pull_request(self, repo, number):
            return {"headRefName": "feature/x", "headRefOid": "abc123def456"}

        def post_status(self, repo, sha, *, state, context, description):
            self.statuses.append((state, description))

    paths = Paths(config_home=tmp_path / "c", state_home=tmp_path / "s")
    monkeypatch.setattr(flow_module.requirements_client, "run_job", lambda *a, **kw: {
        "ran": 3, "verdicts": {"passed": 2, "failed": 1}, "failing": [{"test_name": "t::x", "verdict": "failed", "detail": "no"}], "failing_total": 1})
    conn = agent_db.connect(tmp_path / "agent.db")
    github = Github()
    result = AgentFlow(paths=paths, github=github).run_ci_check(conn, "p1", "https://github.com/o/r", {"pr_number": 1, "suite_id": "s"})
    assert result["state"] == "failure" and result["summary"] == "1 of 3 tests failed"
    assert result["failing"] == [{"test_name": "t::x", "verdict": "failed", "detail": "no"}] and "results" not in result
    conn.close()


def test_statistics_count_a_compact_run_with_a_failure_as_failed():
    from dashboard.statistics import test_execution_failed

    assert test_execution_failed({"status": "succeeded", "result": {"ran": 3, "verdicts": {"passed": 2, "error": 1}}})
    assert not test_execution_failed({"status": "succeeded", "result": {"ran": 3, "verdicts": {"passed": 2, "skipped": 1}}})
    assert test_execution_failed({"status": "failed", "result": None})
