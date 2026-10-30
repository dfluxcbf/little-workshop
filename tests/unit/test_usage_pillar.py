"""M19: the Usage & Efficiency pillar's collection (docs/pillars/usage.md). The OTLP and hook bodies
here have the shapes a real export had (Claude Code 2.1.289); the transcript lines are synthetic."""

import json
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent import usage_client
from agent.claude_invoke import setenv_args, with_env
from agent.flow import AgentFlow
from cli import main as cli_main
from common import manifest, redeploy_plan
from common.paths import PILLARS, Paths
from common.shell import CommandResult
from registry import claude_templates
from usage import attribution, db, doctor, intake, pricing, profile, proxy, rollup, session_title, signatures, transcripts
from usage.app import build_app

SESSION = "7a3414cf-1311-4bc2-9381-9ad58a302746"
NOW = datetime.now(timezone.utc).replace(microsecond=0)


def _attrs(values: dict) -> list[dict]:
    return [{"key": key, "value": {"stringValue": str(value)}} for key, value in values.items()]


def otlp(*records: dict, resource: dict | None = None) -> dict:
    return {"resourceLogs": [{
        "resource": {"attributes": _attrs({"service.name": "claude-code", "service.version": "2.1.289", **(resource or {})})},
        "scopeLogs": [{"logRecords": [{"timeUnixNano": "1791069378072000000", "attributes": _attrs(r)} for r in records]}],
    }]}


def api_request(request_id="req_1", *, session=SESSION, ts=None, model="claude-sonnet-5-5", read=9000, write=500, out=70, **extra) -> dict:
    return {"session.id": session, "event.name": "api_request", "event.timestamp": (ts or NOW).isoformat().replace("+00:00", "Z"),
            "prompt.id": "p1", "model": model, "input_tokens": 10, "output_tokens": out, "cache_read_tokens": read,
            "cache_creation_tokens": write, "cost_usd": "0.0284993", "duration_ms": "1787", "request_id": request_id,
            "query_source": "main", **extra}


def tool_result(tool_use_id="toolu_1", *, command="python3 -m pytest tests/unit -q") -> dict:
    return {"session.id": SESSION, "event.name": "tool_result", "event.timestamp": NOW.isoformat(), "prompt.id": "p1",
            "tool_name": "Bash", "tool_use_id": tool_use_id, "success": "true", "duration_ms": "281",
            "tool_input": json.dumps({"command": command}), "tool_input_size_bytes": "57", "tool_result_size_bytes": "20480"}


@pytest.fixture
def paths(tmp_path):
    p = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    p.ensure_layout()
    return p


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "usage.db")
    yield connection
    connection.close()


@pytest.fixture
def checkouts(tmp_path):
    root = tmp_path / "little-projects"
    for name in ("little-workshop", "little-meals"):
        (root / name).mkdir(parents=True)
    return [{"name": "little-workshop", "path": str(root / "little-workshop"), "repo_url": "git@github.com:dfluxcbf/little-workshop.git"},
            {"name": "little-meals", "path": str(root / "little-meals"), "repo_url": "https://github.com/dfluxcbf/little-meals"}]


class FakeRunner:
    def __init__(self, active=True, doctor_ok=True):
        self.active, self.doctor_ok, self.calls = active, doctor_ok, []

    def run(self, argv, timeout=None, cwd=None):
        self.calls.append(argv)
        if argv[:3] == ["systemctl", "--user", "is-active"]:
            return CommandResult(0 if self.active else 3, "active\n" if self.active else "inactive\n", "")
        if argv == ["claude", "doctor"]:
            return CommandResult(0 if self.doctor_ok else 1, "All checks passed", "")
        return CommandResult(0, "", "")


@pytest.fixture
def client(conn, paths, checkouts, tmp_path):
    app = build_app(conn, paths, runner=FakeRunner(), projects=lambda: checkouts, agent_usage=lambda since: [],
                    transcripts_root=tmp_path / "claude-projects", settings_path=tmp_path / "home" / "settings.json")
    test_client = TestClient(app)
    test_client.tick = app.state.tick
    return test_client


# --- REQ-000000324: The intake stores each API request and tool call once ---


def test_an_otlp_export_becomes_request_and_tool_call_rows(client, conn):
    assert client.post("/v1/logs", json=otlp(api_request(), tool_result(), {"session.id": SESSION, "event.name": "user_prompt"})).json() == {}
    request = dict(conn.execute("SELECT * FROM requests").fetchone())
    assert (request["request_id"], request["session_id"], request["model"]) == ("req_1", SESSION, "claude-sonnet-5-5")
    assert (request["input_tokens"], request["cache_read_tokens"], request["cache_write_tokens"], request["output_tokens"]) == (10, 9000, 500, 70)
    assert request["cost_usd"] == pytest.approx(0.0284993) and request["source"] == "otel" and request["prompt_id"] == "p1"
    call = dict(conn.execute("SELECT * FROM tool_calls").fetchone())
    assert call["tool"] == "Bash" and call["signature"] == "Bash(python3 -m pytest <path>)"
    assert call["result_bytes"] == 20480 and call["success"] == 1 and call["detail"].startswith("python3 -m pytest")
    assert db.get_session(conn, SESSION)["cc_version"] == "2.1.289"


