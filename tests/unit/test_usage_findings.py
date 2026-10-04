"""M20: findings (docs/pillars/usage.md, "Findings"): the detectors, the experiments that check a
change, the fixed optimization task, the Claude-files sync Job and what the generated files add."""

import json
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cli import main as cli_main
from common.paths import Paths
from common.shell import CommandResult
from dashboard import job_catalog
from registry import claude_sync, claude_templates, db as registry_db
from registry.app import build_app as build_registry_app
from registry.models import Project
from usage import counterfactual, db, detectors, optimize, pricing
from usage.app import build_app

NOW = datetime.now(timezone.utc).replace(microsecond=0)
TODAY = date.fromisoformat(db.local_day(NOW.isoformat()))
PRICES = pricing.DEFAULT_PRICES
REPO = Path(__file__).resolve().parents[2]


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


def session(conn, session_id, project="little-meals", *, origin="interactive", model="claude-sonnet-5-5", cwd="/work", ago=timedelta(hours=1)):
    db.upsert_session(conn, session_id, seen=(NOW - ago).isoformat(), cwd=cwd, model=model, tag_project=project,
                      job_type={"query": "agent.query", "job": "agent.run_task"}.get(origin))
    db.set_attribution(conn, session_id, project, origin)


def request(conn, request_id, session_id, *, read=10_000, write=0, out=50, ago=timedelta(hours=1), one_hour=None, source="main"):
    db.upsert_request(conn, {"request_id": request_id, "session_id": session_id, "ts": (NOW - ago).isoformat(), "model": "claude-sonnet-5-5",
                             "input_tokens": 10, "cache_read_tokens": read, "cache_write_tokens": write, "output_tokens": out,
                             "cache_write_1h_tokens": one_hour, "query_source": source, "source": "test"})


def call(conn, call_id, session_id, tool, tool_input, *, result_bytes=100, prompt="p1", request_id=None, ago=timedelta(hours=1)):
    from usage import signatures
    db.upsert_tool_call(conn, {"id": call_id, "session_id": session_id, "prompt_id": prompt, "request_id": request_id,
                               "ts": (NOW - ago).isoformat(), "tool": tool, "signature": signatures.signature(tool, tool_input),
                               "detail": signatures.detail(tool, tool_input), "result_bytes": result_bytes, "source": "test"})


def measured(conn, detector, project="little-meals", days=14):
    period = detectors.Period(TODAY - timedelta(days=days - 1), TODAY)
    found = [m for m in detectors.measure(conn, period, PRICES, only=(detector,)) if m.project == project]
    return found[0] if found else None


# --- REQ-000000335: Detectors turn the stored usage into findings ---


def test_d1_flags_a_project_whose_context_tokens_sit_in_long_requests(conn):
    session(conn, "s1")
    request(conn, "r1", "s1", read=600_000)
    request(conn, "r2", "s1", read=50_000)
    m = measured(conn, "D1")
    assert m.flagged and m.value == pytest.approx(600_010 / 650_020)
    assert m.evidence["sessions_over_500k"] == [{"session_id": "s1", "largest_context": 600_010}] and m.saving > 0
    session(conn, "s2", "little-home")
    request(conn, "r3", "s2", read=50_000)
    assert measured(conn, "D1", "little-home").flagged is False


def test_d2_flags_startup_context_that_grew_over_the_baseline(conn):
    for i, (context, ago) in enumerate([(40_000, 20), (40_000, 18), (50_000, 2), (52_000, 1)]):
        session(conn, f"s{i}", ago=timedelta(days=ago))
        conn.execute("UPDATE sessions SET startup_context = ? WHERE id = ?", (context, f"s{i}"))
    m = measured(conn, "D2", days=7)
    assert m.flagged and m.value == 51_000 and m.evidence["median_before"] == 40_000


def test_d3_counts_cache_rebuilds_that_neither_a_session_start_nor_a_compaction_explains(conn):
    session(conn, "s1")
    request(conn, "r1", "s1", read=0, write=40_000, ago=timedelta(minutes=50))  # the session's first request: not a miss
    request(conn, "r2", "s1", read=40_000, ago=timedelta(minutes=40))
    request(conn, "r3", "s1", read=0, write=45_000, ago=timedelta(minutes=30))  # a miss
    m = measured(conn, "D3")
    assert m.flagged and m.evidence["misses"] == 1 and m.value == pytest.approx(1 / 3)
    db.add_session_event(conn, "s1", NOW.isoformat(), "PostCompact", {})
    assert measured(conn, "D3").evidence["misses"] == 0


