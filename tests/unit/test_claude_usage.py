"""M18, the quick wins of docs/claude_usage_optimization.md: the usage every Claude run reports is
kept, `agent.query` runs where its cache is reused, and a Job is waited for in one call."""

import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

from agent import db as agent_db
from agent.app import build_app as build_agent_app
from agent.claude_invoke import claude_argv, usage_from_raw
from agent.doc_update import SESSION_LOG, DocUpdateFlow
from agent.flow import AgentFlow, AgentFlowError
from agent.query import query_dir, run_query
from cli import main as cli_main
from common.paths import Paths
from common.shell import CommandResult
from orchestration import app as orchestration_app
from orchestration import db as orchestration_db
from orchestration.models import Job, JobStatus
from registry import claude_templates

RESULT = {
    "type": "result", "result": "done", "is_error": False, "session_id": "s-1", "num_turns": 3, "duration_ms": 4200,
    "total_cost_usd": 0.42,
    "usage": {"input_tokens": 10, "cache_read_input_tokens": 9000, "cache_creation_input_tokens": 500, "output_tokens": 70},
    "modelUsage": {"claude-haiku-4-5": {"outputTokens": 5}, "claude-sonnet-5-5": {"outputTokens": 65}},
}


class FakeClaude:
    def __init__(self, payload=RESULT, returncode=0):
        self.payload, self.returncode, self.calls = payload, returncode, []

    def run(self, argv, timeout=None, cwd=None):
        self.calls.append({"argv": argv, "cwd": cwd})
        return CommandResult(self.returncode, json.dumps(self.payload), "")


@pytest.fixture
def paths(tmp_path):
    p = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    p.ensure_layout()
    return p


@pytest.fixture
def conn(tmp_path):
    connection = agent_db.connect(tmp_path / "agent.db")
    yield connection
    connection.close()


# --- REQ-000000320: Claude runs record the usage they report ---


def test_usage_is_read_from_a_result_json():
    usage = usage_from_raw(RESULT)
    assert (usage["input_tokens"], usage["cache_read_tokens"], usage["cache_write_tokens"], usage["output_tokens"]) == (10, 9000, 500, 70)
    assert usage["cost_usd"] == 0.42 and usage["num_turns"] == 3 and usage["duration_ms"] == 4200
    assert usage["session_id"] == "s-1"
    assert usage["model"] == "claude-sonnet-5-5"  # the model that wrote most of the output


def test_a_result_without_usage_gives_none_and_odd_values_count_as_zero():
    assert usage_from_raw({}) is None
    assert usage_from_raw({"result": "text only"}) is None
    usage = usage_from_raw({"usage": {"input_tokens": "many", "output_tokens": None}, "total_cost_usd": "free"})
    assert usage["input_tokens"] == 0 and usage["output_tokens"] == 0 and usage["cost_usd"] == 0.0 and usage["model"] is None


def test_stored_usage_is_listed_filtered_and_summed(conn):
    usage = usage_from_raw(RESULT)
    agent_db.record_usage(conn, agent_db.USAGE_RUN, usage, ref_id="run-1", project_id="little-meals", job_id="j1")
    agent_db.record_usage(conn, agent_db.USAGE_RUN, usage, ref_id="run-1", project_id="little-meals", job_id="j1")
    agent_db.record_usage(conn, agent_db.USAGE_QUERY, usage, ref_id="j2")
    assert agent_db.record_usage(conn, agent_db.USAGE_QUERY, None, ref_id="j3") is None  # nothing reported, nothing stored

    assert len(agent_db.list_usage(conn)) == 3
    assert [row["kind"] for row in agent_db.list_usage(conn, project_id="little-meals")] == ["run", "run"]
    assert len(agent_db.list_usage(conn, kind=agent_db.USAGE_QUERY)) == 1
    last = agent_db.list_usage(conn)[-1]
    assert agent_db.list_usage(conn, since=last["created_at"]) == []
    assert last["model_usage"] == RESULT["modelUsage"] and last["is_error"] is False

    total = agent_db.usage_total(conn, agent_db.USAGE_RUN, "run-1")
    assert total["cache_read_tokens"] == 18000 and total["num_turns"] == 6 and total["invocations"] == 2
    assert agent_db.usage_total(conn, agent_db.USAGE_RUN, "no-such-run") is None


