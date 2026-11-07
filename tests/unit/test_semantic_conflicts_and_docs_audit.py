"""The on-demand docs-drift audit (REQ-000000522) and the semantic conflict check (REQ-000000523)."""

import json
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

from common.paths import Paths
from dashboard import job_catalog
from requirements import db, semantic
from requirements import app as requirements_app
from requirements.app import build_app


# --- REQ-000000522: An on-demand job audits a project's docs against its code ---


def test_the_docs_audit_is_a_read_only_query_in_the_projects_checkout():
    job, error = job_catalog.build_job("little-meals", "docs_audit", {})
    assert error is None
    assert (job["type"], job["target_pillar"], job["project_id"]) == ("agent.query", "agent", "little-meals")
    assert job["params"]["read_checkout"] is True  # Read, Grep and Glob in the checkout, nothing else
    assert "change nothing" in job["params"]["prompt"] and "contradict" in job["params"]["prompt"]
    assert job["params"]["timeout_seconds"] == job_catalog.DOCS_AUDIT_TIMEOUT_SECONDS


def _dashboard(tmp_path, monkeypatch, job):
    from dashboard import app as dashboard_app
    from dashboard import client as dashboard_client
    from test_dashboard_app import _job, _stub_jobs

    full = _job(1, **job)
    _stub_jobs(monkeypatch, [full])
    monkeypatch.setattr(dashboard_client, "get_job", lambda paths, job_id, **kw: full)
    paths = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    return TestClient(dashboard_app.build_app(paths)), full, dashboard_client


def test_the_job_page_shows_a_querys_answer(tmp_path, monkeypatch):
    test_client, job, _ = _dashboard(tmp_path, monkeypatch, {"type": "agent.query", "result": {"text": "### docs/design.md:12\n- **The document says:** x"}})
    page = test_client.get(f"/jobs/{job['id']}").text
    assert "Claude's answer" in page and "docs/design.md:12" in page


# --- REQ-000000523: A user-started semantic check lists suspected requirement conflicts, and one can be recorded ---


@pytest.fixture
def client(tmp_path):
    conn = db.connect(tmp_path / "requirements.db")
    yield TestClient(build_app(conn, Paths(config_home=tmp_path / "c", state_home=tmp_path / "s")))
    conn.close()


def _job(client, job_type, params, project_id="p1"):
    return client.post(f"/v1/jobs/{job_type}", json={"job_id": "j", "project_id": project_id, "params": params}).json()


def _create(client, key, description):
    return _job(client, "requirements.create_requirement", {"key": key, "title": key, "description": description})["result"]


def test_the_semantic_check_is_a_catalog_job_on_the_requirements_pillar():
    job, error = job_catalog.build_job("p1", "semantic_conflicts", {})
    assert error is None and (job["type"], job["target_pillar"], job["project_id"]) == ("requirements.semantic_conflicts", "requirements", "p1")


def test_the_answer_keeps_only_known_pairs_older_first():
    from requirements.models import Requirement
    from datetime import datetime, timezone, timedelta

    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    a = Requirement(id="a", project_id="p", key="REQ-1", title="t", description="Keep logs 30 days.", created_at=t0)
    b = Requirement(id="b", project_id="p", key="REQ-2", title="t", description="Delete logs daily.", created_at=t0 + timedelta(days=1))
    answer = "Here:\n" + json.dumps([
        {"older_key": "REQ-2", "newer_key": "REQ-1", "older_text": "Delete logs daily", "newer_text": "Keep logs 30 days",
         "summary": "retention", "reason": "both cannot hold"},
        {"older_key": "REQ-1", "newer_key": "REQ-9", "summary": "unknown key"},
        {"older_key": "REQ-1", "newer_key": "REQ-1", "summary": "itself"},
        "junk",
    ])
    [s] = semantic.parse_answer(answer, [a, b])
    assert (s["older_key"], s["newer_key"], s["older_text"]) == ("REQ-1", "REQ-2", "Keep logs 30 days")
    assert semantic.parse_answer("no json at all", [a, b]) == []


