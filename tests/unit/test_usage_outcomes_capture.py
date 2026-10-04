"""How each tool call ended, read from its result when a transcript is ingested (src/usage/results.py,
docs/pillars/usage.md "Call outcomes"): the base for telling the branches of a workflow apart."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from common.paths import Paths
from usage import db, intake, results, transcripts
from usage.app import build_app


# --- REQ-000000358: Each tool call's outcome is read from its result ---


@pytest.mark.parametrize("text, is_error, expected", [
    # pytest: the summary decides, whatever the exit code a pipe left (`| tail` exits 0)
    ("....F\n=========== 1 failed, 997 passed, 6 warnings in 3.2s ===========", False, ("failed", 0, 997, 1)),
    ("3157 passed, 6 warnings in 250.83s (0:04:10)", False, ("ok", 0, 3157, 0)),
    ("Exit code 2\n1 failed, 230 deselected, 2 warnings in 0.32s", True, ("failed", 2, 0, 1)),
    ("= 2 passed, 1 error in 0.5s =", False, ("failed", 0, 2, 1)),
    # bazel
    ("//:test   PASSED in 26.1s\n\nExecuted 1 out of 1 test: 1 test passes.", False, ("ok", 0, 1, 0)),
    ("//tests:unit FAILED in 12.0s\n//tests:e2e PASSED in 3s\nExecuted 2 out of 2 tests: 1 test passes and 1 fails locally.", True, ("failed", None, 1, 1)),
    # vitest
    (" Tests  2 failed | 10 passed (12)", False, ("failed", 0, 10, 2)),
    # a plain command: its exit code
    ("Exit code 1\nfatal: not a git repository", True, ("failed", 1, None, None)),
    ("done", False, ("ok", 0, None, None)),
])
def test_a_shell_commands_outcome(text, is_error, expected):
    got = results.classify("Bash", text, is_error)
    assert (got["outcome"], got["exit_code"], got["tests_passed"], got["tests_failed"]) == expected


@pytest.mark.parametrize("status, outcome", [
    ("succeeded", "ok"), ("failed", "failed"), ("pending_confirmation", "held"), ("running", "running"),
    ("queued", "running"), ("cancelled", "cancelled"), ("stopped", "cancelled"),
])
def test_a_workshop_job_ends_as_its_status(status, outcome):
    job = json.dumps({"id": "j1", "type": "version.improvement_finish", "status": status, "steps": []})
    assert results.classify("mcp__little-workshop__workshop_version_improvement_finish", job, False)["outcome"] == outcome


def test_other_workshop_answers_and_other_tools():
    workshop = "mcp__little-workshop__workshop_job_list"
    assert results.classify(workshop, json.dumps([{"id": "j1", "status": "failed"}]), False)["outcome"] == "ok"  # a list read, not a Job
    assert results.classify(workshop, json.dumps({"success": False, "message": "no such pillar"}), False)["outcome"] == "failed"
    assert results.classify(workshop, "not json", True)["outcome"] == "failed"
    assert results.classify("Write", "EACCES: permission denied", True)["outcome"] == "failed"
    assert results.classify("Read", "1\timport os", False) == {"outcome": "ok", "exit_code": None, "tests_passed": None, "tests_failed": None}
    assert results.classify("Bash", None, None)["outcome"] is None  # no result kept: nothing to say


def _line(**fields):
    return json.dumps(fields)


def test_the_transcript_parser_stores_each_calls_outcome(tmp_path):
    path = tmp_path / "-work" / "s.jsonl"
    path.parent.mkdir(parents=True)
    use = lambda i, name, inp: _line(type="assistant", sessionId="s", timestamp=f"2026-10-04T10:00:0{i}Z", requestId=f"r{i}",
                                     message={"model": "m", "usage": {"input_tokens": 1}, "content": [{"type": "tool_use", "id": f"t{i}", "name": name, "input": inp}]})
    result = lambda i, text, err: _line(type="user", sessionId="s", timestamp=f"2026-10-04T10:00:0{i}Z",
                                        message={"content": [{"type": "tool_result", "tool_use_id": f"t{i}", "content": text, "is_error": err}]})
    path.write_text("\n".join([
        use(1, "Bash", {"command": "python3 -m pytest tests/unit -q | tail -3"}), result(1, "2 failed, 40 passed in 1.0s", False),
        use(2, "mcp__little-workshop__workshop_version_feature_finish", {"project_id": "p"}),
        result(2, [{"type": "text", "text": json.dumps({"id": "j", "type": "version.feature_finish", "status": "pending_confirmation"})}], False),
    ]) + "\n")
    calls = {c["id"]: c for c in transcripts.parse_transcript(path)["tool_calls"]}
    assert (calls["t1"]["outcome"], calls["t1"]["tests_failed"], calls["t1"]["tests_passed"], calls["t1"]["success"]) == ("failed", 2, 40, True)
    assert calls["t2"]["outcome"] == "held"


def test_telemetry_gives_only_ok_or_failed():
    body = {"resourceLogs": [{"resource": {"attributes": []}, "scopeLogs": [{"logRecords": [{"attributes": [
        {"key": k, "value": {"stringValue": v}} for k, v in {"session.id": "s", "event.name": "tool_result", "tool_name": "Bash",
                                                              "tool_use_id": "t1", "success": "false", "tool_input": "{\"command\": \"ls\"}"}.items()]}]}]}]}
    (call,) = intake.parse_logs(body)["tool_calls"]
    assert call["outcome"] == "failed"


def test_stored_transcripts_are_read_again_once_when_the_parser_learns_something(tmp_path):
    paths = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    paths.ensure_layout()
    conn = db.connect(tmp_path / "usage.db")
    root = tmp_path / "projects"
    path = root / "-work" / "s.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text("\n".join([
        _line(type="assistant", sessionId="s", timestamp="2026-10-04T10:00:01Z", requestId="r1",
              message={"model": "m", "usage": {"input_tokens": 1}, "content": [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "pytest"}}]}),
        _line(type="user", sessionId="s", timestamp="2026-10-04T10:00:02Z",
              message={"content": [{"type": "tool_result", "tool_use_id": "t1", "content": "1 failed, 2 passed", "is_error": False}]}),
    ]) + "\n")
    # A call stored by an older parser: no outcome, and its file marked as read.
    db.upsert_tool_call(conn, {"id": "t1", "session_id": "s", "ts": "2026-10-04T10:00:01Z", "tool": "Bash", "signature": "Bash(pytest)", "source": "transcript"})
    stat = path.stat()
    db.set_meta(conn, f"transcript:{path}", f"{stat.st_mtime_ns}:{stat.st_size}")
    conn.commit()
    client = TestClient(build_app(conn, paths, projects=lambda: [], agent_usage=lambda since: [], emit=lambda *a: None,
                                  outcome_sources=lambda names: {}, transcripts_root=root))
    first = client.post("/v1/jobs/usage.ingest_transcripts", json={"job_id": "j1"}).json()["result"]
    assert first["files_read"] == 1
    row = conn.execute("SELECT outcome, tests_failed FROM tool_calls WHERE id = 't1'").fetchone()
    assert (row["outcome"], row["tests_failed"]) == ("failed", 1)
    assert client.post("/v1/jobs/usage.ingest_transcripts", json={"job_id": "j2"}).json()["result"]["files_read"] == 0
    summary = client.get("/v1/call_outcomes", params={"signature": "Bash(pytest"}).json()
    assert summary == [{"signature": "Bash(pytest)", "ok": 0, "failed": 1, "held": 0, "running": 0, "cancelled": 0, "unknown": 0,
                        "tests_passed": 2, "tests_failed": 1}]
    conn.close()


def test_an_older_store_gains_the_outcome_columns(tmp_path):
    import sqlite3
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE tool_calls (id TEXT PRIMARY KEY, session_id TEXT NOT NULL, prompt_id TEXT, request_id TEXT, ts TEXT NOT NULL, "
                "tool TEXT NOT NULL, signature TEXT NOT NULL DEFAULT '', detail TEXT NOT NULL DEFAULT '', input_bytes INTEGER, "
                "result_bytes INTEGER, duration_ms INTEGER, success INTEGER, source TEXT NOT NULL)")
    old.commit()
    old.close()
    conn = db.connect(Path(path))
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(tool_calls)")}
    assert {"outcome", "exit_code", "tests_passed", "tests_failed"} <= columns
    conn.close()