def test_the_same_request_sent_twice_is_counted_once(client, conn):
    body = otlp(api_request(), tool_result())
    client.post("/v1/logs", json=body)
    client.post("/v1/logs", json=body)
    assert conn.execute("SELECT COUNT(*) FROM requests").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM tool_calls").fetchone()[0] == 1


def test_the_intake_never_fails_a_session(client):
    assert client.post("/v1/logs", content=b"not json", headers={"content-type": "application/json"}).status_code == 200
    assert client.post("/v1/logs", json={"resourceLogs": [{"scopeLogs": [{"logRecords": [{"attributes": "odd"}]}]}]}).status_code == 200
    assert client.post("/v1/metrics", json={"resourceMetrics": []}).json() == {}
    assert client.post("/v1/hooks/SessionEnd", json={}).status_code == 200
    assert client.post("/v1/statusline", json={"rate_limits": {}}).status_code == 200


def test_prompt_and_response_text_is_never_stored(client, conn):
    client.post("/v1/logs", json=otlp({"session.id": SESSION, "event.name": "user_prompt", "prompt": "my secret"},
                                      {"session.id": SESSION, "event.name": "assistant_response", "response": "the reply"}))
    dump = "".join(str(tuple(row)) for table in ("requests", "tool_calls", "sessions", "session_events") for row in conn.execute(f"SELECT * FROM {table}"))
    assert "my secret" not in dump and "the reply" not in dump


# --- REQ-000000325: Hooks and the statusline tell where a session ran and how full the plan is ---


def test_a_session_end_hook_records_the_directory_the_transcript_and_the_reason(client, conn, checkouts):
    cwd = checkouts[1]["path"] + "/src"
    client.post("/v1/hooks/SessionEnd", json={"session_id": SESSION, "cwd": cwd, "transcript_path": "/nowhere/x.jsonl",
                                               "hook_event_name": "SessionEnd", "reason": "clear"})
    session = db.get_session(conn, SESSION)
    assert (session["cwd"], session["end_reason"], session["transcript_path"]) == (cwd, "clear", "/nowhere/x.jsonl")
    assert session["project"] == "little-meals"
    event = conn.execute("SELECT name, data_json FROM session_events").fetchone()
    assert event["name"] == "SessionEnd" and json.loads(event["data_json"]) == {"reason": "clear"}
    client.post("/v1/hooks/NotAnEvent", json={"session_id": "other"})
    assert db.get_session(conn, "other") is None


def test_a_statusline_post_records_plan_pressure(client, conn):
    client.post("/v1/statusline", json={"session_id": SESSION, "rate_limits": {"five_hour": {"used_percentage": 85.5}, "seven_day": {"used_percentage": 40}}})
    row = db.latest_plan_pressure(conn)
    assert (row["five_hour_pct"], row["seven_day_pct"], row["session_id"]) == (85.5, 40.0, SESSION)
    pressure = client.get("/v1/plan_pressure").json()
    assert pressure["five_hour_percent"] == 85.5 and pressure["threshold_percent"] == 80 and pressure["hold"] is True


def test_no_reading_or_an_old_one_never_holds_anything(client, conn):
    assert client.get("/v1/plan_pressure").json()["hold"] is False
    db.add_plan_pressure(conn, (NOW - timedelta(hours=6)).isoformat(), SESSION, 99.0, 50.0)
    conn.commit()
    assert client.get("/v1/plan_pressure").json()["hold"] is False


# --- REQ-000000326: The transcript backfill covers what telemetry missed ---


def _transcript(path: Path, *, cwd: str, request_id="req_t1") -> None:
    usage = {"input_tokens": 3, "cache_read_input_tokens": 400000, "cache_creation_input_tokens": 1000, "output_tokens": 50,
             "cache_creation": {"ephemeral_1h_input_tokens": 1000, "ephemeral_5m_input_tokens": 0}}
    base = {"sessionId": "sess-t", "cwd": cwd, "version": "2.1.280", "entrypoint": "cli", "timestamp": NOW.isoformat()}
    lines = [
        {**base, "type": "user", "message": {"role": "user", "content": "hello"}},
        {**base, "type": "assistant", "requestId": request_id, "message": {"model": "claude-fable-5-1", "usage": usage, "content": [{"type": "text", "text": "hi"}]}},
        {**base, "type": "assistant", "requestId": request_id, "message": {"model": "claude-fable-5-1", "usage": usage, "content": [
            {"type": "tool_use", "id": "toolu_t1", "name": "mcp__little-workshop__workshop_job_status", "input": {"job_id": "abc"}}]}},
        {**base, "type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_t1", "content": "x" * 300}]}},
        {**base, "type": "assistant", "requestId": "req_synthetic", "message": {"model": "<synthetic>", "usage": usage, "content": []}},
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\nthis line is not json\n[1, 2]\n", encoding="utf-8")


