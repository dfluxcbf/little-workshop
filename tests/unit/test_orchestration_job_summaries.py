"""Job summaries: each Job keeps a brief of its params and result, written with it, and Jobs can be listed
as filtered summaries, so a caller never reads (or Orchestration parses) a 1 MB test-run result it does
not show. On 2026-10-05 the full job list was 1,785 Jobs and 28 MB."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from orchestration import brief, db
from orchestration.app import build_app
from orchestration.models import Job, JobStatus


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "orchestration.db")
    yield connection
    connection.close()


BIG = {"ran": 3317, "verdicts": {"passed": 3316, "failed": 1}, "results": [{"test_name": f"t{n}", "verdict": "passed"} for n in range(3317)]}


# --- REQ-000000382: Jobs are listed as filtered summaries ---


def test_a_brief_keeps_small_values_and_names_what_it_leaves_out():
    assert brief.brief(None) is None
    kept = brief.brief({"title": "Do it", "n": 3, "ok": True, "gone": None, "gate_points": ["commit", "release"],
                        "verdicts": {"passed": 2}, "description": "x" * 5000, "suites": [{"name": "a", "ran": 1}], "results": BIG["results"]})
    assert kept == {"title": "Do it", "n": 3, "ok": True, "gone": None, "gate_points": ["commit", "release"],
                    "verdicts": {"passed": 2}, "_omitted": ["description", "suites", "results"]}
    assert brief.brief({"a": 1}) == {"a": 1}  # nothing left out: no marker


def test_a_job_is_written_with_its_brief_and_listed_as_a_summary(conn):
    job = Job(type="requirements.run_tests", target_pillar="requirements", project_id="little-workshop", params={"suite_id": "s1"})
    db.insert_job(conn, job)
    job.status, job.result = JobStatus.SUCCEEDED, BIG
    db.update_job(conn, job)
    (summary,) = db.find_jobs(conn, summary=True)
    assert summary["summary"] is True and summary["id"] == job.id and summary["status"] == "succeeded"
    assert summary["params"] == {"suite_id": "s1"}
    assert summary["result"] == {"ran": 3317, "verdicts": {"passed": 3316, "failed": 1}, "_omitted": ["results"]}
    assert len(json.dumps(summary)) < 1000
    (full,) = db.find_jobs(conn)
    assert len(full["result"]["results"]) == 3317 and "summary" not in full


def test_jobs_are_filtered_by_status_type_project_creator_time_and_id(conn):
    now = datetime.now(timezone.utc)
    jobs = [
        Job(type="agent.run_task", target_pillar="agent", project_id="little-meals", created_by="scheduler", created_at=now - timedelta(days=3)),
        Job(type="agent.run_task", target_pillar="agent", project_id="little-home", status=JobStatus.RUNNING, created_at=now - timedelta(days=1)),
        Job(type="build.run", target_pillar="build", project_id="little-meals", status=JobStatus.FAILED, created_at=now),
    ]
    for job in jobs:
        db.insert_job(conn, job)

    def ids(**filters):
        return [j["id"] for j in db.find_jobs(conn, summary=True, **filters)]

    assert ids() == [j.id for j in jobs]  # oldest first, as list_jobs
    assert ids(statuses=["running", "failed"]) == [jobs[1].id, jobs[2].id]
    assert ids(types=["agent.run_task"]) == [jobs[0].id, jobs[1].id]
    assert ids(project_id="little-meals") == [jobs[0].id, jobs[2].id]
    assert ids(created_by="scheduler") == [jobs[0].id]
    assert ids(since=now - timedelta(days=2)) == [jobs[1].id, jobs[2].id]
    assert ids(until=now - timedelta(days=2)) == [jobs[0].id]
    assert ids(ids=[jobs[2].id, "missing"]) == [jobs[2].id]
    assert ids(ids=[]) == []  # asked for none: none, not every Job


def test_jobs_are_searched_paged_newest_first_and_counted_in_the_store(conn):
    now = datetime.now(timezone.utc)
    jobs = [Job(type=kind, target_pillar=kind.split(".")[0], project_id=project, created_at=now - timedelta(minutes=10 - n),
                updated_at=now - timedelta(minutes=10 - n), status=status)
            for n, (kind, project, status) in enumerate([
                ("policy.check_project", "little-meals", JobStatus.SUCCEEDED), ("policy.check_project", "little_home", JobStatus.FAILED),
                ("build.run", "little-meals", JobStatus.SUCCEEDED), ("agent.run_task", "little-home", JobStatus.RUNNING)])]
    for job in jobs:
        db.insert_job(conn, job)

    def ids(**filters):
        return [j["id"] for j in db.find_jobs(conn, summary=True, **filters)]

    assert ids(q="MEALS") == [jobs[0].id, jobs[2].id]
    assert ids(q="little_") == [jobs[1].id]  # an underscore is itself, not any character
    assert ids(q=jobs[3].id[:8]) == [jobs[3].id]
    assert ids(newest_first=True, limit=2) == [jobs[3].id, jobs[2].id]
    assert ids(updated_since=now - timedelta(minutes=8, seconds=30)) == [jobs[2].id, jobs[3].id]
    assert db.count_jobs(conn) == {"succeeded": 2, "failed": 1, "running": 1}
    assert db.count_jobs(conn, q="policy") == {"succeeded": 1, "failed": 1}

    client = TestClient(build_app(conn, socket_lookup=lambda pillar: None))
    assert client.get("/v1/jobs/counts", params={"q": "meals"}).json() == {"succeeded": 2}
    assert client.get("/v1/jobs/counts", params={"status": "bogus"}).status_code == 422
    page = client.get("/v1/jobs", params={"view": "summary", "order": "newest", "limit": 3, "status": "succeeded,failed"}).json()
    assert [j["id"] for j in page] == [jobs[2].id, jobs[1].id, jobs[0].id]
    assert client.get("/v1/jobs", params={"limit": 0}).status_code == 422


def test_jobs_written_before_briefs_existed_get_theirs_when_the_store_opens(tmp_path):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript(db.SCHEMA.replace("    brief TEXT,\n", ""))
    row = db.job_to_row(Job(type="build.run", target_pillar="build", params={"target": "//:all"}, result=BIG))
    row.pop("brief")
    old.execute(f"INSERT INTO jobs ({', '.join(row)}) VALUES ({', '.join('?' for _ in row)})", tuple(row.values()))
    old.commit()
    old.close()
    conn = db.connect(path)
    (summary,) = db.find_jobs(conn, summary=True)
    assert summary["params"] == {"target": "//:all"} and summary["result"]["_omitted"] == ["results"]
    conn.close()


def test_the_list_endpoint_serves_summaries_and_filters(conn):
    client = TestClient(build_app(conn, socket_lookup=lambda pillar: None))
    job = Job(type="requirements.run_tests", target_pillar="requirements", status=JobStatus.SUCCEEDED, result=BIG, project_id="p")
    other = Job(type="build.run", target_pillar="build", status=JobStatus.QUEUED, project_id="p")
    db.insert_job(conn, job)
    db.insert_job(conn, other)
    listed = client.get("/v1/jobs", params={"view": "summary", "type": "requirements.run_tests"}).json()
    assert [j["id"] for j in listed] == [job.id] and listed[0]["result"]["_omitted"] == ["results"] and "progress" in listed[0]
    assert [j["id"] for j in client.get("/v1/jobs", params={"status": "queued,succeeded", "view": "summary"}).json()] == [job.id, other.id]
    assert len(client.get("/v1/jobs", params={"status": "succeeded"}).json()[0]["result"]["results"]) == 3317  # full, unless asked
    assert client.get("/v1/jobs", params={"status": "nonsense"}).status_code == 422


def test_lws_job_list_shows_the_newest_summaries_and_reads_them_from_the_store_when_the_daemon_is_down(tmp_path, monkeypatch, capsys):
    from cli import client as cli_client
    from cli import main
    from common.paths import Paths

    paths = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    paths.ensure_layout()
    monkeypatch.setattr(Paths, "from_env", classmethod(lambda cls: paths))
    store = db.connect(paths.pillar_db_path("orchestration"))
    now = datetime.now(timezone.utc)
    old_run = Job(type="requirements.run_tests", target_pillar="requirements", status=JobStatus.SUCCEEDED, result=BIG, created_at=now - timedelta(hours=1))
    newer = Job(type="build.run", target_pillar="build", project_id="little-meals", status=JobStatus.FAILED, created_at=now)
    for job in (old_run, newer):
        db.insert_job(store, job)
    store.close()

    assert main.main(["job", "list"]) == 0  # no daemon here: the store itself
    shown = json.loads(capsys.readouterr().out)
    assert shown["source"] == "fallback" and [j["id"] for j in shown["jobs"]] == [newer.id, old_run.id]
    assert shown["jobs"][1]["result"]["_omitted"] == ["results"]
    assert main.main(["job", "list", "--status", "succeeded", "--full", "--limit", "0"]) == 0
    (full,) = json.loads(capsys.readouterr().out)["jobs"]
    assert len(full["result"]["results"]) == 3317

    asked = {}
    monkeypatch.setattr(cli_client, "list_jobs", lambda p, **kw: asked.update(kw) or {"source": "daemon", "jobs": []})
    main.main(["job", "list", "--type", "agent.run_task,build.run", "--limit", "5"])
    assert asked == {"status": None, "types": "agent.run_task,build.run", "newest_first": True, "limit": 5, "summary": True}


def test_the_mcp_job_list_gives_claude_the_newest_summaries(monkeypatch):
    from mcp_server import client as mcp_client
    from mcp_server import tools

    sent = {}
    monkeypatch.setattr(mcp_client, "_get", lambda paths, pillar, path, params=None, timeout=5.0: sent.update(params) or [])
    tools.TOOLS_BY_NAME["workshop_job_list"].handler(None, {})
    assert sent == {"order": "newest", "limit": 50, "view": "summary"}
    sent.clear()
    tools.TOOLS_BY_NAME["workshop_job_list"].handler(None, {"type": "agent.query", "limit": 0, "full": True})
    assert sent == {"type": "agent.query", "order": "newest"}
