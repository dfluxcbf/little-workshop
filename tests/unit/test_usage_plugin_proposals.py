"""Plugin proposals (docs/pillars/usage.md "Plugin proposals"): whole requests Claude handles the same way,
mostly without judgment, proposed as a Claude Code plugin of hooks and scripts."""

from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from cli import main as cli_main
from common.paths import Paths
from registry import claude_templates
from usage import db, mining, plugins, pricing, signatures
from usage.app import build_app as build_usage_app

NOW = datetime.now(timezone.utc).replace(microsecond=0)
TODAY = date.fromisoformat(db.local_day(NOW.isoformat()))
PRICES = pricing.DEFAULT_PRICES
WS = "mcp__little-workshop__"

EDIT = ("Edit", {"file_path": "src/usage/a.py"})
READ = ("Read", {"file_path": "src/usage/a.py"})
PYTEST = ("Bash", {"command": "python3 -m pytest tests/unit -q"})
RUN_TESTS = (WS + "workshop_requirements_run_tests", {"project_id": "p", "test_command": ["python3", "-m", "pytest", "tests/unit"]})
SYNC = (WS + "workshop_requirements_sync_markers", {"project_id": "p"})
WAIT = (WS + "workshop_job_wait", {"job_id": "j"})
FINISH = {kind: (WS + f"workshop_version_{kind}_finish", {"project_id": "p"}) for kind in ("feature", "improvement", "hotfix")}
TOOL_SEARCH = ("ToolSearch", {"query": "select:x"})


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


def episode(conn, session_id, prompt, calls, *, project="little-workshop", ago=timedelta(hours=1)):
    """One prompt's tool calls, in order: (tool, input) or (tool, input, success), each in a turn of its own, then
    a few more turns in which the conversation goes on."""
    db.upsert_session(conn, session_id, seen=(NOW - ago).isoformat(), tag_project=project)
    db.set_attribution(conn, session_id, project, "interactive")

    def turn(request_id, offset):
        db.upsert_request(conn, {"request_id": request_id, "session_id": session_id, "ts": (NOW - ago + timedelta(seconds=offset)).isoformat(),
                                 "model": "claude-fable-5-1", "input_tokens": 10, "cache_read_tokens": 400_000 + 1000 * offset,
                                 "output_tokens": 50, "source": "test"})
    for index in range(len(calls)):
        turn(f"r-{session_id}-{prompt}-{index}", index)
    for later in range(3):
        turn(f"r-{session_id}-{prompt}-after-{later}", len(calls) + later)
    for index, (tool, tool_input, *success) in enumerate(calls):
        db.upsert_tool_call(conn, {
            "id": f"c-{session_id}-{prompt}-{index}", "session_id": session_id, "prompt_id": prompt,
            "request_id": f"r-{session_id}-{prompt}-{index}", "ts": (NOW - ago + timedelta(seconds=index)).isoformat(), "tool": tool,
            "signature": signatures.signature(tool, tool_input), "detail": signatures.detail(tool, tool_input),
            "success": success[0] if success else True, "source": "test"})
    conn.commit()


def finishes(conn, kinds, *, projects=("little-workshop",), judged=()):
    """One finish request per kind, each in a session of its own after some coding; `judged` holds the indexes of
    the ones whose tests failed and Claude fixed the code before finishing."""
    for i, kind in enumerate(kinds):
        calls = [EDIT, EDIT, TOOL_SEARCH]
        calls += [(*PYTEST, False), READ, EDIT, PYTEST] if i in judged else [PYTEST]
        calls += [SYNC, RUN_TESTS, WAIT, FINISH[kind], WAIT]
        episode(conn, f"s{i}", "p1", calls, project=projects[i % len(projects)], ago=timedelta(days=i % 5, hours=1))


def propose(conn):
    return plugins.propose(conn, PRICES, today=TODAY)


# --- REQ-000000416: Repeated requests that are mostly deterministic are proposed as Claude Code plugins ---