def test_a_transcript_is_parsed_tolerantly(tmp_path):
    path = tmp_path / "p" / "sess-t.jsonl"
    _transcript(path, cwd="/work/x")
    parsed = transcripts.parse_transcript(path)
    assert parsed["skipped"] == 2
    assert parsed["session"]["cwd"] == "/work/x" and parsed["session"]["cc_version"] == "2.1.280" and parsed["session"]["entrypoint"] == "cli"
    (request,) = parsed["requests"]  # the two lines of one request are one request; the synthetic message is none
    assert request["cache_read_tokens"] == 400000 and request["cache_write_1h_tokens"] == 1000 and request["source"] == "transcript"
    (call,) = parsed["tool_calls"]
    assert call["signature"] == "mcp:workshop_job_status(job_id)" and call["result_bytes"] == 300 and call["success"] is True
    assert transcripts.parse_transcript(tmp_path / "missing.jsonl")["session"] is None


def test_the_backfill_job_counts_nothing_twice_and_fills_what_telemetry_left_out(client, conn, checkouts, tmp_path):
    _transcript(tmp_path / "claude-projects" / "-work" / "sess-t.jsonl", cwd=checkouts[0]["path"])
    client.post("/v1/logs", json=otlp(api_request("req_t1", session="sess-t", model="claude-fable-5-1", read=400000, write=1000, out=50)))
    first = client.post("/v1/jobs/usage.ingest_transcripts", json={"job_id": "j1"}).json()
    assert first["outcome"] == "succeeded" and first["result"]["files_read"] == 1 and first["result"]["requests"] == 0
    request = dict(conn.execute("SELECT * FROM requests").fetchone())
    assert request["source"] == "otel" and request["cache_write_1h_tokens"] == 1000  # the transcript added the 1-hour share
    assert db.get_session(conn, "sess-t")["project"] == "little-workshop"

    second = client.post("/v1/jobs/usage.ingest_transcripts", json={"job_id": "j2"}).json()
    assert second["result"]["files"] == 1 and second["result"]["files_read"] == 0  # unchanged file: not read again
    assert conn.execute("SELECT COUNT(*) FROM requests").fetchone()[0] == 1


def test_a_session_end_hook_backfills_that_sessions_transcript(client, conn, tmp_path):
    path = tmp_path / "elsewhere" / "sess-t.jsonl"
    _transcript(path, cwd="/work/x")
    client.post("/v1/hooks/SessionEnd", json={"session_id": "sess-t", "cwd": "/work/x", "transcript_path": str(path), "reason": "other"})
    for _ in range(200):  # the backfill runs beside the hook's answer; its file mark is the last thing it writes
        if db.get_meta(conn, f"transcript:{path}"):
            break
        time.sleep(0.02)
    assert conn.execute("SELECT COUNT(*) FROM requests").fetchone()[0] == 1


# --- REQ-000000327: Each session is attributed to a project and an origin ---


def test_the_job_tag_wins_then_the_longest_checkout_then_the_repository_then_the_workspace(checkouts, tmp_path):
    workshop, meals = checkouts[0]["path"], checkouts[1]["path"]
    assert attribution.project_of({"tag_project": "little-home", "cwd": meals}, checkouts) == "little-home"
    assert attribution.project_of({"cwd": meals + "/src/deep"}, checkouts) == "little-meals"
    assert attribution.project_of({"cwd": workshop + "/.claude/worktrees/lws-docs-1"}, checkouts) == "little-workshop"
    assert attribution.project_of({"cwd": "/somewhere/else", "repository": "https://github.com/dfluxcbf/little-meals.git"}, checkouts) == "little-meals"
    assert attribution.project_of({"cwd": str(tmp_path / "little-projects")}, checkouts) == "little-projects"
    assert attribution.project_of({"cwd": str(tmp_path / "little-projects" / "little-meshs")}, checkouts) == "little-projects"
    assert attribution.project_of({"cwd": "/tmp/unrelated"}, checkouts) == "unattributed"
    assert attribution.project_of({}, checkouts) == "unattributed"


def test_a_git_worktree_elsewhere_is_traced_to_its_repository(checkouts, tmp_path):
    worktree = tmp_path / "state" / "worktrees" / "abc123"
    worktree.mkdir(parents=True)
    (worktree / ".git").write_text(f"gitdir: {checkouts[1]['path']}/.git/worktrees/abc123\n", encoding="utf-8")
    assert attribution.project_of({"cwd": str(worktree / "src")}, checkouts) == "little-meals"


def test_the_origin_says_whether_a_job_a_query_or_a_person_started_the_session():
    assert attribution.origin_of({"job_type": "agent.query"}) == "query"
    assert attribution.origin_of({"job_type": "agent.run_task", "job_id": "j"}) == "job"
    assert attribution.origin_of({}) == "interactive"