def test_d4_and_d5_flag_one_shot_runs_that_write_a_long_cache_in_many_directories(conn):
    for i in range(3):
        session(conn, f"q{i}", "little-workshop", origin="query", cwd=f"/tmp/lws-agent-query-{i}")
        request(conn, f"rq{i}", f"q{i}", read=0, write=30_000, one_hour=30_000)
    d4, d5 = measured(conn, "D4", "little-workshop"), measured(conn, "D5", "little-workshop")
    assert d4.flagged and d4.value == 3 and d4.saving > 0
    assert d5.flagged and d5.value == 3 and d5.evidence["examples"][0] == "/tmp/lws-agent-query-0"
    session(conn, "i1", "little-home")
    request(conn, "ri1", "i1", write=30_000, one_hour=30_000)
    assert measured(conn, "D4", "little-home") is None  # an interactive session is not a one-shot run


def test_d6_flags_turns_spent_polling(conn):
    session(conn, "s1")
    for i in range(12):
        request(conn, f"r{i}", "s1", read=400_000, ago=timedelta(minutes=60 - i))
        call(conn, f"c{i}", "s1", "mcp__little-workshop__workshop_job_status", {"job_id": "abc"}, request_id=f"r{i}")
    call(conn, "sleep", "s1", "Bash", {"command": "sleep 30"})
    m = measured(conn, "D6")
    assert m.flagged and m.evidence["polls"] == 13 and m.evidence["most_polls_in_one_episode"] == 13 and m.saving > 0
    assert detectors.poll_request_ids(conn) == {f"r{i}" for i in range(12)}
    assert detectors.is_poll("Bash(timeout <n> lws job status)") and not detectors.is_poll("Bash(git status)")


def test_d7_and_d8_flag_large_command_output_and_files_read_in_many_sessions(conn):
    for i in range(5):
        session(conn, f"s{i}")
        request(conn, f"r{i}", f"s{i}")
        call(conn, f"t{i}", f"s{i}", "Bash", {"command": "python3 -m pytest tests/unit"}, result_bytes=60_000)
        call(conn, f"d{i}", f"s{i}", "Read", {"file_path": "docs/policies/automation_policy.md"})
    d7, d8 = measured(conn, "D7"), measured(conn, "D8")
    # Each call is its session's last request: nothing re-reads its result, so cutting it saves nothing.
    assert d7.flagged and d7.evidence["commands"] == [{"signature": "Bash(python3 -m pytest <path>)", "calls": 5, "average_bytes": 60_000, "saving": 0.0}]
    assert d8.flagged and d8.evidence["files"] == [{"path": "docs/policies/automation_policy.md", "sessions": 5}]


def test_d9_and_d10_flag_routine_work_on_an_expensive_model_and_a_route_not_taken(conn):
    session(conn, "s1", "little-workshop", model="claude-fable-5-1")
    request(conn, "r1", "s1")
    for i in range(10):
        call(conn, f"c{i}", "s1", "mcp__little-workshop__workshop_deployment_redeploy_pillar", {"pillar": "agent", "commit": "abc"})
    d9, d10 = measured(conn, "D9", "little-workshop"), measured(conn, "D10", "little-workshop")
    assert d9.flagged and d9.value == 10
    assert d10.flagged and d10.evidence["routes_not_taken"][0]["raw"] == "mcp:workshop_deployment_redeploy_pillar"
    assert "redeploy_pillars" in d10.evidence["routes_not_taken"][0]["route"]


def test_d11_and_d12_flag_an_unused_mcp_server_and_job_runs_under_plan_pressure(conn):
    for i in range(5):
        session(conn, f"s{i}", origin="job")
        request(conn, f"r{i}", f"s{i}")
        db.add_session_event(conn, f"s{i}", NOW.isoformat(), "mcp_server", {"server": "claude.ai Spotify"})
        db.add_session_event(conn, f"s{i}", NOW.isoformat(), "mcp_server", {"server": "little-workshop"})
    call(conn, "c1", "s1", "mcp__little-workshop__workshop_core_status", {})
    d11 = measured(conn, "D11", "all")
    assert d11.flagged and d11.evidence["unused_servers"] == ["claude.ai Spotify"]
    assert measured(conn, "D12") is None  # no pressure reading at all
    db.add_plan_pressure(conn, (NOW - timedelta(hours=1)).isoformat(), "s1", 91.0, 30.0)
    assert measured(conn, "D12").flagged and measured(conn, "D12").evidence["days"][0]["five_hour_peak"] == 91.0


