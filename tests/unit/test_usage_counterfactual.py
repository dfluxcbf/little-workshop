"""The before/after estimate of automating a candidate, made by replaying its past occurrences against the
sessions they happened in (src/usage/counterfactual.py, docs/pillars/usage.md "What automating would
save"), and the subagent transcripts the replay needs."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from common.paths import Paths
from usage import counterfactual, db, pricing, transcripts
from usage.app import build_app

PRICES = pricing.DEFAULT_PRICES
START = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)
CAL = counterfactual.Calibration(bytes_per_token=2.5, call_overhead=100.0, call_output=300.0)


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "usage.db")
    yield connection
    connection.close()


def thread(conn, session, contexts, *, outputs=None, agent=None, model="claude-sonnet-5-5"):
    """A thread of requests, one a minute, with the given context sizes (all read from cache)."""
    ids = []
    for i, context in enumerate(contexts):
        request_id = f"{session}-{agent or 'main'}-{i}"
        db.upsert_request(conn, {"request_id": request_id, "session_id": session, "ts": (START + timedelta(minutes=i)).isoformat(),
                                 "model": model, "input_tokens": 0, "cache_read_tokens": context,
                                 "output_tokens": (outputs or {}).get(i, 100), "query_source": "subagent" if agent else "main",
                                 "agent_name": agent, "source": "test"})
        ids.append(request_id)
    conn.commit()
    return ids


def call(session, request_id, minute, result_bytes, call_id):
    return {"id": call_id, "session_id": session, "request_id": request_id, "ts": (START + timedelta(minutes=minute, seconds=5)).isoformat(),
            "result_bytes": result_bytes}


def cost(context, output=100):
    return sum(pricing.weighted({"model": "claude-sonnet-5-5", "cache_read_tokens": context, "output_tokens": output}, PRICES).values())


READ = PRICES["sonnet"].input * PRICES["sonnet"].cache_read / 1e6  # what one context token read from cache costs


# --- REQ-000000357: The estimate replays each occurrence and says how well its constants fit ---


def test_an_occurrence_is_replayed_against_its_own_thread(conn):
    contexts = [10_000, 20_000, 30_000, 31_000, 32_000, 40_000, 41_000, 42_000]
    ids = thread(conn, "s1", contexts, outputs={2: 200, 3: 150, 4: 120})
    calls = [call("s1", ids[2], 2, 1000, "a"), call("s1", ids[3], 3, 2000, "b"), call("s1", ids[4], 4, 500, "c")]
    replay = counterfactual.Replay(conn, PRICES)
    result = replay.occurrence(calls, CAL, trigger_ok=True)
    added = (200 + 150 + 120) + 3 * 100 + 3500 / 2.5  # what its turns wrote, three calls' overhead, its results
    later = 3 * READ  # requests 5, 6 and 7 re-read it, each token at the cache-read price
    kept, dropped = cost(30_000, 200), cost(31_000, 150) + cost(32_000, 120)
    before = kept + dropped + added * later
    replacement = 200 + 100 + 400 / 2.5  # the kept turn's text, one call, a 400-byte answer
    assert result["turns"] == 3 and result["later_requests"] == 3 and result["added_tokens"] == pytest.approx(added)
    assert result["triggered"] == pytest.approx(before)
    assert result["called"] == pytest.approx(dropped + (added - replacement) * later)
    check = cost(40_000) + (300 + 100 + 400 / 2.5) * later  # one more turn to check on it, like the request after the occurrence
    assert result["called_low"] == pytest.approx(result["called"] - check)
    assert result["triggered_low"] == pytest.approx(before - check)
    assert replay.occurrence(calls, CAL, trigger_ok=False)["triggered"] is None


def test_a_compaction_or_clear_ends_the_re_reading(conn):
    ids = thread(conn, "s1", [10_000, 50_000, 51_000, 52_000, 8_000, 9_000, 10_000])  # request 4 follows a compaction
    result = counterfactual.Replay(conn, PRICES).occurrence([call("s1", ids[1], 1, 5000, "a")], CAL, trigger_ok=False)
    assert result["later_requests"] == 2  # requests 2 and 3; nothing after the compaction re-reads it


def test_calls_made_side_by_side_take_one_turn(conn):
    ids = thread(conn, "s1", [10_000, 20_000, 21_000, 22_000])
    result = counterfactual.Replay(conn, PRICES).occurrence(
        [call("s1", ids[1], 1, 100, "a"), call("s1", ids[1], 1, 100, "b")], CAL, trigger_ok=False)
    assert result["turns"] == 1
    assert result["called"] == pytest.approx(((100 + 2 * 100 + 200 / 2.5) - (100 + 100 + 400 / 2.5)) * 2 * READ)


def test_a_call_without_a_request_id_is_placed_by_time_in_the_main_thread(conn):
    ids = thread(conn, "s1", [10_000, 20_000, 30_000, 31_000])
    replay = counterfactual.Replay(conn, PRICES)
    located = replay.locate({"session_id": "s1", "request_id": None, "ts": (START + timedelta(minutes=2, seconds=30)).isoformat()})
    assert located == (("s1", counterfactual.MAIN), 2)
    assert replay.locate({"session_id": "nobody", "request_id": None, "ts": START.isoformat()}) is None


def test_a_subagent_is_a_thread_of_its_own(conn):
    main = thread(conn, "s1", [10_000, 20_000, 30_000, 40_000, 50_000])
    sub = thread(conn, "s1", [5_000, 6_000, 7_000], agent="agent-abc")
    replay = counterfactual.Replay(conn, PRICES)
    assert set(replay.threads) == {("s1", counterfactual.MAIN), ("s1", "agent-abc")}
    result = replay.occurrence([call("s1", sub[0], 0, 100, "a")], CAL, trigger_ok=False)
    assert result["later_requests"] == 2  # the subagent's own later requests, not the main conversation's
    across = replay.occurrence([call("s1", main[0], 0, 100, "a"), call("s1", sub[1], 1, 100, "b")], CAL, trigger_ok=False)
    assert across is None  # one occurrence never spans two threads


def test_the_constants_are_fitted_on_the_stored_turns_and_checked_on_the_newest(conn):
    # 300 turns whose growth is exactly what the model says: output + 1 call × 97 + result bytes / 2.4.
    contexts, outputs, results, context = [], {}, [], 10_000
    for i in range(301):
        out, result = 200 + (i % 7) * 30, 5000 + i if i % 2 else 10  # every other call returns almost nothing
        contexts.append(context)
        outputs[i] = out
        results.append(result)
        context += round(out + 97 + result / 2.4)
    ids = thread(conn, "s1", contexts, outputs=outputs)
    for i in range(300):
        db.upsert_tool_call(conn, {"id": f"c{i}", "session_id": "s1", "request_id": ids[i], "ts": (START + timedelta(minutes=i, seconds=5)).isoformat(),
                                   "tool": "Bash", "signature": "Bash(x)", "result_bytes": results[i], "source": "test"})
    conn.commit()
    calibration = counterfactual.calibrate(conn, counterfactual.Replay(conn, PRICES))
    assert calibration.turns == 300
    assert calibration.bytes_per_token == pytest.approx(2.4, rel=0.01)
    assert calibration.call_overhead == pytest.approx(97 + 10 / 2.4, abs=1)  # the overhead, with the few tokens a tiny result adds
    assert calibration.check["turns"] == 90  # the newest 30%
    assert calibration.check["median_abs_error_tokens"] < 5 and abs(calibration.check["total_error"]) < 0.01  # rounding only


def test_too_few_turns_keep_the_measured_fallback_constants(conn):
    ids = thread(conn, "s1", [10_000, 11_000])
    calibration = counterfactual.calibrate(conn, counterfactual.Replay(conn, PRICES))
    assert (calibration.bytes_per_token, calibration.call_overhead, calibration.turns) == (2.4, 97.0, 0)
    assert "fallback" in calibration.check["note"]


def test_the_method_report_says_what_the_estimates_rest_on(conn):
    thread(conn, "s1", [10_000, 20_000, 30_000, 5_000, 6_000])
    thread(conn, "s1", [1_000, 2_000], agent="agent-x")
    replay = counterfactual.Replay(conn, PRICES)
    report = counterfactual.method_report(conn, replay, counterfactual.calibrate(conn, replay))
    assert report["threads"] == 2 and report["subagent_threads"] == 1
    assert report["calibration"]["bytes_per_token"] == 2.4 and report["later_requests_median"] is not None


# --- REQ-000000357: Subagent transcripts are read as threads of their session ---


def _write(path, lines):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")


def _assistant(session, request_id, ts, *, tool_id=None, sidechain=False):
    content = [{"type": "tool_use", "id": tool_id, "name": "Bash", "input": {"command": "ls"}}] if tool_id else [{"type": "text", "text": "ok"}]
    return {"type": "assistant", "sessionId": session, "timestamp": ts, "requestId": request_id, "isSidechain": sidechain,
            "cwd": "/sub/dir" if sidechain else "/main/dir", "version": "2.1.289",
            "message": {"model": "claude-sonnet-5-5", "usage": {"input_tokens": 1, "cache_read_input_tokens": 1000, "output_tokens": 5}, "content": content}}


def test_a_subagent_transcript_is_read_as_its_own_thread(tmp_path):
    root = tmp_path / "projects"
    main = root / "-work" / "sess-1.jsonl"
    _write(main, [{"type": "user", "sessionId": "sess-1", "timestamp": "2026-10-01T09:00:00Z", "message": {"content": "go"}},
                  _assistant("sess-1", "req-m", "2026-10-01T09:00:01Z", tool_id="tm")])
    agent = root / "-work" / "sess-1" / "subagents" / "agent-abc.jsonl"
    _write(agent, [{"type": "user", "sessionId": "sess-1", "timestamp": "2026-10-01T09:00:02Z", "isSidechain": True, "message": {"content": "task"}},
                   _assistant("sess-1", "req-s", "2026-10-01T09:00:03Z", tool_id="ts", sidechain=True)])
    assert transcripts.transcript_files(root) == sorted([main, agent])
    assert transcripts.subagent_files(main) == [agent]
    parsed = transcripts.parse_transcript(agent)
    (request,) = parsed["requests"]
    assert (request["query_source"], request["agent_name"]) == ("subagent", "agent-abc")
    assert parsed["tool_calls"][0]["prompt_id"] == "sess-1:agent-abc:1"  # numbered apart from the main conversation's prompts
    assert parsed["session"] == {"id": "sess-1", "seen": "2026-10-01T09:00:03Z", "first": "2026-10-01T09:00:02Z"}  # nothing about the session


def test_a_session_end_reads_the_sessions_subagents_too(tmp_path):
    paths = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    paths.ensure_layout()
    connection = db.connect(tmp_path / "usage.db")
    root = tmp_path / "projects"
    main = root / "-work" / "sess-1.jsonl"
    _write(main, [_assistant("sess-1", "req-m", "2026-10-01T09:00:01Z")])
    _write(root / "-work" / "sess-1" / "subagents" / "agent-abc.jsonl", [_assistant("sess-1", "req-s", "2026-10-01T09:00:03Z", sidechain=True)])
    client = TestClient(build_app(connection, paths, projects=lambda: [], agent_usage=lambda since: [], emit=lambda *a: None,
                                  outcome_sources=lambda names: {}))
    client.post("/v1/hooks/SessionEnd", json={"session_id": "sess-1", "cwd": "/main/dir", "transcript_path": str(main), "reason": "other"})
    import time
    for _ in range(200):
        if connection.execute("SELECT COUNT(*) FROM requests").fetchone()[0] == 2:
            break
        time.sleep(0.02)
    rows = {row["request_id"]: row["query_source"] for row in connection.execute("SELECT request_id, query_source FROM requests")}
    assert rows == {"req-m": "main", "req-s": "subagent"}
    session = db.get_session(connection, "sess-1")
    assert session["transcript_path"] == str(main) and session["cwd"] == "/main/dir"  # the subagent's did not overwrite them
    connection.close()


def test_mining_keeps_the_method_report_for_the_page(conn, tmp_path):
    paths = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    paths.ensure_layout()
    thread(conn, "s1", [10_000, 20_000, 30_000])
    app = build_app(conn, paths, projects=lambda: [], agent_usage=lambda since: [], emit=lambda *a: None, outcome_sources=lambda names: {})
    client = TestClient(app)
    assert client.get("/v1/estimate_method").json() == {"method": None}
    app.state.mine_automation()
    method = client.get("/v1/estimate_method").json()["method"]
    assert method["calibration"]["bytes_per_token"] == 2.4 and method["threads"] == 1