def test_tagged_telemetry_is_attributed_exactly(client, conn):
    client.post("/v1/logs", json=otlp(api_request(), resource={"lws.project": "little-meals", "lws.job": "j1", "lws.job_type": "agent.run_task", "lws.run": "r1"}))
    session = db.get_session(conn, SESSION)
    assert (session["project"], session["origin"], session["job_id"], session["run_id"]) == ("little-meals", "job", "j1", "r1")


# --- REQ-000000328: Requests are compiled into per-day rollups, metrics and weeks ---


def test_weighted_tokens_price_each_token_type_per_model():
    prices = pricing.DEFAULT_PRICES
    request = {"model": "claude-haiku-4-5-20251001", "input_tokens": 10, "output_tokens": 120, "cache_read_tokens": 12413, "cache_write_tokens": 13324}
    one_hour = pricing.weighted({**request, "cache_write_1h_tokens": 13324}, prices)
    assert sum(one_hour.values()) == pytest.approx(0.0284993, abs=1e-6)  # what Claude Code itself estimated for this request
    assert sum(pricing.weighted({**request, "cost_usd": 0.0284993}, prices).values()) == pytest.approx(0.0284993)
    assert pricing.weighted(request, prices)["cache_write"] == pytest.approx(13324 * 1.25 / 1e6)
    assert pricing.price_for("claude-fable-5-1", prices).cache_read == 0.025 and pricing.price_for("something-new", prices) == prices[""]


def test_the_price_table_can_be_overridden(tmp_path):
    (tmp_path / pricing.PRICING_FILE).write_text(json.dumps({"opus": {"input": 8, "output": 40, "cache_read": 0.1}, "bad": {"input": "x"}}))
    prices = pricing.load_prices(tmp_path)
    assert prices["opus"].input == 8 and "bad" not in prices and prices["haiku"] == pricing.DEFAULT_PRICES["haiku"]


def test_a_day_rollup_carries_the_named_metrics(client, conn):
    client.post("/v1/hooks/SessionEnd", json={"session_id": SESSION, "cwd": "/tmp/x", "reason": "other"})
    client.post("/v1/logs", json=otlp(
        api_request("r1", ts=NOW - timedelta(seconds=30), read=40000, write=4000, out=100),
        api_request("r2", ts=NOW - timedelta(seconds=20), read=300000, write=0, out=100),
        api_request("r3", ts=NOW - timedelta(seconds=10), read=1000, write=0, out=10, query_source="subagent")))
    client.tick()
    today = date.fromisoformat(db.local_day(NOW.isoformat()))
    (day,) = rollup.days(conn, today, today)
    assert day["requests"] == 3 and day["cache_read_tokens"] == 341000
    assert day["cache_hit_ratio"] == pytest.approx(341000 / (341000 + 4000 + 30))
    assert day["average_context"] == pytest.approx((44010 + 300010 + 1010) / 3)
    assert day["long_context_share"] == pytest.approx(300010 / (44010 + 300010 + 1010))
    assert day["weighted"] > 0
    assert db.get_session(conn, SESSION)["startup_context"] == 44010  # the first main-thread request
    assert {row["origin"] for row in conn.execute("SELECT origin FROM day_rollups")} == {"interactive", "subagent"}
    assert rollup.days(conn, today, today, origin="subagent")[0]["requests"] == 1


def test_a_rollup_outlives_the_requests_it_was_built_from(client, conn):
    client.post("/v1/logs", json=otlp(api_request("old", ts=NOW - timedelta(days=200))))
    client.tick()  # builds the day, then prunes requests past the retention
    assert conn.execute("SELECT COUNT(*) FROM requests").fetchone()[0] == 0
    assert conn.execute("SELECT SUM(requests) FROM day_rollups").fetchone()[0] == 1
    client.tick()
    assert conn.execute("SELECT SUM(requests) FROM day_rollups").fetchone()[0] == 1


def test_a_week_starts_on_monday_and_is_compared_only_with_weeks_that_have_data(conn):
    assert rollup.monday_of(date(2026, 10, 4)) == date(2026, 9, 28)  # a Sunday belongs to the week of the Monday before
    for day, requests in (("2026-09-21", 2), ("2026-09-30", 5)):
        conn.execute("INSERT INTO day_rollups (day, project, origin, model, requests, weighted_output) VALUES (?, 'little-workshop', 'interactive', 'm', ?, ?)",
                     (day, requests, float(requests)))
    conn.execute("INSERT INTO day_rollups (day, project, origin, model, requests, weighted_output) VALUES ('2026-09-30', 'little-meals', 'interactive', 'm', 1, 0.5)")
    summary = rollup.summary(conn, pricing.DEFAULT_PRICES, week="2026-10-02", today=date(2026, 10, 4))
    assert (summary["week"], summary["week_end"], summary["complete"]) == ("2026-09-28", "2026-10-04", False)
    assert [d["day"] for d in summary["days"]] == [f"2026-09-{d}" for d in (28, 29, 30)] + [f"2026-10-0{d}" for d in (1, 2, 3, 4)]
    assert summary["total"]["requests"] == 6 and summary["weeks"] == ["2026-09-21", "2026-09-28"]
    assert summary["previous_week"][0]["requests"] == 2 and summary["next_week"] is None
    assert summary["four_week_average"][0]["requests"] == 2  # one earlier week with data: its own numbers
    assert [p["project"] for p in summary["projects"]] == ["little-workshop", "little-meals"]
    assert summary["pays_for_itself"] == [{"week": "2026-09-21", "workshop": 2.0, "others": 0.0}, {"week": "2026-09-28", "workshop": 5.0, "others": 0.5}]
    only = rollup.summary(conn, pricing.DEFAULT_PRICES, week="2026-09-28", project="little-meals", today=date(2026, 10, 12))
    assert only["total"]["requests"] == 1 and only["complete"] is True


