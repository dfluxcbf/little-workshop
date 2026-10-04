"""The data explorer (src/usage/explorer.py, docs/pillars/usage.md "Data explorer"): what the transcript
backfill keeps besides requests and tool calls, and the cards that plot it."""

import json
import sqlite3
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from common.paths import Paths
from usage import db, explorer, transcripts
from usage.app import build_app

D1, D2, D3 = "2026-10-02", "2026-10-03", "2026-10-04"


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "usage.db")
    yield connection
    connection.close()


def _line(**fields):
    return json.dumps(fields)


def _transcript(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    base = {"sessionId": "s1"}
    path.write_text("\n".join([
        # One request written as two lines: the thinking time is on the first only.
        _line(**base, type="assistant", uuid="a1", timestamp="2026-10-04T10:00:01Z", requestId="r1", effort="medium", perTurnEffort="xhigh",
              thinkingDurationMs=1500, message={"model": "m", "usage": {"input_tokens": 1, "output_tokens": 300,
                                                                         "output_tokens_details": {"thinking_tokens": 200}},
                                                "content": [{"type": "thinking", "thinking": ""}]}),
        _line(**base, type="assistant", uuid="a2", timestamp="2026-10-04T10:00:02Z", requestId="r1", effort="medium", perTurnEffort="xhigh",
              message={"model": "m", "usage": {"input_tokens": 1, "output_tokens": 300, "output_tokens_details": {"thinking_tokens": 200}},
                       "content": [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls"}}]}),
        _line(**base, type="user", uuid="u1", timestamp="2026-10-04T10:00:03Z", toolDenialKind="permission-rule",
              message={"content": [{"type": "tool_result", "tool_use_id": "t1", "content": "denied", "is_error": True}]}),
        _line(**base, type="user", uuid="u2", timestamp="2026-10-04T10:00:04Z", promptSource="queued", message={"content": "go on"}),
        _line(**base, type="attachment", uuid="at1", timestamp="2026-10-04T10:00:05Z", attachment={"type": "total_tokens_reminder"},
              rendered=[{"content": "abcde"}, {"content": "xy"}]),
        # Not rendered into the conversation: no context cost, no row.
        _line(**base, type="attachment", uuid="at2", timestamp="2026-10-04T10:00:05Z", attachment={"type": "deferred_tools_record"}),
        _line(**base, type="system", subtype="turn_duration", uuid="sy1", timestamp="2026-10-04T10:01:30Z", durationMs=90_000, messageCount=12),
        _line(**base, type="system", subtype="compact_boundary", uuid="sy2", timestamp="2026-10-04T10:02:00Z",
              compactMetadata={"trigger": "auto", "preTokens": 168_000, "postTokens": 20_000, "durationMs": 4}),
        _line(**base, type="assistant", uuid="e1", timestamp="2026-10-04T10:03:00Z", isApiErrorMessage=True, apiErrorStatus=429, error="rate_limit",
              message={"model": "<synthetic>", "usage": {"input_tokens": 0, "output_tokens": 0}, "content": [{"type": "text", "text": "limit"}]}),
    ]) + "\n")
    return path


# --- REQ-000000360: The transcript backfill keeps what the data explorer plots ---


def test_the_parser_reads_thinking_effort_and_the_transcripts_other_records(tmp_path):
    parsed = transcripts.parse_transcript(_transcript(tmp_path / "-work" / "s1.jsonl"))
    (request,) = parsed["requests"]
    assert (request["thinking_tokens"], request["thinking_ms"], request["effort"]) == (200, 1500, "xhigh")
    events = {(e["id"], e["kind"], e["name"], e["value"]) for e in parsed["transcript_events"]}
    assert events == {("u1", "denial", "permission-rule", None), ("u2", "prompt", "queued", None),
                      ("at1", "insertion", "total_tokens_reminder", 7), ("sy1", "turn", None, 90_000),
                      ("sy2", "compaction", "auto", 148_000), ("e1", "api_error", "429", None)}


def test_ingesting_stores_them_once_and_removing_a_project_drops_them(tmp_path, conn):
    paths = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    paths.ensure_layout()
    root = tmp_path / "projects"
    _transcript(root / "-work" / "s1.jsonl")
    client = TestClient(build_app(conn, paths, projects=lambda: [], agent_usage=lambda since: [], emit=lambda *a: None,
                                  outcome_sources=lambda names: {}, transcripts_root=root))
    client.post("/v1/jobs/usage.ingest_transcripts", json={"job_id": "j1"})
    stored = conn.execute("SELECT kind, name, value, data_json FROM transcript_events WHERE id = 'sy2'").fetchone()
    assert (stored["kind"], stored["name"], stored["value"]) == ("compaction", "auto", 148_000)
    assert json.loads(stored["data_json"]) == {"pre_tokens": 168_000, "post_tokens": 20_000}
    assert conn.execute("SELECT thinking_tokens, effort FROM requests WHERE request_id = 'r1'").fetchone()[:] == (200, "xhigh")
    # The file is read again only when it changes or the parser learns something: nothing doubles either way.
    conn.execute("DELETE FROM meta")
    client.post("/v1/jobs/usage.ingest_transcripts", json={"job_id": "j2"})
    assert conn.execute("SELECT COUNT(*) FROM transcript_events").fetchone()[0] == 6
    db.set_attribution(conn, "s1", "little-meals", "interactive")
    db.purge_project(conn, "little-meals")
    assert conn.execute("SELECT COUNT(*) FROM transcript_events").fetchone()[0] == 0


def test_an_older_store_gains_the_request_columns(tmp_path):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE requests (request_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, ts TEXT NOT NULL, day TEXT NOT NULL, "
                "model TEXT NOT NULL DEFAULT '', input_tokens INTEGER NOT NULL DEFAULT 0, cache_read_tokens INTEGER NOT NULL DEFAULT 0, "
                "cache_write_tokens INTEGER NOT NULL DEFAULT 0, cache_write_1h_tokens INTEGER, output_tokens INTEGER NOT NULL DEFAULT 0, "
                "cost_usd REAL, query_source TEXT, agent_name TEXT, skill_name TEXT, duration_ms INTEGER, prompt_id TEXT, source TEXT NOT NULL)")
    old.commit()
    old.close()
    conn = db.connect(path)
    assert {"thinking_tokens", "thinking_ms", "effort"} <= {row["name"] for row in conn.execute("PRAGMA table_info(requests)")}
    conn.close()


# --- REQ-000000361: The data explorer plots the kept data, one card at a time ---


def _request(conn, request_id, day, effort, thinking, output):
    db.upsert_request(conn, {"request_id": request_id, "session_id": "s1", "ts": f"{day}T12:00:00Z", "model": "m",
                             "output_tokens": output, "thinking_tokens": thinking, "effort": effort, "source": "test"})


def _event(conn, event_id, day, kind, name="", value=None, data=None):
    db.upsert_transcript_event(conn, {"id": event_id, "session_id": "s1", "ts": f"{day}T12:00:00Z", "kind": kind, "name": name,
                                      "value": value, "data": data})


def _cards(conn) -> dict:
    built = explorer.build(conn, days=3, today=date.fromisoformat(D3))
    assert built["days"] == [D1, D2, D3]
    return {card["id"]: card for card in built["cards"]}


def _series(card) -> dict:
    return {s["name"]: s["values"] for s in card["series"]}


def test_every_card_says_what_it_shows_and_where_its_data_comes_from(conn):
    cards = explorer.build(conn, days=3, today=date.fromisoformat(D3))["cards"]
    assert [c["id"] for c in cards] == ["thinking", "compactions", "turn_time", "insertions", "prompt_sources", "denials", "rate_limits", "test_runs"]
    for card in cards:
        assert card["title"] and "From " in card["about"] and card["kind"] in ("stack", "line") and card["note"] is None


def test_thinking_compactions_and_turn_time(conn):
    _request(conn, "r1", D1, "high", 100, 400)
    _request(conn, "r2", D1, "low", 50, 100)
    _request(conn, "r3", D2, None, 10, 20)
    _event(conn, "c1", D1, "compaction", "auto", 80_000, {"pre_tokens": 100_000, "post_tokens": 20_000})
    _event(conn, "c2", D1, "compaction", "auto", 130_000, {"pre_tokens": 160_000, "post_tokens": 30_000})
    _event(conn, "c3", D3, "compaction", "manual", 40_000, {"pre_tokens": 50_000, "post_tokens": 10_000})
    for i, ms in enumerate((60_000, 120_000, 600_000)):
        _event(conn, f"t{i}", D1, "turn", value=ms)
    cards = _cards(conn)
    assert list(_series(cards["thinking"]).items()) == [("low", [50, 0, 0]), ("high", [100, 0, 0]), ("not recorded", [0, 10, 0])]
    assert cards["thinking"]["note"] == "Thinking is 31% of output tokens (low 50%, high 25%, not recorded 50%)."
    assert _series(cards["compactions"]) == {"auto": [2, 0, 0], "manual": [0, 0, 1]}
    assert cards["compactions"]["note"] == "3 compactions; the median one went from 100k to 20k tokens."
    assert _series(cards["turn_time"]) == {"median turn": [2.0, None, None], "slowest 10%": [10.0, None, None]}
    assert cards["turn_time"]["note"] == "3 turns; median 2.0 min, the slowest 10% over 10.0 min."


def test_insertions_keep_the_largest_kinds_and_add_up_the_rest(conn):
    for i, kind in enumerate("abcdefg"):
        _event(conn, f"i{i}", D2, "insertion", kind, 1_000 * (7 - i))
    series = _series(_cards(conn)["insertions"])
    assert list(series) == ["a", "b", "c", "d", "e", "other"] and series["other"] == [0, 3_000, 0]


def test_prompt_sources_denials_rate_limits_and_test_runs(conn):
    _event(conn, "p1", D1, "prompt", "typed")
    for i in range(3):
        _event(conn, f"q{i}", D2, "prompt", "queued")
    _event(conn, "d1", D3, "denial", "automode-blocked")
    _event(conn, "e1", D2, "api_error", "429")
    _event(conn, "e2", D2, "api_error", "429")
    db.add_plan_pressure(conn, f"{D2}T09:00:00Z", "s1", 40.0, 10.0)
    db.add_plan_pressure(conn, f"{D2}T15:00:00Z", "s1", 85.0, 12.0)
    for call_id, day, outcome in (("c1", D1, "ok"), ("c2", D1, "failed"), ("c3", D3, "ok")):
        db.upsert_tool_call(conn, {"id": call_id, "session_id": "s1", "ts": f"{day}T12:00:00Z", "tool": "Bash", "signature": "Bash(pytest)",
                                   "source": "test", "outcome": outcome, "tests_passed": 3, "tests_failed": int(outcome == "failed")})
    db.upsert_tool_call(conn, {"id": "c4", "session_id": "s1", "ts": f"{D1}T12:00:00Z", "tool": "Bash", "signature": "Bash(ls)", "source": "test",
                               "outcome": "failed"})  # not a test run
    cards = _cards(conn)
    assert _series(cards["prompt_sources"]) == {"typed": [1, 0, 0], "queued": [0, 3, 0]}
    assert cards["prompt_sources"]["note"] == "4 prompts with a recorded source; 75% were queued while Claude was working."
    assert _series(cards["denials"]) == {"automode-blocked": [0, 0, 1]}
    limits = cards["rate_limits"]["series"]
    assert limits[0] == {"name": "API error 429", "values": [0, 2, 0]}
    assert limits[1] == {"name": "5-hour window, highest", "values": [None, 85.0, None], "axis": "right", "unit": "percent"}
    assert _series(cards["test_runs"]) == {"passed": [1, 0, 1], "failed": [1, 0, 0]}
    assert cards["test_runs"]["note"] == "3 test runs, 1 failed (33%)."


def test_the_pillar_serves_the_cards(tmp_path, conn):
    paths = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    paths.ensure_layout()
    client = TestClient(build_app(conn, paths, projects=lambda: [], agent_usage=lambda since: [], emit=lambda *a: None,
                                  outcome_sources=lambda names: {}))
    served = client.get("/v1/explorer", params={"days": 500}).json()
    assert len(served["days"]) == explorer.MAX_DAYS and len(served["cards"]) == len(explorer.CARDS)