def test_thresholds_come_from_the_pillars_configuration(conn):
    session(conn, "s1")
    request(conn, "r1", "s1", read=300_000)
    period = detectors.Period(TODAY, TODAY)
    assert detectors.measure(conn, period, PRICES, only=("D1",))[0].flagged is True
    relaxed = {"thresholds": {"d1_long_context_share": 1.5}}
    assert detectors.measure(conn, period, PRICES, relaxed, only=("D1",))[0].flagged is False


@pytest.fixture
def usage(conn, paths):
    jobs = []

    class Runner:
        def run(self, argv, timeout=None, cwd=None):
            return CommandResult(0, "", "")

    def create_job(template):
        jobs.append(template)
        return {"id": f"job-{len(jobs)}", "status": "running"}

    app = build_app(conn, paths, runner=Runner(), projects=lambda: [], agent_usage=lambda since: [], create_job=create_job)
    client = TestClient(app)
    client.analyze, client.jobs = app.state.analyze, jobs
    return client


def test_analysis_opens_refreshes_and_resolves_findings_and_keeps_a_dismissal(usage, conn):
    session(conn, "s1")
    request(conn, "r1", "s1", read=600_000)
    first = usage.post("/v1/jobs/usage.analyze", json={"job_id": "j1"}).json()["result"]
    assert first["flagged"] >= 1
    (finding,) = [f for f in usage.get("/v1/findings").json() if f["detector"] == "D1"]
    assert finding["project"] == "little-meals" and finding["state"] == "open" and "Clear between tasks" in finding["action"]
    assert finding["evidence"]["requests_over_200k"] == 1 and finding["saving"] > 0 and finding["experiments"] == []

    usage.analyze()
    assert len([f for f in usage.get("/v1/findings").json() if f["detector"] == "D1"]) == 1  # refreshed, not duplicated

    assert usage.post(f"/v1/findings/{finding['id']}/state", json={"state": "dismissed"}).json() == {"ok": True}
    usage.analyze()
    assert db.get_finding(conn, finding["id"])["state"] == "dismissed"
    assert usage.post(f"/v1/findings/{finding['id']}/state", json={"state": "resolved"}).json()["ok"] is False

    usage.post(f"/v1/findings/{finding['id']}/state", json={"state": "open"})
    conn.execute("DELETE FROM requests")
    conn.commit()
    assert usage.analyze()["resolved"] >= 1
    assert db.get_finding(conn, finding["id"])["state"] == "resolved"
    assert usage.get("/v1/findings", params={"state": "all", "project": "little-meals"}).json()[0]["id"] == finding["id"]


def test_findings_are_ranked_by_what_a_fix_saves(conn):
    for detector, saving in (("D7", 0.0), ("D1", 12.0), ("D6", 3.0)):
        db.record_finding(conn, {"detector": detector, "project": "p", "title": "t", "metric": "m", "value": 1.0, "saving": saving,
                                 "action": "a", "evidence": {}}, "2026-09-21", "2026-10-04")
    assert [f["detector"] for f in db.list_findings(conn)] == ["D1", "D6", "D7"]


# --- REQ-000000336: An applied optimization is measured before and after ---


def test_an_experiment_that_moved_its_metric_resolves_the_finding_and_one_that_did_not_reopens_it(usage, conn):
    session(conn, "s1")
    request(conn, "r1", "s1", read=600_000)
    usage.analyze()
    finding = db.list_findings(conn)[0]
    assert usage.post("/v1/jobs/usage.optimize", json={"job_id": "j1", "project_id": "little-meals", "params": {"finding_ids": [finding["id"]]}}).json()["outcome"] == "succeeded"
    (experiment,) = db.list_experiments(conn)
    assert experiment["state"] == "running" and experiment["before_value"] == pytest.approx(1.0) and experiment["change"] == "job job-1"
    assert db.get_finding(conn, finding["id"])["state"] == "accepted"

    not_due = usage.analyze()
    assert not_due["experiments_measured"] == []  # two weeks have not passed

    result = usage.post("/v1/jobs/usage.measure_experiment", json={"job_id": "j2", "params": {"experiment_id": experiment["id"]}}).json()["result"]
    assert result["improved"] is False and db.get_finding(conn, finding["id"])["state"] == "open"  # nothing changed: reopened

    conn.execute("UPDATE experiments SET state = 'running', after_value = NULL, created_at = ?", ((NOW - timedelta(days=15)).isoformat(),))
    conn.execute("DELETE FROM requests")
    request(conn, "r2", "s1", read=20_000)
    conn.commit()
    due = usage.analyze()  # old enough now: measured on its own
    assert due["experiments_measured"][0]["improved"] is True
    assert db.get_finding(conn, finding["id"])["state"] == "resolved"
    after = usage.get("/v1/findings", params={"state": "all"}).json()[0]["experiments"][0]
    assert after["state"] == "improved" and after["after_value"] == 0.0
    assert usage.post("/v1/jobs/usage.measure_experiment", json={"job_id": "j3", "params": {"experiment_id": "nope"}}).json()["outcome"] == "failed"