def test_the_retention_is_never_under_a_full_month(paths):
    proxy.save_config(paths, {"retention_days": 3})
    assert proxy.load_config(paths)["retention_days"] == proxy.MIN_RETENTION_DAYS


def test_removing_a_project_purges_its_rows(client, conn):
    client.post("/v1/logs", json=otlp(api_request(), tool_result(), resource={"lws.project": "little-meals"}))
    client.tick()
    assert client.post("/v1/jobs/usage.purge_project", json={"job_id": "j", "project_id": "little-meals"}).json()["result"] == {"sessions_removed": 1}
    for table in ("sessions", "requests", "tool_calls", "day_rollups"):
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


# --- REQ-000000329: The user profile is shown first and applied only when asked ---


def test_the_profile_is_a_dry_run_unless_apply_is_asked(client, paths, tmp_path):
    settings = tmp_path / "home" / "settings.json"
    settings.parent.mkdir()
    own = {"model": "fable", "env": {"MY_OWN": "1"}, "hooks": {"SessionEnd": [{"hooks": [{"type": "command", "command": "echo mine"}]}]}}
    settings.write_text(json.dumps(own))
    port = proxy.intake_port(paths)

    dry = client.post("/v1/jobs/usage.configure_claude", json={"job_id": "j1"}).json()["result"]
    assert dry["applied"] is False and dry["in_place"] is False
    assert {"env.CLAUDE_CODE_ENABLE_TELEMETRY", "hooks.SessionEnd", "hooks.SessionStart", "statusLine", "cleanupPeriodDays"} <= {c["setting"] for c in dry["changes"]}
    assert json.loads(settings.read_text()) == own  # nothing written

    applied = client.post("/v1/jobs/usage.configure_claude", json={"job_id": "j2", "params": {"apply": True}}).json()["result"]
    assert applied["applied"] is True and Path(applied["backup"]).exists() and json.loads(Path(applied["backup"]).read_text()) == own
    after = json.loads(settings.read_text())
    assert after["model"] == "fable" and after["env"]["MY_OWN"] == "1"  # the owner's entries stay
    assert after["env"]["OTEL_EXPORTER_OTLP_ENDPOINT"] == f"http://127.0.0.1:{port}" and after["env"]["OTEL_EXPORTER_OTLP_PROTOCOL"] == "http/json"
    assert after["env"]["OTEL_LOG_TOOL_DETAILS"] == "1" and "OTEL_LOG_USER_PROMPTS" not in after["env"]
    assert after["hooks"]["SessionEnd"][0] == own["hooks"]["SessionEnd"][0]
    assert after["hooks"]["SessionEnd"][1] == {"hooks": [{"type": "http", "url": f"http://127.0.0.1:{port}/v1/hooks/SessionEnd"}]}
    assert after["hooks"]["SessionStart"][0]["hooks"][0]["type"] == "command"
    assert after["cleanupPeriodDays"] == 90 and "statusline.py" in after["statusLine"]["command"]
    script = (paths.pillar_config_dir("usage") / "statusline.py").read_text()
    assert f"127.0.0.1:{port}/v1/statusline" in script
    compile(script, "statusline.py", "exec")

    again = client.post("/v1/jobs/usage.configure_claude", json={"job_id": "j3", "params": {"apply": True}}).json()["result"]
    assert again["in_place"] is True and again["applied"] is False and again["backup"] is None


def test_the_profile_leaves_the_owners_statusline_and_longer_retention_alone():
    current = {"statusLine": {"type": "command", "command": "my-own-line"}, "cleanupPeriodDays": 365,
               "env": {"OTEL_LOG_USER_PROMPTS": "1"}}
    after, changes, notes = profile.merged(current, profile.desired(4318, Path("/cfg/statusline.py")))
    assert after["statusLine"] == current["statusLine"] and after["cleanupPeriodDays"] == 365
    assert not {"statusLine", "cleanupPeriodDays"} & {c["setting"] for c in changes}
    assert any("statusLine is your own" in note for note in notes) and any("OTEL_LOG_USER_PROMPTS" in note for note in notes)


