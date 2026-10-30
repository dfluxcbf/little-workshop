"""The peek: what a working Claude has done so far, read from its session transcript."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent import db, peek
from agent.app import build_app
from common.paths import Paths


def _line(entry: dict) -> str:
    return json.dumps(entry) + "\n"


ASSISTANT = {"type": "assistant", "timestamp": "2026-10-04T20:10:00Z", "message": {"content": [
    {"type": "thinking", "thinking": "private"},
    {"type": "text", "text": "I'll run the tests."},
    {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "python3 -m pytest tests/unit", "description": "unit"}},
    {"type": "tool_use", "id": "t2", "name": "Edit", "input": {"file_path": "/repo/src/agent/flow.py", "old_string": "a", "new_string": "b"}},
]}}
RESULT = {"type": "user", "timestamp": "2026-10-04T20:11:00Z", "message": {"content": [
    {"type": "tool_result", "tool_use_id": "t1", "is_error": True, "content": "1 failed, 20 passed"},
    {"type": "tool_result", "tool_use_id": "t2", "content": "ok"},
]}}


# --- REQ-000000380: Claude can be watched while it works ---


def test_a_transcript_line_becomes_what_a_person_wants_to_see():
    tools: dict[str, str] = {}
    events = peek.events_of(ASSISTANT, tools) + peek.events_of(RESULT, tools)
    assert [(e["kind"], e.get("tool"), e["text"]) for e in events] == [
        ("text", None, "I'll run the tests."),
        ("tool", "Bash", "python3 -m pytest tests/unit"),
        ("tool", "Edit", "/repo/src/agent/flow.py"),
        ("error", "Bash", "1 failed, 20 passed"),  # only a failed result is shown, named by its tool
    ]
    prompt = peek.events_of({"type": "user", "message": {"content": "Do the task " + "x" * 500}}, {})
    assert prompt[0]["kind"] == "prompt" and len(prompt[0]["text"]) <= peek.INPUT_LIMIT
    todo = peek.events_of({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t3", "name": "TodoWrite", "input": {
        "todos": [{"status": "completed"}, {"status": "pending"}]}}]}}, {})
    assert todo[0]["text"] == "1 of 2 to-dos done"


def test_events_are_read_after_a_byte_offset_and_a_half_written_line_waits(tmp_path):
    path = tmp_path / "s.jsonl"
    path.write_text(_line(ASSISTANT) + '{"type": "user", "message"', encoding="utf-8")
    first = peek.read_events(path)
    assert [e["kind"] for e in first["events"]] == ["text", "tool", "tool"] and first["next"] == len(_line(ASSISTANT).encode())
    with path.open("a", encoding="utf-8") as handle:
        handle.write(': {"content": "next prompt"}}\n')
    second = peek.read_events(path, after=first["next"])
    assert [e["text"] for e in second["events"]] == ["next prompt"] and second["next"] == path.stat().st_size
    assert peek.read_events(path, after=-1) == {"events": [], "next": path.stat().st_size, "skipped": 0}
    limited = peek.read_events(path, limit=2)
    assert len(limited["events"]) == 2 and limited["skipped"] == 2


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    return tmp_path / "home"


def test_the_agent_serves_the_peek_of_a_runs_session_and_of_an_update_doc_job(tmp_path, home, monkeypatch):
    project_dir = tmp_path / "little-meals"
    project_dir.mkdir()
    monkeypatch.setattr("agent.app.resolve_project", lambda paths, project_id, **kw: {"name": project_id, "path": str(project_dir)})
    conn = db.connect(tmp_path / "agent.db")
    client = TestClient(build_app(Paths(config_home=tmp_path / "c", state_home=tmp_path / "s"), conn))

    db.create_run(conn, "r1", job_id="job-1", project_id="little-meals", intent="task", review_mode="manual", title="t", source={})
    assert client.get("/v1/runs/by-job/job-1").json()["id"] == "r1"
    assert client.get("/v1/runs/by-job/nope").status_code == 404
    assert client.get("/v1/peek/job-1").json() == {"available": False, "running": True, "source": "run", "events": [], "next": 0, "skipped": 0}

    db.update_run(conn, "r1", session_id="11111111-2222-3333-4444-555555555555")
    transcript = peek.transcripts_dir(project_dir) / "11111111-2222-3333-4444-555555555555.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text(_line(ASSISTANT), encoding="utf-8")
    seen = client.get("/v1/peek/job-1").json()
    assert seen["available"] and seen["running"] and [e["kind"] for e in seen["events"]] == ["text", "tool", "tool"]
    db.update_run(conn, "r1", status=db.FINISHED)
    after = client.get("/v1/peek/job-1", params={"after": seen["next"]}).json()
    assert after["events"] == [] and after["running"] is False

    worktree = tmp_path / "worktree"
    db.create_doc_update(conn, "job-2", project_id="little-meals", branch="dev", remote_control=False, comment_ids=[])
    db.update_doc_update(conn, "job-2", status=db.DOC_UPDATE_RUNNING, worktree_path=str(worktree))
    (peek.transcripts_dir(worktree)).mkdir(parents=True)
    (peek.transcripts_dir(worktree) / "abc.jsonl").write_text(_line(RESULT), encoding="utf-8")
    docs = client.get("/v1/peek/job-2").json()
    assert docs["source"] == "doc_update" and docs["running"] and docs["events"][0]["kind"] == "error"
    assert client.get("/v1/peek/job-3").status_code == 404
    conn.close()


def test_the_incidents_version_changes_when_an_incident_is_recorded(tmp_path):
    conn = db.connect(tmp_path / "agent.db")
    client = TestClient(build_app(Paths(config_home=tmp_path / "c", state_home=tmp_path / "s"), conn))
    before = client.get("/v1/incidents/version").json()["version"]
    client.post("/v1/incidents", json={"project_id": "little-workshop", "pillar": "agent", "exception_type": "KeyError"})
    assert client.get("/v1/incidents/version").json()["version"] != before
    conn.close()
