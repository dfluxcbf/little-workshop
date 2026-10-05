"""The Dashboard reads Jobs as filtered summaries for its lists, the one Job it shows in full, and shows at
most 20,000 characters of a Job's params or result, linking to the whole as JSON."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from common.paths import Paths
from dashboard import app as app_module
from dashboard import calendar, client, statistics
from orchestration_jobs_fake import patch_jobs


@pytest.fixture
def paths(tmp_path):
    return Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")


@pytest.fixture
def test_client(paths):
    return TestClient(app_module.build_app(paths))


def _job(i, **overrides):
    base = {
        "id": f"{i:08d}aaaabbbbccccdddd", "type": "build.run", "target_pillar": "build", "project_id": "little-meals",
        "params": {"target": "//:all"}, "status": "succeeded", "queue_key": None, "depends_on": [], "created_by": "dashboard",
        "result": {"ok": True}, "error": None, "created_at": f"2026-10-05T06:{i:02d}:00+00:00", "updated_at": f"2026-10-05T06:{i:02d}:30+00:00",
    }
    base.update(overrides)
    return base


# --- REQ-000000383: The Dashboard reads only what it shows ---


def test_a_job_page_shows_twenty_thousand_characters_and_links_the_whole(test_client, monkeypatch):
    job = _job(1, result={"log": "x" * 50_000})
    monkeypatch.setattr(client, "get_job", lambda paths, job_id: job if job_id == job["id"] else None)
    patch_jobs(monkeypatch, client, [job])
    page = test_client.get(f"/jobs/{job['id']}").text
    assert "x" * 19_900 in page and "x" * 20_001 not in page
    assert "Showing the first 20,000 of 50," in page and f'href="/jobs/{job["id"]}.json"' in page
    assert len(test_client.get(f"/jobs/{job['id']}.json").json()["result"]["log"]) == 50_000
    assert test_client.get("/jobs/nope.json").status_code == 404


def test_the_jobs_page_says_what_a_summary_left_out(test_client, monkeypatch):
    patch_jobs(monkeypatch, client, [_job(1, type="requirements.run_tests", result={"ran": 3317, "_omitted": ["failing"]})])
    page = test_client.get("/jobs").text
    assert "&#34;ran&#34;: 3317" in page and "&#34;_omitted&#34;" not in page
    assert "Left out of this list: failing." in page and 'href="/jobs/00000001aaaabbbbccccdddd.json"' in page


def test_what_a_job_waits_on_is_read_by_id(test_client, monkeypatch):
    dep = _job(1, type="agent.run_task", target_pillar="agent", status="running")
    job = _job(2, depends_on=[dep["id"], "0000dead"])
    monkeypatch.setattr(client, "get_job", lambda paths, job_id: job)
    monkeypatch.setattr(client, "agent_runs", lambda paths, project: [])
    reads = patch_jobs(monkeypatch, client, [dep, job, _job(3)])
    page = test_client.get(f"/jobs/{job['id']}").text
    assert "agent.run_task" in page and "no longer in the history (0000dead)" in page
    (by_id,) = [r for r in reads if r.get("ids") is not None]
    assert sorted(by_id["ids"]) == sorted([dep["id"], "0000dead"]) and by_id["summary"]
    assert all(r.get("ids") is not None or r.get("status") for r in reads)  # nothing reads every Job


def test_the_schedule_reads_the_months_scheduled_jobs_only(test_client, monkeypatch):
    monkeypatch.setattr(client, "scheduled_tasks", lambda paths: [])
    monkeypatch.setattr(client, "agent_runs", lambda paths, project: [])
    reads = patch_jobs(monkeypatch, client, [])
    test_client.get("/schedule", params={"month": "2026-10"})
    test_client.get("/schedule", params={"month": "2026-10", "show": "all"})
    month = [r for r in reads if r.get("since") is not None]
    days = calendar.grid_days(date(2026, 10, 1))
    assert [r["created_by"] for r in month] == [calendar.SCHEDULER, None] and all(r["summary"] for r in month)
    assert month[0]["since"].date() == days[0] and month[0]["until"].date() == days[-1] + timedelta(days=1)


def test_the_banner_the_launch_buttons_tasks_and_doc_updates_read_small_filtered_lists(test_client, monkeypatch):
    monkeypatch.setattr(client, "list_projects", lambda paths, status=None: [{"name": "little-meals", "status": "active"}])
    monkeypatch.setattr(client, "agent_runs", lambda paths, project: [])
    monkeypatch.setattr(client, "scheduled_tasks", lambda paths: [])
    monkeypatch.setattr(client, "doc_updates", lambda paths, project: [])
    monkeypatch.setattr(client, "docs_overview", lambda paths, project: {"project_id": project, "branch": "dev", "branches": ["dev"],
                                                                         "dirty": False, "files": []})
    reads = patch_jobs(monkeypatch, client, [
        _job(1, type="agent.run_task", status="queued", params={"title": "Queued task", "task": "y" * 5000}),
        _job(2, type="agent.update_docs", status="running", params={"branch": "docs/x"}),
    ])
    assert "Queued task" in test_client.get("/tasks").text
    assert "docs/x" in test_client.get("/docs/little-meals").text
    test_client.get("/starting.json")
    assert reads and all(r["summary"] and (r.get("status") or r.get("ids") is not None) for r in reads)
    asked_types = [set(r.get("types") or ()) for r in reads]
    assert {"agent.run_task"} in asked_types and {"agent.update_docs"} in asked_types
    assert {"deployment.deploy", "agent.session_start"} in asked_types


def test_statistics_read_only_the_jobs_changed_since_the_last_count(paths, monkeypatch):
    now = datetime(2026, 10, 5, 8, 0, tzinfo=timezone.utc)
    assert statistics.jobs_to_read(paths, now) is None  # never counted: every Job
    first = [_job(1, status="running", result=None)]
    statistics.update(paths, projects=["little-meals"], jobs=first, policy_results=[], policies=[], incidents=[],
                      jobs_complete=True, jobs_read_at=now)
    since = statistics.jobs_to_read(paths, now + timedelta(minutes=10))
    assert since == now - statistics.JOBS_READ_MARGIN
    # The next count reads the one Job that changed: it settled. Nothing is forgotten by a partial read.
    statistics.update(paths, projects=["little-meals"], jobs=[_job(1, status="succeeded")], policy_results=[], policies=[],
                      incidents=[], jobs_complete=False, jobs_read_at=now + timedelta(minutes=10))
    statistics.update(paths, projects=["little-meals"], jobs=[], policy_results=[], policies=[], incidents=[],
                      jobs_complete=False, jobs_read_at=now + timedelta(minutes=20))
    (row,) = statistics.load(paths)["jobs"]
    assert (row["triggered"], row["succeeded"]) == (1, 1)
    assert statistics.jobs_to_read(paths, now + timedelta(days=1, minutes=1)) is None  # once a day, every Job again


def test_a_summary_that_cannot_say_whether_tests_failed_is_read_in_full(paths):
    summary = _job(1, type="requirements.run_tests", target_pillar="requirements", summary=True,
                   result={"ran": 2, "_omitted": ["results"]})
    full = {**summary, "summary": None, "result": {"ran": 2, "results": [{"verdict": "passed"}, {"verdict": "failed"}]}}
    asked = []
    statistics.update(paths, projects=["little-meals"], jobs=[summary], policy_results=[], policies=[], incidents=[],
                      full_job=lambda job_id: asked.append(job_id) or full)
    (row,) = statistics.load(paths)["tests"]
    assert asked == [summary["id"]] and (row["failed"], row["succeeded"]) == (1, 0)


def test_the_dashboards_statistics_update_asks_for_changed_jobs_after_its_first_count(paths, monkeypatch):
    monkeypatch.setattr(client, "list_projects", lambda paths, status=None: [{"name": "little-meals"}])
    monkeypatch.setattr(client, "policy_check_results", lambda paths, timeout=5.0: [])
    monkeypatch.setattr(client, "policy_list", lambda paths, topic=None: [])
    monkeypatch.setattr(client, "list_incidents", lambda paths, status=None: [])
    reads = patch_jobs(monkeypatch, client, [_job(1)])
    test = TestClient(app_module.build_app(paths))
    test.get("/statistics")
    test.get("/statistics")
    jobs_reads = [r for r in reads if not r.get("status")]
    assert jobs_reads[0].get("updated_since") is None and jobs_reads[1].get("updated_since") is not None
    assert all(r["summary"] for r in jobs_reads)