def test_a_changed_port_replaces_the_profiles_own_hooks_only():
    first, _, _ = profile.merged({}, profile.desired(4318, Path("/cfg/statusline.py")))
    second, changes, _ = profile.merged(first, profile.desired(4400, Path("/cfg/statusline.py")))
    assert len(second["hooks"]["SessionEnd"]) == 1 and "4400" in second["hooks"]["SessionEnd"][0]["hooks"][0]["url"]
    assert "hooks.SessionEnd" in {c["setting"] for c in changes}


# --- REQ-000000415: The Claude profile installs the session-title hook and the status mod ---


def test_the_profile_names_sessions_on_start_and_on_each_prompt_beside_the_owners_hooks():
    own = {"hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command", "command": "echo mine"}]}]}}
    after, changes, _ = profile.merged(own, profile.desired(4318, Path("/cfg/statusline.py")))
    title = {"hooks": [{"type": "command", "command": "python3 /cfg/session_title.py", "timeout": profile.TITLE_HOOK_TIMEOUT}]}
    assert after["hooks"]["UserPromptSubmit"] == [own["hooks"]["UserPromptSubmit"][0], title]
    assert after["hooks"]["SessionStart"][-1] == title and len(after["hooks"]["SessionStart"]) == 2
    assert {"hooks.UserPromptSubmit", "hooks.SessionStart"} <= {c["setting"] for c in changes}
    again, changes, _ = profile.merged(after, profile.desired(4318, Path("/cfg/statusline.py")))
    assert again == after and not changes  # applied twice, the hooks are there once


def test_applying_the_profile_installs_the_title_hook_and_the_status_mod(tmp_path):
    settings, statusline = tmp_path / "home" / "settings.json", tmp_path / "cfg" / profile.STATUSLINE_FILE
    mod = profile.StatusMod(tmp_path / "state" / "sockets" / "dashboard.sock", "http://localhost:8790")
    dry = profile.plan(settings, 4318, statusline, status_mod=mod)
    folder = profile.mod_dir(settings)
    assert folder == tmp_path / "home" / "skills" / profile.MOD_NAME
    assert {str(statusline.parent / profile.SESSION_TITLE_FILE), str(folder / "hooks" / "register.tsx")} <= {c["setting"] for c in dry["changes"]}
    profile.apply(settings, 4318, statusline, status_mod=mod)
    assert (statusline.parent / profile.SESSION_TITLE_FILE).read_text() == (Path(session_title.__file__)).read_text()
    for target, text in profile.mod_files(mod).items():
        assert (folder / target).read_text() == text
    assert profile.plan(settings, 4318, statusline, status_mod=mod)["in_place"] is True
    (folder / "hooks" / "register.tsx").write_text("// an older mod")
    assert profile.plan(settings, 4318, statusline, status_mod=mod)["changes"] == [
        {"setting": str(folder / "hooks" / "register.tsx"), "from": "an older version", "to": f"the {profile.MOD_NAME} mod's hooks/register.tsx"}]


def test_the_configure_job_writes_the_mod_with_this_pillars_dashboard(client, paths, tmp_path):
    proxy.intake_port(paths)
    result = client.post("/v1/jobs/usage.configure_claude", json={"job_id": "j1", "params": {"apply": True}}).json()["result"]
    assert result["applied"] is True
    module = (tmp_path / "home" / "skills" / profile.MOD_NAME / "hooks" / "register.tsx").read_text()
    assert json.dumps(str(paths.socket_path("dashboard"))) in module and '"http://localhost:8790"' in module
    hooks = json.loads((tmp_path / "home" / "settings.json").read_text())["hooks"]
    assert any(profile.SESSION_TITLE_FILE in h["hooks"][0]["command"] for h in hooks["UserPromptSubmit"])


# --- REQ-000000330: usage.doctor reports what collection depends on ---


def test_the_doctor_reports_each_check_with_a_hint_and_keeps_its_report(client, conn, paths, tmp_path):
    proxy.intake_port(paths)
    result = client.post("/v1/jobs/usage.doctor", json={"job_id": "j1"}).json()["result"]
    checks = {check["check"]: check for check in result["checks"]}
    assert checks["intake"]["ok"] is True
    assert checks["user_profile"]["ok"] is False and "Configure Claude telemetry" in checks["user_profile"]["hint"]
    assert checks["data_received"]["ok"] is False and result["ok"] is False
    assert "language_server:pyright-langserver" in checks and "language_server:csharp-ls" in checks
    assert client.get("/v1/doctor").json()["report"]["ts"] == result["ts"]


def test_the_doctor_passes_when_everything_is_in_place(conn, paths, tmp_path):
    port = proxy.intake_port(paths)
    settings = tmp_path / "settings.json"
    profile.apply(settings, port, paths.pillar_config_dir("usage") / profile.STATUSLINE_FILE,
                  status_mod=profile.StatusMod(paths.socket_path("dashboard"), proxy.load_config(paths)["dashboard_url"]))
    db.upsert_session(conn, "s1", seen=NOW.isoformat())
    report = doctor.run(conn, paths, runner=FakeRunner(), settings_path=settings, which=lambda name: f"/usr/bin/{name}")
    assert report["ok"] is True and all(check["hint"] == "" for check in report["checks"])
    missing = doctor.run(conn, paths, runner=FakeRunner(active=False), settings_path=settings, which=lambda name: None)
    failed = {check["check"] for check in missing["checks"] if not check["ok"]}
    assert {"intake", "claude_doctor", "language_server:clangd"} <= failed


