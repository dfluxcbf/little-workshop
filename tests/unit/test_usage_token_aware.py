"""M22: token-aware operation (docs/pillars/usage.md "Outcomes", docs/pillars/orchestration.md
"Plan pressure", docs/pillars/agentic_development.md "Launch defaults per job type")."""

import json
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from agent import db as agent_db, launch
from agent.claude_invoke import claude_argv, usage_from_raw, with_budget
from agent.flow import AgentFlow
from agent.query import query_dir, run_query
from common.paths import Paths
from common.shell import CommandResult
from orchestration import db as orchestration_db, plan_pressure
from orchestration.dispatch import dispatch_ready_jobs
from orchestration.models import Job, JobStatus
from usage import db, outcomes
from usage.app import build_app

RESULT = {"result": "ok", "total_cost_usd": 0.25, "usage": {"input_tokens": 1, "output_tokens": 2}}


class FakeClaude:
    def __init__(self):
        self.calls = []

    def run(self, argv, timeout=None, cwd=None):
        self.calls.append(argv)
        return CommandResult(0, json.dumps(RESULT), "")


@pytest.fixture
def paths(tmp_path):
    p = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    p.ensure_layout()
    return p


def _option(argv, flag):
    return argv[argv.index(flag) + 1] if flag in argv else None


# --- REQ-000000348: Each kind of Claude run has its own model, effort and budget ---


def test_each_job_type_has_launch_defaults_the_owner_can_override(paths):
    assert launch.launch_for("agent.run_task", paths) == launch.Launch("claude-sonnet-5-5", "medium", None)
    assert launch.launch_for("agent.query", paths).max_budget_usd == 1.0
    assert launch.launch_for("something.else", paths) == launch.Launch()
    (paths.pillar_config_dir("agent") / launch.LAUNCH_FILE).write_text(json.dumps({
        "agent.query": {"model": "claude-haiku-4-5-20251001", "effort": "low", "max_budget_usd": 0.25},
        "agent.run_task": {"max_budget_usd": 20}, "agent.session": "not an object"}))
    assert launch.launch_for("agent.query", paths) == launch.Launch("claude-haiku-4-5-20251001", "low", 0.25)
    assert launch.launch_for("agent.run_task", paths) == launch.Launch("claude-sonnet-5-5", "medium", 20.0)
    assert launch.launch_for("agent.session", paths) == launch.Launch()
    (paths.pillar_config_dir("agent") / launch.LAUNCH_FILE).write_text("{broken")
    assert launch.launch_for("agent.query", paths) == launch.DEFAULTS["agent.query"]


def test_a_task_run_and_a_query_start_claude_with_their_job_types_settings(paths, tmp_path):
    (paths.pillar_config_dir("agent") / launch.LAUNCH_FILE).write_text(json.dumps({
        "agent.run_task": {"model": "claude-opus-5-5", "effort": "high", "max_budget_usd": 12.5}}))
    fake, seen = FakeClaude(), []
    AgentFlow(paths=paths, runner=fake)._invoke_claude(tmp_path, "do it", None, on_usage=seen.append)
    task = fake.calls[0]
    assert (_option(task, "--model"), _option(task, "--effort"), _option(task, "--max-budget-usd")) == ("claude-opus-5-5", "high", "12.5")
    assert seen[0]["budget_usd"] == 12.5

    AgentFlow(paths=paths, runner=fake, model="claude-fable-5-1", max_budget_usd=3.0)._invoke_claude(tmp_path, "do it", None)
    assert (_option(fake.calls[1], "--model"), _option(fake.calls[1], "--max-budget-usd")) == ("claude-fable-5-1", "3.0")  # the flow's own settings win

    run_query("refine", cwd=query_dir(paths), runner=fake, on_usage=seen.append)
    query = fake.calls[2]
    assert (_option(query, "--model"), _option(query, "--effort"), _option(query, "--max-budget-usd")) == ("claude-sonnet-5-5", "medium", "1.0")
    assert seen[-1]["budget_usd"] == 1.0
    assert _option(claude_argv("x", effort="low"), "--effort") == "low"


def test_a_runs_usage_says_how_much_of_its_budget_it_used(tmp_path):
    conn = agent_db.connect(tmp_path / "agent.db")
    usage = with_budget(usage_from_raw(RESULT), 2.0)
    agent_db.record_usage(conn, agent_db.USAGE_RUN, usage, ref_id="run-1", project_id="p")
    total = agent_db.usage_total(conn, agent_db.USAGE_RUN, "run-1")
    assert total["budget_usd"] == 2.0 and total["budget_used"] == pytest.approx(0.125)
    agent_db.record_usage(conn, agent_db.USAGE_RUN, with_budget(usage_from_raw(RESULT), None), ref_id="run-2", project_id="p")
    unbounded = agent_db.usage_total(conn, agent_db.USAGE_RUN, "run-2")
    assert unbounded["budget_usd"] is None and unbounded["budget_used"] is None and with_budget(None, 1.0) is None
    conn.close()