def test_the_check_asks_about_published_and_draft_requirements_and_records_nothing(client, monkeypatch):
    older = _create(client, "REQ-1", "The pillar shall keep logs for 30 days.")
    _create(client, "REQ-2", "The pillar shall delete logs every day.")
    asked = {}

    def fake_ask(paths, prompt, *, job_id, project_id):
        asked.update(prompt=prompt, project_id=project_id)
        return json.dumps([{"older_key": "REQ-1", "newer_key": "REQ-2", "older_text": "keep logs for 30 days",
                            "newer_text": "delete logs every day", "summary": "retention", "reason": "cannot both hold"}])

    monkeypatch.setattr(requirements_app.semantic, "ask_claude", fake_ask)
    body = _job(client, "requirements.semantic_conflicts", {})

    assert body["outcome"] == "succeeded" and body["result"]["checked"] == 2
    [s] = body["result"]["suspected"]
    assert s["older_requirement_id"] == older["id"] and s["reason"] == "cannot both hold"
    assert "REQ-1" in asked["prompt"] and "REQ-2" in asked["prompt"] and asked["project_id"] == "p1"
    assert client.get("/v1/conflicts").json() == []  # only suspected, never recorded by the check


def test_a_recorded_semantic_conflict_survives_the_lexical_analysis_until_a_requirement_changes(client, monkeypatch):
    older = _create(client, "REQ-1", "The pillar shall keep logs for 30 days.")
    newer = _create(client, "REQ-2", "The pillar shall delete logs every day.")
    suspect = {"older_key": "REQ-1", "newer_key": "REQ-2", "older_requirement_id": older["id"], "newer_requirement_id": newer["id"],
               "older_text": "keep logs for 30 days", "newer_text": "delete logs every day", "summary": "retention", "reason": "r"}

    recorded = _job(client, "requirements.record_semantic_conflict", suspect)

    assert recorded["outcome"] == "succeeded"
    conflict = recorded["result"]
    assert conflict["kind"] == "semantic" and conflict["status"] == "open" and "retention" in conflict["summary"]
    assert _job(client, "requirements.record_semantic_conflict", suspect)["result"]["id"] == conflict["id"]  # no duplicate
    _job(client, "requirements.analyze_conflicts", {})
    assert client.get(f"/v1/conflicts/{conflict['id']}").json()["status"] == "open"  # the word-based check leaves it

    _job(client, "requirements.update_requirement", {"requirement_id": newer["id"], "description": "The pillar shall delete logs after 30 days."})
    _job(client, "requirements.analyze_conflicts", {})
    assert client.get(f"/v1/conflicts/{conflict['id']}").json()["status"] == "resolved"


def test_recording_refuses_a_requirement_of_another_project(client):
    a = _create(client, "REQ-1", "The pillar shall start.")
    other = _job(client, "requirements.create_requirement", {"key": "REQ-2", "title": "x", "description": "The pillar shall stop."}, project_id="p2")["result"]
    body = _job(client, "requirements.record_semantic_conflict", {
        "older_key": "REQ-1", "newer_key": "REQ-2", "older_requirement_id": a["id"], "newer_requirement_id": other["id"],
        "older_text": "x", "newer_text": "y", "summary": "s"})
    assert body["outcome"] == "failed" and "not one of this project's" in body["error"]


def test_the_job_page_lists_suspects_and_records_one(tmp_path, monkeypatch):
    suspect = {"older_key": "REQ-1", "newer_key": "REQ-2", "older_requirement_id": "a", "newer_requirement_id": "b",
               "older_text": "keep", "newer_text": "delete", "summary": "retention", "reason": "cannot both hold"}
    test_client, job, dashboard_client = _dashboard(tmp_path, monkeypatch, {
        "type": "requirements.semantic_conflicts", "project_id": "p1", "result": {"checked": 2, "suspected": [suspect]}})
    page = test_client.get(f"/jobs/{job['id']}").text
    assert "Suspected conflicts" in page and "cannot both hold" in page and f'action="/jobs/{job["id"]}/record-conflict/0"' in page

    created = {}
    monkeypatch.setattr(dashboard_client, "create_job", lambda paths, **f: (created.update(f), {"status": "succeeded", "result": {"id": "c1"}})[1])
    response = test_client.post(f"/jobs/{job['id']}/record-conflict/0", follow_redirects=False)
    assert response.headers["location"].startswith("/requirements/conflicts/c1")
    assert (created["type"], created["project_id"], created["params"]) == ("requirements.record_semantic_conflict", "p1", suspect)
    missing = test_client.post(f"/jobs/{job['id']}/record-conflict/5", follow_redirects=False)
    assert "not on this job" in unquote(missing.headers["location"])