# --- REQ-000000331: Runs little-workshop starts report with their project, Job and run ---


def test_no_usage_pillar_means_no_launch_environment(paths):
    assert usage_client.launch_env(paths, project="little-meals", job_id="j1") == {}
    assert with_env(["claude", "-p"], {}) == ["claude", "-p"] and setenv_args(None) == []


def test_the_launch_environment_tags_the_run(paths, monkeypatch):
    class Response:
        def raise_for_status(self): pass
        def json(self): return {"launch_env": profile.telemetry_env(4318)}

    class Client:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, path): return Response()

    monkeypatch.setattr(usage_client, "resolve_pillar_socket", lambda paths, pillar, timeout=2.0: Path("/x/usage.sock"))
    monkeypatch.setattr(usage_client, "client_for_socket", lambda socket_path, timeout=2.0: Client())
    env = usage_client.launch_env(paths, project="little-meals", job_id="j1", job_type="agent.run_task", run_id="r1")
    assert env["CLAUDE_CODE_ENABLE_TELEMETRY"] == "1" and env["OTEL_EXPORTER_OTLP_ENDPOINT"] == "http://127.0.0.1:4318"
    assert env["OTEL_RESOURCE_ATTRIBUTES"] == "lws.project=little-meals,lws.job=j1,lws.job_type=agent.run_task,lws.run=r1"
    assert with_env(["claude"], {"A": "1"}) == ["env", "A=1", "claude"] and setenv_args({"A": "1"}) == ["--setenv=A=1"]


def test_a_task_run_starts_claude_with_that_environment(paths, tmp_path):
    calls = []

    class Runner:
        def run(self, argv, timeout=None, cwd=None):
            calls.append(argv)
            return CommandResult(0, json.dumps({"result": "ok"}), "")

    AgentFlow(paths=paths, runner=Runner())._invoke_claude(tmp_path, "do it", None, env={"OTEL_RESOURCE_ATTRIBUTES": "lws.project=p"})
    assert calls[0][:3] == ["env", "OTEL_RESOURCE_ATTRIBUTES=lws.project=p", "claude"]


def test_the_pillar_hands_out_the_launch_environment_once_it_has_a_port(client, paths):
    assert client.get("/v1/config").json()["launch_env"] == {}
    port = proxy.intake_port(paths)
    config = client.get("/v1/config").json()
    assert config["endpoint"] == f"http://127.0.0.1:{port}" and config["launch_env"]["OTEL_LOGS_EXPORTER"] == "otlp"


# --- REQ-000000332: The pillar is optional and its intake is reached through a loopback port ---


def test_the_pillar_is_registered_optional_and_enabled_by_default():
    assert "usage" in PILLARS and manifest.DEFAULT_ENABLED["usage"] is True
    batches = redeploy_plan.redeploy_batches(["usage", "orchestration", "deployment"])
    assert batches == [["orchestration"], ["usage"], ["deployment"]]


def test_the_first_free_port_is_chosen_once_and_kept(paths):
    taken = {4318, 4319}
    assert proxy.intake_port(paths, is_free=lambda port: port not in taken) == 4320
    assert proxy.intake_port(paths, is_free=lambda port: True) == 4320  # kept, not chosen again
    assert json.loads(proxy.config_path(paths).read_text())["intake_port"] == 4320


def test_the_proxy_listens_on_loopback_and_forwards_to_the_pillars_socket(paths, monkeypatch):
    monkeypatch.setattr(proxy, "proxyd_binary", lambda: "/usr/lib/systemd/systemd-socket-proxyd")
    runner = FakeRunner(active=False)
    started = proxy.ensure_proxy(paths, runner=runner)
    port = started["port"]
    argv = runner.calls[-1]
    assert started["ok"] is True and argv[:3] == ["systemd-run", "--user", "--collect"]
    assert f"--socket-property=ListenStream=127.0.0.1:{port}" in argv and argv[-1] == str(paths.socket_path("usage"))
    listening = FakeRunner(active=True)
    assert proxy.ensure_proxy(paths, runner=listening)["detail"] == "already listening"
    assert not any(call[0] == "systemd-run" for call in listening.calls)
    monkeypatch.setattr(proxy, "proxyd_binary", lambda: None)
    assert proxy.ensure_proxy(paths, runner=FakeRunner(active=False))["ok"] is False  # reported, never raised


# --- REQ-000000333: Run results stored by Agentic Development reach the usage store ---