def test_the_variants_of_an_action_are_one_family():
    assert plugins.family("mcp:workshop_version_hotfix_finish(project_id)") == "version_finish"
    assert plugins.family("mcp:workshop_version_feature_finish(project_id)") == "version_finish"
    assert plugins.family("mcp:workshop_version_release_finish(project_id)") == "version_release_finish"
    assert plugins.family("mcp:workshop_deployment_redeploy_changed_pillars(project_id)") == "deployment_redeploy_pillar"
    assert plugins.family("Bash(python3 -m pytest <path>)") == "python3 -m pytest"
    assert plugins.family("Bash(git push origin)") == "git push"
    assert plugins.plugin_name("version_finish") == "finish-branch"


def test_a_request_starts_at_the_first_step_code_could_make():
    calls = [{"signature": s} for s in ("Edit(<path>)", "Read(<path>)", "Bash(git status)", "mcp:workshop_version_improvement_finish(project_id)",
                                        "mcp:workshop_job_wait(job_id)", "Read(<path>)")]
    request = plugins.request_of(calls)
    assert request is not None and [c["signature"] for c in request] == ["mcp:workshop_version_improvement_finish(project_id)", "mcp:workshop_job_wait(job_id)"]
    assert plugins.request_of([{"signature": "Read(<path>)"}, {"signature": "Bash(git status)"}]) is None  # it changed nothing


def test_finishes_of_every_branch_kind_are_one_plugin_covering_every_step(conn):
    finishes(conn, ["feature", "improvement", "hotfix", "improvement"])
    result = propose(conn)
    [proposal] = result["proposals"]
    assert proposal["name"] == "finish-branch" and proposal["family"] == "version_finish" and proposal["occurrences"] == 4
    assert [step["step"] for step in proposal["steps"]] == [
        "python3 -m pytest", "requirements_sync_markers", "requirements_run_tests", "job_wait", "version_finish"]
    finish = next(step for step in proposal["steps"] if step["step"] == "version_finish")
    assert len(finish["variants"]) == 3  # feature, improvement and hotfix: one plugin finishes any branch
    assert proposal["usage"]["fallback_rate"] == 0 and proposal["fallbacks"] == []
    assert all("Edit" not in call for example in proposal["examples"] for call in example["calls"])  # the coding before is not the request


def test_a_request_that_needs_claude_too_often_is_not_proposed(conn):
    finishes(conn, ["improvement"] * 4, judged={0, 1})
    result = propose(conn)
    assert result["proposals"] == []
    assert result["too_much_judgment"] == [{"family": "version_finish", "occurrences": 4, "fallback_rate": 0.5}]


# --- REQ-000000417: A plugin proposal states exactly how much Claude it uses, today and with the plugin ---


def test_a_proposal_names_each_fallback_point_and_what_claude_still_costs(conn):
    finishes(conn, ["improvement"] * 8, judged={0, 1})
    [proposal] = propose(conn)["proposals"]
    usage = proposal["usage"]
    assert usage["fallback_rate"] == 0.25
    [point] = proposal["fallbacks"]
    assert point["after"] == "python3 -m pytest" and point["occurrences"] == 2 and point["rate"] == 0.25
    assert point["after_failed"] == 2 and point["tools"] == {"Read": 2, "Edit": 2} and point["calls_median"] == 2
    assert 0 < usage["with_plugin_weekly"] < usage["today_weekly"]
    assert proposal["weekly_value"] == pytest.approx(usage["today_weekly"] - usage["with_plugin_weekly"])
    assert usage["calls_per_occurrence"] == 6 and usage["tokens_per_occurrence"] > 0 and usage["turns_per_occurrence"] >= 6
    assert usage["frequency"] in ("daily", "weekly", "occasional") and usage["per_week"] == 4.0