# --- REQ-000000337: Optimize Claude usage is a fixed task built from the chosen findings ---


def test_the_optimize_job_starts_one_fixed_agent_task_with_the_findings_digest(usage, conn):
    session(conn, "s1")
    request(conn, "r1", "s1", read=600_000)
    usage.analyze()
    finding = db.list_findings(conn)[0]
    usage.post("/v1/jobs/usage.optimize", json={"job_id": "j1", "project_id": "little-meals", "params": {"finding_ids": [finding["id"], "unknown"]}})
    (job,) = usage.jobs
    assert (job["type"], job["target_pillar"], job["project_id"], job["queue_key"], job["created_by"]) == ("agent.run_task", "agent", "little-meals", "little-meals", "usage")
    params = job["params"]
    assert params["intent"] == "improvement" and params["review_mode"] == "manual" and params["title"] == "Optimize Claude usage"
    assert "### D1: Long sessions re-read a large context" in params["description"] and '"requests_over_200k": 1' in params["description"]
    assert "Never edit what little-workshop generates" in params["description"]


def test_the_digest_is_the_same_text_for_the_same_findings():
    finding = {"detector": "D6", "title": "Turns spent polling or waiting", "metric": "polling share", "value": 0.125, "saving": 3.5,
               "action": "Wait with workshop_job_wait.", "evidence": {"b": 1, "a": 2}, "period_start": "2026-09-21", "period_end": "2026-10-04"}
    text = optimize.digest([finding])
    assert text == optimize.digest([dict(finding)]) and "polling share = 0.125" in text and text.index('"a": 2') < text.index('"b": 1')
    assert optimize.digest([]) == "(no findings were chosen)"


def test_the_optimize_job_refuses_findings_of_another_project_and_an_unreachable_orchestration(conn, paths):
    def down(template):
        raise RuntimeError("Orchestration is unreachable")

    client = TestClient(build_app(conn, paths, projects=lambda: [], agent_usage=lambda since: [], create_job=down))
    finding_id = db.record_finding(conn, {"detector": "D1", "project": "little-meals", "title": "t", "metric": "m", "value": 1.0,
                                          "saving": 1.0, "action": "a", "evidence": {}}, "2026-09-21", "2026-10-04")
    conn.commit()
    body = {"job_id": "j", "project_id": "little-home", "params": {"finding_ids": [finding_id]}}
    assert "none of the chosen findings" in client.post("/v1/jobs/usage.optimize", json=body).json()["error"]
    body["project_id"] = "little-meals"
    assert client.post("/v1/jobs/usage.optimize", json=body).json() == {"outcome": "failed", "error": "Orchestration is unreachable"}
    assert db.get_finding(conn, finding_id)["state"] == "open" and db.list_experiments(conn) == []
    assert client.post("/v1/jobs/usage.optimize", json={"job_id": "j", "params": {}}).json()["outcome"] == "failed"


def test_the_catalog_offers_the_three_usage_jobs():
    job, error = job_catalog.build_job("little-meals", "optimize_usage", {"arg_findings": ["f1", "f2"]})
    assert error is None and job == {"type": "usage.optimize", "target_pillar": "usage", "project_id": "little-meals", "queue_key": None,
                                     "params": {"finding_ids": ["f1", "f2"]}}
    assert job_catalog.build_job("little-meals", "optimize_usage", {})[1] == "Choose at least one of: findings to address."
    assert job_catalog.build_job("little-meals", "apply_claude_config", {})[1] == "Apply common Claude configuration is not available for little-meals."
    sync, _ = job_catalog.build_job("little-workshop", "apply_claude_config", {"arg_write": ["on"]})
    assert (sync["type"], sync["target_pillar"], sync["params"]) == ("registry.sync_claude_files", "registry", {"write": True})
    dry, _ = job_catalog.build_job("little-workshop", "configure_claude_telemetry", {})
    assert (dry["type"], dry["params"]) == ("usage.configure_claude", {"apply": False})