def test_the_tick_pulls_run_usage_once(conn, paths):
    rows = [{"id": "u1", "kind": "run", "project_id": "little-meals", "job_id": "j1", "session_id": "s", "created_at": "2026-10-04T10:00:00+00:00", "output_tokens": 70}]
    asked = []
    app = build_app(conn, paths, runner=FakeRunner(), projects=lambda: [], agent_usage=lambda since: asked.append(since) or (rows if since is None else []))
    assert app.state.tick()["run_usage"] == 1
    assert app.state.tick()["run_usage"] == 0
    assert asked == [None, "2026-10-04T10:00:00+00:00"]
    stored = conn.execute("SELECT kind, project, data_json FROM run_usage").fetchone()
    assert (stored["kind"], stored["project"]) == ("run", "little-meals") and json.loads(stored["data_json"])["output_tokens"] == 70


def test_an_unreachable_agent_pillar_or_registry_changes_nothing(conn, paths):
    app = build_app(conn, paths, runner=FakeRunner(), projects=lambda: None, agent_usage=lambda since: None)
    assert app.state.tick()["run_usage"] == 0


# --- REQ-000000334: Usage is read through lws, the MCP server and the Dashboard ---


def test_signatures_drop_what_is_particular_to_one_call():
    assert signatures.signature("Bash", {"command": "cd /home/x/repo && python3 -m pytest tests/unit/test_a.py -q"}) == "Bash(python3 -m pytest <path>)"
    assert signatures.signature("Bash", {"command": "FOO=1 git status --short | head"}) == "Bash(git status --short)"
    assert signatures.signature("Bash", '{"command": "sleep 30"}') == "Bash(sleep <n>)"
    assert signatures.signature("mcp__little-workshop__workshop_deployment_redeploy_pillar", {"pillar": "agent", "commit": "abc1234"}) == "mcp:workshop_deployment_redeploy_pillar(commit, pillar)"
    assert signatures.signature("Read", {"file_path": "/a/b.py"}) == "Read(<path>)" and signatures.signature("Grep", {}) == "Grep()"
    assert signatures.detail("Read", {"file_path": "/a/b.py"}) == "/a/b.py"


def test_lws_usage_summary_prints_the_week_without_the_series(capsys, monkeypatch):
    monkeypatch.setattr(cli_main.client, "usage_summary", lambda p, **kw: {"week": "2026-09-28", "days": [1], "total": {"requests": 3}, "asked": kw})
    assert cli_main.main(["usage", "summary", "--project-id", "little-meals"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["total"] == {"requests": 3} and "days" not in out and out["asked"]["project"] == "little-meals"

    def down(p, **kw):
        raise cli_main.client.UsagePillarUnavailable("down")

    monkeypatch.setattr(cli_main.client, "usage_summary", down)
    assert cli_main.main(["usage", "summary"]) == 1


def test_lws_usage_configure_claude_is_a_dry_run_without_apply(monkeypatch, capsys):
    jobs = []
    monkeypatch.setattr(cli_main.client, "create_job", lambda p, **kw: jobs.append(kw) or {"status": "succeeded"})
    assert cli_main.main(["usage", "configure-claude"]) == 0 and cli_main.main(["usage", "configure-claude", "--apply"]) == 0
    assert cli_main.main(["usage", "doctor"]) == 0 and cli_main.main(["usage", "ingest-transcripts"]) == 0
    assert [(j["type"], j["params"]) for j in jobs] == [("usage.configure_claude", {"apply": False}), ("usage.configure_claude", {"apply": True}),
                                                        ("usage.doctor", {}), ("usage.ingest_transcripts", {})]
    assert all(j["target_pillar"] == "usage" for j in jobs)


def test_the_summary_is_readable_everywhere_and_applying_the_profile_always_asks():
    project = claude_templates.settings("little-widget")["permissions"]
    assert f"{claude_templates.MCP}workshop_usage_summary" in project["allow"]
    assert f"{claude_templates.MCP}workshop_usage_configure_claude" in project["ask"]
    platform = claude_templates.settings("little-workshop", platform=True)["permissions"]
    assert f"{claude_templates.MCP}workshop_usage_configure_claude" in platform["ask"]
    assert f"{claude_templates.MCP}workshop_usage_doctor" in platform["allow"]


def test_the_summary_endpoint_answers_for_a_week_a_project_and_an_origin(client):
    client.post("/v1/logs", json=otlp(api_request(), resource={"lws.project": "little-meals", "lws.job_type": "agent.run_task"}))
    client.tick()
    summary = client.get("/v1/summary").json()
    assert summary["total"]["requests"] == 1 and summary["projects"][0]["project"] == "little-meals"
    assert summary["top_sessions"][0]["session_id"] == SESSION
    assert client.get("/v1/summary", params={"project": "little-home"}).json()["total"]["requests"] == 0
    assert client.get("/v1/summary", params={"origin": "job"}).json()["total"]["requests"] == 1
    today = db.local_day(NOW.isoformat())
    assert client.get("/v1/days", params={"start": today, "end": today}).json()[0]["requests"] == 1