def test_a_task_runs_claude_call_hands_over_its_usage_even_when_it_fails(paths, tmp_path):
    seen = []
    flow = AgentFlow(paths=paths, runner=FakeClaude())
    text, _ = flow._invoke_claude(tmp_path, "do it", None, on_usage=seen.append)
    assert text == "done" and seen[0]["output_tokens"] == 70

    failing = AgentFlow(paths=paths, runner=FakeClaude({**RESULT, "is_error": True}))
    with pytest.raises(AgentFlowError):
        failing._invoke_claude(tmp_path, "do it", None, on_usage=seen.append)
    assert len(seen) == 2 and seen[1]["is_error"] is True


def test_a_query_stores_its_usage_and_the_pillar_returns_it(paths, conn):
    client = TestClient(build_agent_app(paths, conn, flow=AgentFlow(paths=paths, runner=FakeClaude())))
    response = client.post("/v1/jobs/agent.query", json={"job_id": "j1", "params": {"prompt": "refine this"}})
    assert response.json()["outcome"] == "succeeded"

    rows = client.get("/v1/usage").json()
    assert len(rows) == 1
    assert rows[0]["kind"] == "query" and rows[0]["job_id"] == "j1" and rows[0]["cache_read_tokens"] == 9000
    assert client.get("/v1/usage", params={"kind": "run"}).json() == []


def test_a_runs_status_carries_its_usage(paths, conn):
    agent_db.create_run(conn, "run-1", job_id="j1", project_id="little-meals", intent="task", review_mode="manual", title="t", source={})
    client = TestClient(build_agent_app(paths, conn, flow=AgentFlow(paths=paths, runner=FakeClaude())))
    assert client.get("/v1/runs/little-meals/run-1").json()["usage"] is None

    agent_db.record_usage(conn, agent_db.USAGE_RUN, usage_from_raw(RESULT), ref_id="run-1", project_id="little-meals", job_id="j1")
    usage = client.get("/v1/runs/little-meals/run-1").json()["usage"]
    assert usage["output_tokens"] == 70 and usage["cost_usd"] == 0.42


def test_a_headless_doc_update_stores_the_usage_its_session_log_ends_with(paths, conn, tmp_path):
    worktree = tmp_path / "worktree"
    (worktree / ".review").mkdir(parents=True)
    (worktree / SESSION_LOG).write_text("some warning on stderr\n" + json.dumps(RESULT), encoding="utf-8")
    flow = DocUpdateFlow(paths=paths)
    flow._record_usage(conn, {"job_id": "j9", "project_id": "little-home"}, worktree)
    flow._record_usage(conn, {"job_id": "j10", "project_id": "little-home"}, tmp_path / "no-log-here")  # Remote Control: no log

    rows = agent_db.list_usage(conn)
    assert [(r["kind"], r["ref_id"], r["project_id"], r["output_tokens"]) for r in rows] == [("doc_update", "j9", "little-home", 70)]


# --- REQ-000000321: agent.query runs where its prompt cache can be reused ---


def test_every_query_runs_in_the_same_directory_with_a_five_minute_cache(paths):
    fake = FakeClaude()
    run_query("first", cwd=query_dir(paths), runner=fake)
    run_query("second", cwd=query_dir(paths), runner=fake)
    first, second = fake.calls
    assert first["cwd"] == second["cwd"] == paths.state_home / "agent-query"
    assert first["cwd"].is_dir()
    assert first["argv"][:3] == ["env", "CLAUDE_CODE_PROMPT_CACHE_TTL=5m", "claude"]
    assert "--strict-mcp-config" in first["argv"] and "--disable-slash-commands" in first["argv"]


def test_only_a_minimal_launch_drops_mcp_servers_and_skills():
    assert "--strict-mcp-config" not in claude_argv("x")
    assert claude_argv("x", minimal=True)[-2:] == ["--strict-mcp-config", "--disable-slash-commands"]


def test_the_query_endpoint_uses_that_directory(paths, conn):
    fake = FakeClaude()
    client = TestClient(build_agent_app(paths, conn, flow=AgentFlow(paths=paths, runner=fake)))
    client.post("/v1/jobs/agent.query", json={"job_id": "j1", "params": {"prompt": "a"}})
    assert fake.calls[0]["cwd"] == paths.state_home / "agent-query"


