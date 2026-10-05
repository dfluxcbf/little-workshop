"""A backlog entry keeps what Claude is doing for it (so its pages read no Jobs); Claude's short answers
(agent.query) are recorded against the Job that asked, so they can be watched like a task; and deleting
an entry deletes the transcripts of the sessions that worked on it."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from common.paths import Paths
from common.shell import CommandResult


@pytest.fixture
def paths(tmp_path):
    p = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    p.ensure_layout()
    return p


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    return tmp_path / "home"


@pytest.fixture
def registry(paths, tmp_path):
    from registry import db
    from registry.app import build_app
    from registry.models import Project

    conn = db.connect(tmp_path / "registry.db")
    db.insert_project(conn, Project(name="little-meals", path="/tmp/little-meals"))
    yield TestClient(build_app(conn, paths)), conn
    conn.close()


def _entry(client, text="Weekly export"):
    return client.post("/v1/backlog", json={"project_name": "little-meals", "primary_entry": text}).json()


# --- REQ-000000385: A backlog entry keeps what Claude is doing for it ---


def test_an_entry_says_claude_is_refining_it_while_it_does_and_with_which_job(registry, monkeypatch):
    from registry import agent_client

    client, _ = registry
    entry = _entry(client)
    seen = {}

    def query(paths, prompt, *, job_id, project_id=None, ref=None, timeout=0):
        seen["during"] = client.get(f"/v1/backlog/{entry['id']}").json()
        seen["ref"] = ref
        return "# Weekly export\n\nRefined."

    monkeypatch.setattr(agent_client, "query", query)
    client.post("/v1/jobs/registry.backlog_refine", json={"job_id": "job-7", "params": {"entry_id": entry["id"]}})
    during = seen["during"]
    assert during["refining_job"] == "job-7" and during["refining_since"] and during["refine_error"] is None
    assert seen["ref"] == f"backlog:{entry['id']}"
    after = client.get(f"/v1/backlog/{entry['id']}").json()
    assert after["refining_job"] is None and after["refining_since"] is None and after["refined_entry"].endswith("Refined.")


def test_a_failed_attempt_is_kept_until_the_next_one(registry, monkeypatch):
    from registry import agent_client

    client, _ = registry
    entry = _entry(client)

    def down(*args, **kwargs):
        raise agent_client.AgentClientError("agent.query failed: over budget")

    monkeypatch.setattr(agent_client, "query", down)
    client.post("/v1/jobs/registry.backlog_refine", json={"job_id": "job-1", "params": {"entry_id": entry["id"]}})
    failed = client.get(f"/v1/backlog/{entry['id']}").json()
    assert failed["refine_error"] == "agent.query failed: over budget" and failed["refining_job"] is None
    monkeypatch.setattr(agent_client, "query", lambda *a, **kw: "# R")
    client.post("/v1/jobs/registry.backlog_refine", json={"job_id": "job-2", "params": {"entry_id": entry["id"]}})
    assert client.get(f"/v1/backlog/{entry['id']}").json()["refine_error"] is None

    client.post("/v1/jobs/registry.backlog_task_prompt", json={"job_id": "job-3", "params": {"entry_id": entry["id"]}})
    prompted = client.get(f"/v1/backlog/{entry['id']}").json()
    assert prompted["prompting_job"] is None and prompted["prompt_error"] is None and prompted["task_prompt"].startswith("# R")


def test_the_dashboard_shows_the_entrys_own_activity_without_reading_jobs(tmp_path, monkeypatch):
    from dashboard import app as app_module
    from dashboard import client

    entry = {"id": "e1", "project_name": "little-meals", "title": "Export", "primary_entry": "Export", "refinement_notes": "",
             "refined_entry": "", "task_prompt": "", "created_at": "2026-10-05T06:00:00+00:00", "updated_at": "2026-10-05T06:00:00+00:00",
             "refining_since": datetime.now(timezone.utc).isoformat(), "refining_job": "job-7", "refine_error": None,
             "prompting_since": None, "prompting_job": None, "prompt_error": "agent.query failed: over budget"}
    monkeypatch.setattr(client, "backlog_get", lambda paths, entry_id: dict(entry))
    monkeypatch.setattr(client, "backlog_list", lambda paths, project=None: [dict(entry)])
    reads = []  # the attention banner reads Jobs by status on every page; the backlog itself reads none
    monkeypatch.setattr(client, "list_jobs", lambda paths, **kw: reads.append(kw) or [])
    monkeypatch.setattr(client, "agent_peek", lambda paths, job_id, **kw: {"available": True, "running": True, "events": [], "next": 0, "skipped": 0})
    test_client = TestClient(app_module.build_app(Paths(config_home=tmp_path / "c", state_home=tmp_path / "s")))
    page = test_client.get("/backlog/little-meals/e1").text
    assert "Claude is refining…" in page and "The last task prompt failed: agent.query failed: over budget" in page
    assert 'data-peek-job="job-7"' in page and "peek:job-7" in page.split('data-live="', 1)[1].split('"', 1)[0]
    assert "refining…" in test_client.get("/backlog/little-meals").text
    refused = test_client.post("/backlog/little-meals/e1/refine", data={}, follow_redirects=False).headers["location"]
    assert "already" in refused
    assert reads and all(kw.get("status") for kw in reads)  # only the banner's, filtered by status

    entry["refining_since"] = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()  # its Job never came back
    page = test_client.get("/backlog/little-meals/e1").text
    assert "Claude is refining…" not in page and "did not finish" in page


# --- REQ-000000386: Claude's short answers can be watched too ---


class _QueryRunner:
    def __init__(self):
        self.calls = []

    def run(self, argv, timeout=None, cwd=None):
        self.calls.append(argv)
        return CommandResult(0, json.dumps({"result": "an answer", "is_error": False}), "")


def test_a_query_is_recorded_against_the_job_that_asked_and_can_be_peeked(paths, home, tmp_path):
    from agent import db, peek
    from agent.app import build_app
    from agent.flow import AgentFlow
    from agent.query import query_dir

    conn = db.connect(tmp_path / "agent.db")
    runner = _QueryRunner()
    client = TestClient(build_app(paths, conn, flow=AgentFlow(paths=paths, runner=runner)))
    answer = client.post("/v1/jobs/agent.query", json={"job_id": "job-7", "project_id": "little-meals",
                                                        "params": {"prompt": "Refine this", "ref": "backlog:e1"}}).json()
    assert answer == {"outcome": "succeeded", "result": {"text": "an answer"}}
    argv = runner.calls[0]
    session_id = argv[argv.index("--session-id") + 1]
    (session,) = db.query_sessions(conn, job_id="job-7")
    assert (session["session_id"], session["ref"]) == (session_id, "backlog:e1") and session["finished_at"]

    transcript = peek.transcripts_dir(query_dir(paths)) / f"{session_id}.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "# Refined"}]}}) + "\n")
    seen = client.get("/v1/peek/job-7").json()
    assert seen["source"] == "query" and seen["running"] is False and seen["events"][0]["text"] == "# Refined"
    conn.close()


def test_policy_authoring_asks_claude_under_its_own_job(monkeypatch, paths):
    from common.job_steps import StepReporter, reporting
    from policy import agent_client

    sent = {}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, path, json):
            sent.update(json)
            import httpx

            return httpx.Response(200, json={"outcome": "succeeded", "result": {"text": "ok"}}, request=httpx.Request("POST", "http://x" + path))

    monkeypatch.setattr(agent_client, "resolve_pillar_socket", lambda *a, **k: Path("/x"))
    monkeypatch.setattr(agent_client, "client_for_socket", FakeClient)
    with reporting(StepReporter(paths, "job-42", call=lambda *a, **k: {})):
        agent_client.query(paths, "Author this")
    assert sent["job_id"] == "job-42"
    agent_client.query(paths, "Outside any job")
    assert sent["job_id"] == "policy-authoring"


def test_the_dashboard_shows_the_peek_of_a_job_that_asked_claude(tmp_path, monkeypatch):
    from dashboard import app as app_module
    from dashboard import client

    job = {"id": "j1", "type": "registry.backlog_refine", "target_pillar": "registry", "project_id": "little-meals", "status": "running",
           "params": {"entry_id": "e1"}, "queue_key": None, "depends_on": [], "created_by": "dashboard", "result": None, "error": None,
           "created_at": "2026-10-05T06:00:00+00:00", "updated_at": "2026-10-05T06:00:00+00:00", "steps": []}
    monkeypatch.setattr(client, "get_job", lambda paths, job_id: job)
    monkeypatch.setattr(client, "agent_peek", lambda paths, job_id, **kw: {"available": True, "running": True, "next": 10, "skipped": 0,
                                                                            "events": [{"at": "", "kind": "text", "text": "thinking aloud"}]})
    page = TestClient(app_module.build_app(Paths(config_home=tmp_path / "c", state_home=tmp_path / "s"))).get("/jobs/j1").text
    assert "Claude peek" in page and "thinking aloud" in page


# --- REQ-000000387: A deleted entry's Claude transcripts go with it ---


def test_deleting_an_entry_has_the_agent_forget_its_sessions(registry, monkeypatch):
    from registry import agent_client

    client, _ = registry
    entry = _entry(client)
    forgotten = []
    monkeypatch.setattr(agent_client, "forget", lambda paths, ref: forgotten.append(ref) or 2)
    answer = client.delete(f"/v1/backlog/{entry['id']}").json()
    assert answer == {"deleted": True, "id": entry["id"], "transcripts_deleted": 2}
    assert forgotten == [f"backlog:{entry['id']}"]


def test_the_agent_deletes_the_transcripts_of_a_refs_sessions(paths, home, tmp_path):
    from agent import db, peek
    from agent.app import build_app
    from agent.query import query_dir

    conn = db.connect(tmp_path / "agent.db")
    client = TestClient(build_app(paths, conn))
    folder = peek.transcripts_dir(query_dir(paths))
    folder.mkdir(parents=True)
    for job, session, ref in (("j1", "s-1", "backlog:e1"), ("j2", "s-2", "backlog:e1"), ("j3", "s-3", "backlog:e2")):
        db.start_query_session(conn, job_id=job, session_id=session, ref=ref)
        (folder / f"{session}.jsonl").write_text("{}\n")
    (folder / "s-1").mkdir()  # its subagents' transcripts
    (folder / "s-1" / "agent-x.jsonl").write_text("{}\n")

    assert client.post("/v1/query-sessions/forget", json={"ref": "backlog:e1"}).json() == {"deleted": 2}
    assert sorted(p.name for p in folder.iterdir()) == ["s-3.jsonl"]
    assert [s["session_id"] for s in db.query_sessions(conn)] == ["s-3"]
    assert client.post("/v1/query-sessions/forget", json={"ref": ""}).status_code == 422
    conn.close()