# --- REQ-000000338: The Claude files of every project are synced by one Job, each on its own branch ---


def _git(args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def registry(tmp_path, paths):
    conn = registry_db.connect(tmp_path / "registry.db")
    checkout = tmp_path / "little-widget"
    checkout.mkdir()
    (checkout / "pyproject.toml").write_text("[project]\nname = 'little-widget'\n")
    _git(["init", "-b", "dev"], checkout)
    _git(["config", "user.email", "t@example.com"], checkout)
    _git(["config", "user.name", "T"], checkout)
    _git(["add", "-A"], checkout)
    _git(["commit", "-m", "first"], checkout)
    registry_db.insert_project(conn, Project(name="little-widget", path=str(checkout)))
    started = []

    def start_branch(project_id, name):
        started.append((project_id, name))
        _git(["checkout", "-b", f"improvement/{name}"], checkout)
        return {"status": "succeeded", "result": {"branch": f"improvement/{name}"}}

    client = TestClient(build_registry_app(conn, paths, start_branch=start_branch))
    yield client, checkout, started
    conn.close()


def test_the_sync_job_reports_what_differs_without_writing(registry):
    client, checkout, started = registry
    result = client.post("/v1/jobs/registry.sync_claude_files", json={"job_id": "j1"}).json()["result"]
    (report,) = result["projects"]
    assert result["differing"] == 1 and report["written"] is False and "created .claude/settings.json" in report["changed"]
    assert not (checkout / ".claude").exists() and started == []
    assert client.post("/v1/jobs/registry.sync_claude_files", json={"job_id": "j2", "project_id": "nope"}).json()["outcome"] == "failed"


def test_the_sync_job_writes_on_an_improvement_branch_commits_and_never_finishes(registry):
    client, checkout, started = registry
    (report,) = client.post("/v1/jobs/registry.sync_claude_files", json={"job_id": "j1", "params": {"write": True}}).json()["result"]["projects"]
    assert report["written"] is True and report["branch"] == "improvement/claude-files-sync" and "committed" in report["detail"]
    assert started == [("little-widget", "claude-files-sync")]
    assert _git(["branch", "--show-current"], checkout) == "improvement/claude-files-sync"
    assert _git(["log", "-1", "--format=%s"], checkout) == "impr (-1): claude-files-sync" and _git(["status", "--porcelain"], checkout) == ""
    assert _git(["rev-list", "--count", "dev"], checkout) == "1"  # dev is untouched: nothing was merged
    settings = json.loads((checkout / ".claude" / "settings.json").read_text())
    assert settings["enabledPlugins"] == {"pyright-lsp@claude-plugins-official": True}

    again = client.post("/v1/jobs/registry.sync_claude_files", json={"job_id": "j2", "params": {"write": True}}).json()["result"]
    assert again["differing"] == 0 and again["projects"][0]["detail"] == "in sync" and len(started) == 1


def test_the_sync_job_leaves_a_checkout_with_uncommitted_changes_alone(registry):
    client, checkout, started = registry
    (checkout / "notes.txt").write_text("work in progress")
    (report,) = client.post("/v1/jobs/registry.sync_claude_files", json={"job_id": "j1", "params": {"write": True}}).json()["result"]["projects"]
    assert report["written"] is False and "uncommitted changes" in report["detail"] and started == []


def test_the_command_line_tool_is_the_same_sync(tmp_path, capsys):
    checkout = tmp_path / "little-widget"
    checkout.mkdir()
    report = claude_sync.pending(checkout, "little-widget", "bazel")
    assert report["template"] == "bazel" and claude_templates.TEST_FILTER_PATH in report["files"]
    claude_sync.write(checkout, report["files"])
    assert claude_sync.pending(checkout, "little-widget", "bazel")["changed"] == []


# --- REQ-000000339: Generated Claude files keep heavy folders, test output and policy reading out of the context ---


def test_generated_settings_deny_reading_heavy_folders_and_enable_the_languages_plugins():
    python = claude_templates.settings("little-widget", "python", plugins=("pyright-lsp",))
    assert {"Read(./bazel-*/**)", "Read(./node_modules/**)", "Read(./.venv*/**)"} <= set(python["permissions"]["deny"])
    assert python["enabledPlugins"] == {"pyright-lsp@claude-plugins-official": True}
    unity = claude_templates.settings("ethereal-lands", "unity")
    assert "Read(./Library/**)" in unity["permissions"]["deny"] and "enabledPlugins" not in unity
    with pytest.raises(ValueError):
        claude_templates.settings("little-widget", plugins=("not-a-plugin",))
    assert not any(key.startswith("OTEL") for key in (python.get("env") or {}))  # telemetry is user scope, never project scope


def test_plugins_are_detected_from_the_checkouts_languages(tmp_path):
    (tmp_path / "pyproject.toml").write_text("")
    (tmp_path / "package.json").write_text("{}")
    (tmp_path / "main.cc").write_text("")
    assert claude_sync.detect_plugins(tmp_path) == ("pyright-lsp", "typescript-lsp", "clangd-lsp")
    web = tmp_path / "web"
    (web / "src").mkdir(parents=True)
    (web / "src" / "package.json").write_text("{}")  # a SvelteKit app kept under src/
    assert claude_sync.detect_plugins(web) == ("typescript-lsp",)
    unity = tmp_path / "game"
    (unity / "ProjectSettings").mkdir(parents=True)
    (unity / "ProjectSettings" / "ProjectVersion.txt").write_text("")
    assert claude_sync.detect_plugins(unity) == ("csharp-lsp",)


def test_a_projects_own_plugins_and_read_denies_survive_the_sync():
    current = claude_templates.settings("little-widget", "python")
    current["enabledPlugins"] = {"my-plugin@somewhere": True}
    current["permissions"]["deny"].append("Read(./data/**)")
    merged = claude_sync.merged_settings("little-widget", "python", current, {"plugins": ("pyright-lsp",)})
    assert merged["enabledPlugins"] == {"my-plugin@somewhere": True, "pyright-lsp@claude-plugins-official": True}
    assert "Read(./data/**)" in merged["permissions"]["deny"] and merged["permissions"]["deny"].count("Read(./bazel-*/**)") == 1
    assert len(merged["hooks"]["PreToolUse"]) == 2  # the generated entries are not duplicated


def _filter(args, stdin):
    script = REPO / claude_templates.TEST_FILTER_PATH
    return subprocess.run([sys.executable, str(script), *args], input=stdin, capture_output=True, text=True, env={"CLAUDE_PROJECT_DIR": "/proj", "PATH": "/usr/bin:/bin"})


def test_the_filter_hook_rewrites_a_plain_test_command_and_nothing_else():
    def hook(command, tool="Bash"):
        out = _filter([], json.dumps({"tool_name": tool, "tool_input": {"command": command, "description": "d"}})).stdout
        return json.loads(out)["hookSpecificOutput"] if out.strip() else None

    rewritten = hook("python3 -m pytest tests/unit -q")
    assert rewritten["permissionDecision"] == "allow" and rewritten["updatedInput"]["description"] == "d"
    assert rewritten["updatedInput"]["command"] == 'set -o pipefail; python3 -m pytest tests/unit -q 2>&1 | python3 "/proj/.claude/hooks/test_output_filter.py" --filter'
    assert hook("bazel test //...") is not None and hook("tools/run-unity-tests") is not None
    for untouched in ("python3 -m pytest tests/unit | tail -5", "cd x && pytest", "git status", "pytest > out.txt", "echo pytest"):
        assert hook(untouched) is None, untouched
    assert hook("pytest", tool="Read") is None
    assert _filter([], "not json").stdout == ""


def test_the_filter_keeps_short_output_whole_and_long_output_to_failures_and_the_end(tmp_path):
    short = "\n".join(f"line {i}" for i in range(20))
    assert _filter(["--filter"], short).stdout.strip() == short
    lines = [f"test_{i} PASSED" for i in range(1000)]
    lines[500] = "FAILED tests/unit/test_x.py::test_y - AssertionError"
    out = _filter(["--filter"], "\n".join(lines) + "\n3 failed, 997 passed").stdout.splitlines()
    assert out[0].startswith("[test output: 1001 lines, filtered") and "FAILED tests/unit/test_x.py::test_y - AssertionError" in out
    assert out[-1] == "3 failed, 997 passed" and len(out) < 60 and any("line(s) left out" in line for line in out)
    saved = Path(out[0].rsplit(" in ", 1)[1].rstrip("]"))
    assert len(saved.read_text().splitlines()) == 1001
    saved.unlink()


def test_the_policies_digest_skill_is_generated_and_guarded():
    files = claude_templates.claude_files("little-widget")
    skill = files[claude_templates.POLICIES_SKILL_PATH]
    assert skill.startswith("---\nname: policies-digest\ndescription: ") and "--project-id little-widget" in skill
    compile(files[claude_templates.POLICIES_SCRIPT_PATH], "digest.py", "exec")
    reasons = claude_templates.generated_files_reasons()
    assert {claude_templates.POLICIES_SKILL_PATH, claude_templates.POLICIES_SCRIPT_PATH, claude_templates.TEST_FILTER_PATH} <= set(reasons)


def test_the_digest_script_prints_the_policies_attached_to_the_project(tmp_path):
    fake = tmp_path / "lws"
    fake.write_text('''#!/usr/bin/env python3
import json, sys
if sys.argv[1:4] == ["policy", "projects", "list"]:
    print(json.dumps({"projects": [{"policy_id": "p1", "gating_enabled": True}, {"policy_id": "p2", "gating_enabled": False}]}))
else:
    print(json.dumps({"policies": [
        {"id": "p1", "topic": "commit-cites-requirement", "gating": ["commit", "branch_finish"], "ears_text": "A commit\\n shall cite a requirement.", "test_script": "LONG"},
        {"id": "p2", "topic": "advice", "gating": [], "ears_text": "The project shall be tidy."},
        {"id": "p3", "topic": "not-attached", "gating": ["commit"], "ears_text": "x"}]}))
''')
    fake.chmod(0o755)
    script = REPO / claude_templates.POLICIES_SCRIPT_PATH
    env = {"PATH": f"{tmp_path}:/usr/bin:/bin"}
    out = subprocess.run([sys.executable, str(script), "--project-id", "little-widget"], capture_output=True, text=True, env=env).stdout
    assert "- commit-cites-requirement [commit, branch_finish]: A commit shall cite a requirement." in out
    assert "- advice [advisory] (gating off for this project): The project shall be tidy." in out
    assert "not-attached" not in out and "LONG" not in out and out.strip().endswith("2 policies for little-widget")
    only = subprocess.run([sys.executable, str(script), "--project-id", "little-widget", "--gate", "commit"], capture_output=True, text=True, env=env).stdout
    assert only.strip().endswith("1 policy for little-widget gating commit")


# --- REQ-000000340: Findings are read through lws, the MCP server and the Dashboard ---


def test_lws_usage_findings_and_analyze(capsys, monkeypatch):
    asked = []
    monkeypatch.setattr(cli_main.client, "usage_findings", lambda p, **kw: asked.append(kw) or [{"detector": "D1"}])
    assert cli_main.main(["usage", "findings", "--project-id", "little-meals", "--state", "all"]) == 0
    assert json.loads(capsys.readouterr().out) == [{"detector": "D1"}] and asked == [{"project": "little-meals", "state": "all"}]
    jobs = []
    monkeypatch.setattr(cli_main.client, "create_job", lambda p, **kw: jobs.append(kw) or {"status": "succeeded"})
    assert cli_main.main(["usage", "analyze"]) == 0 and jobs[0]["type"] == "usage.analyze"


def test_findings_are_readable_in_every_project_and_analysis_is_little_workshops():
    project = claude_templates.settings("little-widget")["permissions"]
    assert f"{claude_templates.MCP}workshop_usage_findings" in project["allow"]
    assert f"{claude_templates.MCP}workshop_usage_analyze" in project["ask"]
    assert "Bash(lws usage findings --project-id little-widget *)" in project["allow"]


# --- REQ-000000359: A finding says what fixes it, and its saving is replayed where the store allows ---


def test_every_detector_says_what_fixes_it_and_none_is_automation(usage, conn):
    assert set(detectors.FIXED_BY) == set(detectors.DETECTORS)
    assert not any("automat" in fix for fix in detectors.FIXED_BY.values())
    assert detectors.SAVING_BASIS["D6"] == detectors.SAVING_BASIS["D7"] == "replayed" and detectors.SAVING_BASIS["D1"] == "rough estimate"
    for detector in ("D6", "D9"):
        db.record_finding(conn, {"detector": detector, "project": "p", "title": "t", "metric": "m", "value": 1.0, "saving": 0.0,
                                 "action": "a", "evidence": {}}, "2026-09-21", "2026-10-04")
    conn.commit()
    found = {f["detector"]: f for f in usage.get("/v1/findings").json()}
    assert (found["D6"]["fixed_by"], found["D6"]["saving_basis"]) == ("a route that exists (workshop_job_wait)", "replayed")
    assert (found["D9"]["fixed_by"], found["D9"]["saving_basis"]) == ("a setting (the model)", None)


def test_d6_replays_each_run_of_polling_turns_as_the_waits_it_needs(conn):
    session(conn, "s1")
    for i in range(6):
        request(conn, f"r{i}", "s1", read=100_000 + i * 1_000, ago=timedelta(minutes=30 - i))
    call(conn, "e0", "s1", "Edit", {"file_path": "a.py"}, request_id="r0", ago=timedelta(minutes=30))
    polls = []
    for i in range(1, 5):  # four turns that only poll, over three minutes: two waits of 120 s
        call(conn, f"c{i}", "s1", "mcp__little-workshop__workshop_job_status", {"job_id": "j"}, request_id=f"r{i}",
             result_bytes=900 + i, ago=timedelta(minutes=30 - i))
        polls.append(f"c{i}")
    # A poll in a turn that also works is not waiting: it stays out of the run.
    call(conn, "c5", "s1", "mcp__little-workshop__workshop_job_status", {"job_id": "j"}, request_id="r5", ago=timedelta(minutes=25))
    call(conn, "e5", "s1", "Edit", {"file_path": "a.py"}, request_id="r5", ago=timedelta(minutes=25))
    conn.commit()
    m = measured(conn, "D6")
    replay, calibration = counterfactual.prepare(conn, PRICES)
    run = [dict(conn.execute("SELECT * FROM tool_calls WHERE id = ?", (c,)).fetchone()) for c in polls]
    expected = replay.occurrence(run, calibration, trigger_ok=False, kept_turns=2, reply_bytes=904)["called"]
    assert m.saving == pytest.approx(expected / 2) and m.saving > 0 and m.evidence["turns_a_wait_saves"] == 2


def test_d6_a_lone_poll_is_already_one_turn_and_saves_nothing(conn):
    session(conn, "s1")
    for i in range(3):
        request(conn, f"r{i}", "s1", ago=timedelta(minutes=10 - i))
    call(conn, "e0", "s1", "Edit", {"file_path": "a.py"}, request_id="r0", ago=timedelta(minutes=10))
    call(conn, "p1", "s1", "Bash", {"command": "sleep 30"}, request_id="r1", ago=timedelta(minutes=9))
    call(conn, "e2", "s1", "Edit", {"file_path": "a.py"}, request_id="r2", ago=timedelta(minutes=8))
    m = measured(conn, "D6")
    assert m.evidence["polls"] == 1 and m.saving == pytest.approx(0) and m.evidence["turns_a_wait_saves"] == 0


def test_d7_replays_each_large_result_cut_to_what_a_filter_leaves(conn):
    session(conn, "s1")
    contexts = [50_000, 60_000, 70_000, 80_000, 10_000, 20_000]  # the fifth request follows a compaction
    for i, read in enumerate(contexts):
        request(conn, f"r{i}", "s1", read=read, ago=timedelta(minutes=30 - i))
    for i in range(5):
        call(conn, f"t{i}", "s1", "Bash", {"command": "python3 -m pytest tests/unit"}, request_id="r0", result_bytes=60_000,
             ago=timedelta(minutes=30))
    conn.commit()
    m = measured(conn, "D7")
    # What a context token cost in each later request up to the compaction: r1 to r3.
    later = [dict(row) for row in conn.execute("SELECT * FROM requests WHERE request_id IN ('r1', 'r2', 'r3')")]
    per_token = sum((p["input"] + p["cache_read"] + p["cache_write"]) / (r["input_tokens"] + r["cache_read_tokens"] + r["cache_write_tokens"])
                    for r in later for p in [pricing.weighted(r, PRICES)])
    _, calibration = counterfactual.prepare(conn, PRICES)
    expected = 5 * (60_000 - detectors.FILTERED_BYTES) / calibration.bytes_per_token * per_token / 2
    assert m.saving == pytest.approx(expected) and m.evidence["commands"][0]["saving"] == pytest.approx(expected, abs=0.005)