# --- REQ-000000322: A caller waits for a Job in one call ---


@pytest.fixture
def orch(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestration_app, "WAIT_POLL_SECONDS", 0.01)
    connection = orchestration_db.connect(tmp_path / "orchestration.db")
    yield connection, TestClient(orchestration_app.build_app(connection, socket_lookup=lambda pillar: None))
    connection.close()


def _job(connection, status):
    job = Job(type="noop", target_pillar="reference", status=status)
    orchestration_db.insert_job(connection, job)
    return job


def test_waiting_for_a_settled_or_held_job_answers_at_once(orch):
    connection, client = orch
    for status in (JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.PENDING_CONFIRMATION):
        job = _job(connection, status)
        started = time.monotonic()
        body = client.get(f"/v1/jobs/{job.id}/wait", params={"timeout": 30}).json()
        assert body["status"] == status.value and "steps" in body
        assert time.monotonic() - started < 5


def test_waiting_returns_when_the_job_settles(orch):
    connection, client = orch
    job = _job(connection, JobStatus.RUNNING)

    def settle():
        time.sleep(0.2)
        job.status = JobStatus.SUCCEEDED
        orchestration_db.update_job(connection, job)

    threading.Thread(target=settle).start()
    started = time.monotonic()
    body = client.get(f"/v1/jobs/{job.id}/wait", params={"timeout": 30}).json()
    assert body["status"] == "succeeded"
    assert time.monotonic() - started < 10


def test_waiting_gives_the_running_job_back_at_the_timeout_and_never_waits_past_the_cap(orch, monkeypatch):
    connection, client = orch
    job = _job(connection, JobStatus.RUNNING)
    assert client.get(f"/v1/jobs/{job.id}/wait", params={"timeout": 0.05}).json()["status"] == "running"

    monkeypatch.setattr(orchestration_app, "WAIT_MAX_SECONDS", 0.05)
    started = time.monotonic()
    assert client.get(f"/v1/jobs/{job.id}/wait", params={"timeout": 9999}).json()["status"] == "running"
    assert time.monotonic() - started < 5
    assert orchestration_app.WAIT_DEFAULT_SECONDS == 120 and orchestration_app.WAIT_MAX_SECONDS == 0.05


def test_waiting_for_an_unknown_job_is_a_404(orch):
    _, client = orch
    assert client.get("/v1/jobs/nope/wait").status_code == 404


def test_lws_job_wait_prints_the_job(capsys, monkeypatch):
    monkeypatch.setattr(cli_main.client, "job_wait", lambda p, job_id, timeout: {"source": "daemon", "job": {"id": job_id, "timeout": timeout}})
    assert cli_main.main(["job", "wait", "j1", "--timeout", "30"]) == 0
    assert json.loads(capsys.readouterr().out)["job"] == {"id": "j1", "timeout": 30.0}

    monkeypatch.setattr(cli_main.client, "job_wait", lambda p, job_id, timeout: {"source": "daemon", "job": None})
    assert cli_main.main(["job", "wait", "nope"]) == 1


# --- REQ-000000323: Generated Claude files route waiting and multi-pillar redeploys ---


def test_the_wait_is_pre_allowed_wherever_the_status_read_is():
    allow = claude_templates.settings("little-widget")["permissions"]["allow"]
    assert f"{claude_templates.MCP}workshop_job_status" in allow
    assert f"{claude_templates.MCP}workshop_job_wait" in allow
    assert "job wait" in claude_templates.LWS_READ_ONLY_ANY


def test_the_lifecycle_skill_says_to_wait_and_only_little_workshop_gets_the_redeploy_route():
    project = claude_templates.lifecycle_skill_md("little-widget")
    assert "`workshop_job_wait`" in project and "Never repeat `workshop_job_status` or `sleep`" in project
    assert "redeploy_pillars" not in project

    platform = claude_templates.lifecycle_skill_md("little-workshop", platform=True)
    assert "`workshop_deployment_redeploy_pillars`: one call for several pillars" in platform
    files = claude_templates.claude_files("little-workshop", platform=True)
    assert "workshop_deployment_redeploy_pillars" in files[claude_templates.LIFECYCLE_SKILL_PATH]