# --- REQ-000000349: Unattended Claude jobs wait while the plan's 5-hour window is nearly full ---


@pytest.fixture
def orch(tmp_path, monkeypatch):
    plan_pressure.reset()
    conn = orchestration_db.connect(tmp_path / "orchestration.db")
    reading = {"hold": True}
    monkeypatch.setattr(plan_pressure, "fetch", lambda socket_lookup: reading["hold"] if reading["hold"] is None else {"hold": reading["hold"]})
    yield conn, reading
    conn.close()
    plan_pressure.reset()


def _queued(conn, job_type, created_by):
    job = Job(type=job_type, target_pillar="reference", created_by=created_by)
    orchestration_db.insert_job(conn, job)
    return job


def test_only_unattended_jobs_that_use_claude_are_ever_held():
    held = Job(type="agent.run_task", target_pillar="agent", created_by="scheduler")
    assert plan_pressure.applies(held) and plan_pressure.applies(held.model_copy(update={"created_by": "trigger:abc"}))
    assert not plan_pressure.applies(held.model_copy(update={"created_by": "dashboard"}))  # a person started it
    assert not plan_pressure.applies(Job(type="policy.check_project", target_pillar="policy", created_by="scheduler"))  # code only
    assert plan_pressure.applies(Job(type="usage.optimize", target_pillar="usage", created_by="scheduler"))


@pytest.mark.parametrize("with_lock", [False, True])
def test_a_scheduled_agent_run_waits_under_pressure_while_a_policy_check_and_a_persons_task_run(orch, with_lock):
    import threading
    conn, reading = orch
    scheduled = _queued(conn, "agent.run_task", "scheduler")
    check = _queued(conn, "policy.check_project", "scheduler")
    mine = _queued(conn, "agent.run_task", "dashboard")
    dispatch_ready_jobs(conn, lambda pillar: None, lock=threading.Lock() if with_lock else None)
    status = lambda job: orchestration_db.get_job(conn, job.id).status
    assert status(scheduled) == JobStatus.QUEUED  # held: still waiting in its queue
    assert status(check) == JobStatus.FAILED and status(mine) == JobStatus.FAILED  # dispatched (no pillar in this test)

    reading["hold"] = False
    plan_pressure.reset()
    dispatch_ready_jobs(conn, lambda pillar: None, lock=threading.Lock() if with_lock else None)
    assert status(scheduled) == JobStatus.FAILED  # the window has room again: it was dispatched


def test_an_unreachable_usage_pillar_never_holds_anything_and_the_reading_is_cached(orch):
    conn, reading = orch
    reading["hold"] = None  # the pillar is off or unreachable
    assert plan_pressure.holding(lambda pillar: None, now=1000.0) is False
    reading["hold"] = True
    assert plan_pressure.holding(lambda pillar: None, now=1030.0) is False  # within the minute: not asked again
    assert plan_pressure.holding(lambda pillar: None, now=1061.0) is True


def test_the_usage_pillar_says_hold_from_the_owners_threshold(tmp_path, paths):
    from usage import proxy
    conn = db.connect(tmp_path / "usage.db")
    client = TestClient(build_app(conn, paths, projects=lambda: [], agent_usage=lambda since: [], emit=lambda *a: None, outcome_sources=lambda names: {}))
    db.add_plan_pressure(conn, datetime.now(timezone.utc).isoformat(), "s1", 85.0, 20.0)
    conn.commit()
    assert client.get("/v1/plan_pressure").json()["hold"] is True
    proxy.save_config(paths, {"plan_pressure_hold_percent": 90})
    answer = client.get("/v1/plan_pressure").json()
    assert answer["hold"] is False and answer["threshold_percent"] == 90
    conn.close()


# --- REQ-000000350: Tokens are reported per finished task, finished branch and requirement done ---


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "usage.db")
    yield connection
    connection.close()