def test_a_proposal_worth_too_little_is_not_listed(conn, monkeypatch):
    finishes(conn, ["improvement"] * 3)
    monkeypatch.setattr(mining, "MIN_WEEKLY_SAVING", 1e9)
    assert propose(conn)["proposals"] == []


# --- REQ-000000418: A plugin proposal is for one project or for all projects, installed from a local marketplace ---


def test_requests_from_several_projects_make_a_plugin_for_all_projects(conn):
    finishes(conn, ["improvement"] * 4, projects=("little-workshop", "little-meals"))
    [proposal] = propose(conn)["proposals"]
    assert proposal["scope"] == plugins.SCOPE_ALL
    assert proposal["install"]["command"] == "claude plugin install finish-branch@little-workshop --scope user"
    assert proposal["install"]["list_command"] == "claude plugin list"


def test_requests_from_one_project_make_a_plugin_for_that_project(conn):
    finishes(conn, ["improvement"] * 3, projects=("little-meals",))
    [proposal] = propose(conn)["proposals"]
    assert proposal["scope"] == "project:little-meals"
    assert proposal["install"]["scope"] == "project" and "little-meals checkout" in proposal["install"]["where"]


def test_a_proposal_absorbs_the_candidates_made_of_its_steps():
    proposal = {"steps": [{"step": "requirements_run_tests"}, {"step": "job_wait"}, {"step": "version_finish"}]}
    candidates = [{"id": "a", "signature": "mcp:workshop_requirements_run_tests(project_id) → mcp:workshop_job_wait(job_id)"},
                  {"id": "b", "signature": "mcp:workshop_version_hotfix_finish(project_id)"},
                  {"id": "c", "signature": "mcp:workshop_job_wait(job_id) → Bash(git push origin)"}]
    assert plugins.absorbed(proposal, candidates) == ["a", "b"]


# --- REQ-000000419: Plugin proposals are listed and decided ---


@pytest.fixture
def usage(conn, paths):
    app = build_usage_app(conn, paths, projects=lambda: [], agent_usage=lambda since: [], create_job=lambda template: {"id": "j"},
                          emit=lambda *a: None)
    return TestClient(app)


def test_mining_stores_proposals_and_keeps_a_decision(conn, usage):
    finishes(conn, ["improvement", "hotfix", "feature"])
    result = usage.post("/v1/jobs/usage.mine_automation", json={"job_id": "j1"}).json()["result"]
    assert result["plugin_proposals"] == 1
    [listed] = usage.get("/v1/plugin_proposals").json()
    assert listed["name"] == "finish-branch" and listed["state"] == "open" and listed["steps"] and "absorbs" in listed
    assert usage.post(f"/v1/plugin_proposals/{listed['id']}", json={"state": "dismissed"}).json() == {"ok": True}
    assert usage.post(f"/v1/plugin_proposals/{listed['id']}", json={"state": "nonsense"}).json()["ok"] is False
    assert usage.get("/v1/plugin_proposals").json() == []
    conn.execute("DELETE FROM tool_calls")
    conn.commit()
    usage.post("/v1/jobs/usage.mine_automation", json={"job_id": "j2"})
    assert [p["state"] for p in usage.get("/v1/plugin_proposals", params={"state": "all"}).json()] == ["dismissed"]


def test_an_open_proposal_no_longer_found_is_dropped(conn):
    db.record_plugin_proposal(conn, {"name": "x", "family": "x", "scope": "all-projects", "weekly_value": 1.0, "occurrences": 3, "sessions": 3})
    assert db.drop_stale_plugin_proposals(conn, set()) == 1 and db.list_plugin_proposals(conn, state=None) == []


def test_proposals_are_listed_through_lws_and_readable_without_asking():
    assert "workshop_usage_plugin_proposals" in claude_templates.MCP_READ_ONLY
    args = cli_main.build_parser().parse_args(["usage", "plugin-proposals", "--state", "all"])
    assert args.func is cli_main.cmd_usage_plugin_proposals and args.state == "all"