def test_a_weeks_tokens_stand_beside_what_the_week_produced(conn):
    conn.execute("INSERT INTO day_rollups (day, project, origin, model, requests, weighted_output) VALUES ('2026-09-29', 'little-meals', 'job', 'm', 10, 12.0)")
    conn.execute("INSERT INTO day_rollups (day, project, origin, model, requests, weighted_output) VALUES ('2026-09-22', 'little-meals', 'job', 'm', 5, 4.0)")
    for i, (cost, error) in enumerate([(2.0, False), (4.0, False), (1.0, True)]):
        db.add_run_usage(conn, {"id": f"u{i}", "kind": "run", "project_id": "little-meals", "created_at": "2026-09-30T10:00:00+00:00", "cost_usd": cost, "is_error": error})
    db.add_run_usage(conn, {"id": "q", "kind": "query", "project_id": "little-meals", "created_at": "2026-09-30T10:00:00+00:00", "cost_usd": 9.0})
    jobs = [{"id": "j1", "type": "version.improvement_finish", "status": "succeeded", "project_id": "little-meals", "updated_at": "2026-09-30T12:00:00+00:00"},
            {"id": "j2", "type": "version.feature_finish", "status": "succeeded", "project_id": "little-meals", "updated_at": "2026-10-01T12:00:00+00:00"},
            {"id": "j3", "type": "version.feature_start", "status": "succeeded", "project_id": "little-meals", "updated_at": "2026-10-01T12:00:00+00:00"},
            {"id": "j4", "type": "version.feature_finish", "status": "failed", "project_id": "little-meals", "updated_at": "2026-10-01T12:00:00+00:00"}]
    assert outcomes.note_finished_branches(conn, jobs) == 2 and outcomes.note_finished_branches(conn, jobs) == 0  # each finish once
    outcomes.note_requirements(conn, "little-meals", {"done_count": 40, "total_count": 50}, "2026-09-27")
    outcomes.note_requirements(conn, "little-meals", {"done_count": 43, "total_count": 50}, "2026-10-02")
    outcomes.note_requirements(conn, "little-meals", None, "2026-10-03")  # the pillar was unreachable: nothing noted

    rows = outcomes.weekly(conn, weeks=2, today=date(2026, 10, 4))
    this_week, last_week = rows[0], rows[1]
    assert (this_week["week"], this_week["project"], this_week["weighted"]) == ("2026-09-28", "little-meals", 12.0)
    assert this_week["tasks_finished"] == 2 and this_week["task_cost"] == 7.0 and this_week["weighted_per_task"] == 3.5  # a failed run costs, and finishes nothing
    assert this_week["branches_finished"] == 2 and this_week["weighted_per_branch"] == 6.0
    assert this_week["requirements_done"] == 3 and this_week["weighted_per_requirement"] == 4.0
    assert last_week["week"] == "2026-09-21" and last_week["branches_finished"] == 0 and last_week["weighted_per_branch"] is None
    assert last_week["requirements_done"] is None  # no reading before that week
    assert outcomes.weekly(conn, weeks=2, project="little-home", today=date(2026, 10, 4)) == []


def test_the_tick_notes_outcomes_from_the_other_pillars_and_the_summary_carries_them(conn, paths):
    today = date.today()
    sources = {"jobs": [{"id": "j1", "type": "version.feature_finish", "status": "succeeded", "project_id": "little-meals",
                         "updated_at": datetime.now(timezone.utc).isoformat()}],
               "reports": {"little-meals": {"done_count": 7, "total_count": 9}}}
    asked = []
    app = build_app(conn, paths, projects=lambda: [{"name": "little-meals", "path": "/x"}], agent_usage=lambda since: [],
                    emit=lambda *a: None, outcome_sources=lambda names: asked.append(names) or sources)
    assert app.state.tick()["branches_noted"] == 1 and asked == [["little-meals"]]
    assert conn.execute("SELECT done FROM requirement_snapshots WHERE project = 'little-meals'").fetchone()[0] == 7
    client = TestClient(app)
    (row,) = client.get("/v1/outcomes", params={"weeks": 1}).json()
    assert row["project"] == "little-meals" and row["branches_finished"] == 1
    summary = client.get("/v1/summary").json()
    assert summary["outcomes"][0]["branches_finished"] == 1 and len(summary["outcomes_by_week"]) >= 1
    assert client.get("/v1/outcomes", params={"project": "little-home"}).json() == []


def test_unreachable_pillars_add_no_outcome(conn, paths):
    app = build_app(conn, paths, projects=lambda: [{"name": "p", "path": "/x"}], agent_usage=lambda since: [], emit=lambda *a: None,
                    outcome_sources=lambda names: {"jobs": None, "reports": {"p": None}})
    assert app.state.tick()["branches_noted"] == 0
    assert conn.execute("SELECT COUNT(*) FROM requirement_snapshots").fetchone()[0] == 0
