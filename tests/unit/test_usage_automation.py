"""M21: automation (docs/pillars/usage.md "Automation mining", docs/pillars/orchestration.md
"Triggers"): mining repeated work into candidates, workshop triggers, the Jobs that replace what
Claude did by hand."""

import json
import subprocess
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cli import main as cli_main
from common import events, redeploy_plan
from common.paths import Paths
from deployment import db as deployment_db
from deployment.app import build_app as build_deployment_app
from orchestration import db as orchestration_db, triggers
from orchestration.app import build_app as build_orchestration_app
from orchestration.dispatch import dispatch_ready_jobs
from orchestration.models import Job, JobStatus, JobTemplate
from registry import claude_templates
from requirements import db as requirements_db, markers
from requirements.app import build_app as build_requirements_app
from requirements.models import Requirement, TestSuite
from usage import db, mining, pricing, signatures
from usage.app import build_app as build_usage_app

NOW = datetime.now(timezone.utc).replace(microsecond=0)
TODAY = date.fromisoformat(db.local_day(NOW.isoformat()))
PRICES = pricing.DEFAULT_PRICES


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


def episode(conn, session_id, prompt, calls, *, project="little-workshop", ago=timedelta(hours=1), success=True):
    """One prompt's tool calls, in order: [(tool, input), ...]."""
    db.upsert_session(conn, session_id, seen=(NOW - ago).isoformat(), tag_project=project)
    db.set_attribution(conn, session_id, project, "interactive")
    db.upsert_request(conn, {"request_id": f"r-{session_id}-{prompt}", "session_id": session_id, "ts": (NOW - ago).isoformat(),
                             "model": "claude-fable-5-1", "input_tokens": 10, "cache_read_tokens": 400_000, "output_tokens": 50, "source": "test"})
    for index, (tool, tool_input) in enumerate(calls):
        db.upsert_tool_call(conn, {
            "id": f"c-{session_id}-{prompt}-{index}", "session_id": session_id, "prompt_id": prompt,
            "ts": (NOW - ago + timedelta(seconds=index)).isoformat(), "tool": tool, "signature": signatures.signature(tool, tool_input),
            "detail": signatures.detail(tool, tool_input), "success": success, "source": "test"})


REDEPLOY = ("mcp__little-workshop__workshop_deployment_redeploy_pillar", {"pillar": "agent", "commit": "abc1234"})
STATUS = ("mcp__little-workshop__workshop_job_status", {"job_id": "abc"})


# --- REQ-000000341: Repeated tool-call episodes are mined into scored candidates ---


def test_a_signature_seen_in_three_sessions_is_a_deterministic_candidate(conn):
    for i in range(3):
        episode(conn, f"s{i}", "p1", [REDEPLOY, STATUS], ago=timedelta(days=i, hours=1))
    found = {c["signature"]: c for c in mining.mine(conn, PRICES, today=TODAY)}
    single = found["mcp:workshop_deployment_redeploy_pillar(commit, pillar)"]
    assert single["class"] == "deterministic" and single["score"] == 5 and all(single["checks"].values())
    assert single["occurrences"] == 3 and single["sessions"] == 3 and single["weekly_value"] > 0 and single["projects"] == {"little-workshop": 3}
    assert single["examples"][0]["calls"] == ["agent"]  # what the call acted on, never file contents
    sequence = found["mcp:workshop_deployment_redeploy_pillar(commit, pillar) → mcp:workshop_job_status(job_id)"]
    assert sequence["occurrences"] == 3 and "blocking wait" in sequence["proposed_form"]


def test_what_happens_rarely_or_is_only_exploration_is_no_candidate(conn):
    episode(conn, "s1", "p1", [REDEPLOY])
    episode(conn, "s2", "p1", [REDEPLOY])
    for i in range(4):
        episode(conn, f"e{i}", "p1", [("Read", {"file_path": "a.py"}), ("Grep", {"pattern": "x"})])
    assert mining.mine(conn, PRICES, today=TODAY) == []
    for i in range(10):
        episode(conn, "s1", f"q{i}", [REDEPLOY])
    (candidate,) = mining.mine(conn, PRICES, today=TODAY)  # ten times, though in two sessions
    assert candidate["occurrences"] == 12 and candidate["checks"]["output_stability"] is False and candidate["class"] == "hybrid"


def test_work_that_writes_content_or_takes_prose_is_heuristic(conn):
    for i in range(3):
        episode(conn, f"s{i}", "p1", [("Edit", {"file_path": "a.py"}), ("Bash", {"command": "python3 -m pytest tests/unit"})])
        episode(conn, f"t{i}", "p1", [("Agent", {"subagent_type": "test-runner", "prompt": "run the suite"})], success=None)
    found = {c["signature"]: c for c in mining.mine(conn, PRICES, today=TODAY)}
    edit = found["Edit(<path>) → Bash(python3 -m pytest <path>)"]
    assert edit["checks"]["no_content_creation"] is False and edit["class"] == "heuristic" and "Stays with Claude" in edit["proposed_form"]
    assert "Agent(test-runner)" not in found  # a call whose argument is prose alone is nothing code could make
    assert "subagent that returns only the failures" in found["Bash(python3 -m pytest <path>)"]["proposed_form"]


def test_candidates_are_ranked_by_what_they_cost_per_week(conn):
    for i in range(3):
        episode(conn, f"s{i}", "p1", [REDEPLOY])
    for i in range(6):
        episode(conn, f"t{i}", "p1", [("Bash", {"command": "python3 tools/sync_policies.py"})])
    ranked = [c["signature"] for c in mining.mine(conn, PRICES, today=TODAY)]
    assert ranked == ["Bash(python3 tools/sync_policies.py)", "mcp:workshop_deployment_redeploy_pillar(commit, pillar)"]


def test_a_shell_signature_names_the_command_that_does_the_work():
    sign = lambda command: signatures.signature("Bash", {"command": command})
    assert sign("cd /home/x/repo; python3 -m pytest tests/unit -q") == "Bash(python3 -m pytest <path>)"
    assert sign("cd /home/x/repo\nexport FOO=1\nlws job list") == "Bash(lws job list)"
    assert sign("cd /home/x/repo") == "Bash(cd <path>)"
    assert sign("python3 - <<'EOF'\np='/a/b'\nprint(p)\nEOF") == "Bash(python3 <inline>)"
    assert sign("cd /x && python3 -c 'print(1)'") == "Bash(python3 <inline>)"
    assert sign("bash -lc 'bazel test //...'") == "Bash(bash <inline>)"
    assert sign("") == "Bash(?)" and signatures.signature("Bash", None) == "Bash(?)"


def test_reading_writing_and_inline_scripts_alone_are_no_candidates(conn):
    noise = [("Bash", {"command": "cd /home/x/repo"}), ("Bash", {"command": ""}), ("Bash", {"command": "cat docs/a.md"}),
             ("Bash", {"command": "python3 - <<'EOF'\nprint(1)\nEOF"}), ("Write", {"file_path": "a.py"}), ("Edit", {"file_path": "a.py"}),
             ("Bash", {"command": "git status --short"}), ("Bash", {"command": "ls -la src"})]
    for i in range(4):
        episode(conn, f"s{i}", "p1", noise + [REDEPLOY])
    found = {c["signature"] for c in mining.mine(conn, PRICES, today=TODAY)}
    assert "mcp:workshop_deployment_redeploy_pillar(commit, pillar)" in found
    singles = {signature for signature in found if " → " not in signature}
    assert singles == {"mcp:workshop_deployment_redeploy_pillar(commit, pillar)"}
    assert all("workshop_deployment_redeploy_pillar" in signature for signature in found)  # a sequence needs a call that code could make


def test_mining_again_resigns_stored_calls_and_drops_open_candidates_that_are_gone(usage, conn):
    for i in range(3):
        episode(conn, f"s{i}", "p1", [REDEPLOY])
        conn.execute("INSERT INTO tool_calls (id, session_id, prompt_id, ts, tool, signature, detail, source) VALUES (?, ?, 'p1', ?, 'Bash', 'Bash(cd <path>)', ?, 't')",
                     (f"old{i}", f"s{i}", NOW.isoformat(), "cd /repo; lws job list"))
    db.record_candidate(conn, {"signature": "Bash(cd <path>)", "class": "deterministic", "score": 5, "occurrences": 9, "sessions": 9,
                               "weekly_value": 99.0, "median_cost": 1.0, "proposed_form": "x"})
    kept = db.record_candidate(conn, {"signature": "Bash(old but dismissed)", "class": "hybrid", "score": 3, "occurrences": 1, "sessions": 1,
                                      "weekly_value": 1.0, "median_cost": 1.0, "proposed_form": "x"})
    db.update_candidate(conn, kept, state="dismissed")
    conn.commit()
    usage.mine()
    signatures_now = {c["signature"]: c["state"] for c in db.list_candidates(conn, state=None)}
    assert "Bash(cd <path>)" not in signatures_now  # an open candidate mining no longer finds is dropped
    assert signatures_now["Bash(old but dismissed)"] == "dismissed"  # a person's decision is kept
    assert "Bash(lws job list)" in signatures_now  # the stored calls were signed again from the command kept with them


def test_a_transcripts_tool_calls_are_grouped_by_prompt(tmp_path):
    from usage import transcripts
    base = {"sessionId": "s", "timestamp": NOW.isoformat()}
    use = lambda i: {**base, "type": "assistant", "requestId": f"r{i}", "message": {"model": "m", "usage": {"input_tokens": 1}, "content": [
        {"type": "tool_use", "id": f"t{i}", "name": "Bash", "input": {"command": "ls"}}]}}
    result = lambda i: {**base, "type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": f"t{i}", "content": "x"}]}}
    lines = [{**base, "type": "user", "message": {"content": "first"}}, use(1), result(1), use(2), result(2),
             {**base, "type": "user", "message": {"content": [{"type": "text", "text": "second"}]}}, use(3)]
    path = tmp_path / "s.jsonl"
    path.write_text("\n".join(json.dumps(line) for line in lines))
    assert [c["prompt_id"] for c in transcripts.parse_transcript(path)["tool_calls"]] == ["s:1", "s:1", "s:2"]


# --- REQ-000000342: A candidate is made a task, dismissed or scheduled by a person ---


@pytest.fixture
def usage(conn, paths):
    jobs = []

    def create_job(template):
        jobs.append(template)
        return {"id": f"job-{len(jobs)}"}

    app = build_usage_app(conn, paths, projects=lambda: [], agent_usage=lambda since: [], create_job=create_job, emit=lambda *a: None)
    client = TestClient(app)
    client.jobs, client.mine = jobs, app.state.mine_automation
    return client


def test_mining_keeps_candidates_and_a_person_decides(usage, conn):
    for i in range(3):
        episode(conn, f"s{i}", "p1", [REDEPLOY])
    result = usage.post("/v1/jobs/usage.mine_automation", json={"job_id": "j1"}).json()["result"]
    assert result == {"candidates": 1, "by_class": {"deterministic": 1, "hybrid": 0, "heuristic": 0}}
    (candidate,) = usage.get("/v1/candidates").json()
    assert candidate["state"] == "open" and candidate["schedule"]["kind"] == "recurring"

    assert usage.post(f"/v1/candidates/{candidate['id']}", json={"state": "accepted"}).json()["ok"] is False  # only by making it a task
    assert usage.post(f"/v1/candidates/{candidate['id']}", json={"state": "dismissed"}).json() == {"ok": True}
    usage.mine()
    assert usage.get("/v1/candidates").json() == [] and usage.get("/v1/candidates", params={"state": "all"}).json()[0]["state"] == "dismissed"
    assert usage.jobs == []  # nothing is implemented on its own


def test_make_it_a_task_starts_one_fixed_improvement_task_on_little_workshop(usage, conn):
    for i in range(3):
        episode(conn, f"s{i}", "p1", [REDEPLOY], project="little-meals")
    usage.mine()
    candidate = db.list_candidates(conn)[0]
    done = usage.post("/v1/jobs/usage.automate", json={"job_id": "j1", "params": {"candidate_id": candidate["id"]}}).json()
    assert done["outcome"] == "succeeded" and db.get_candidate(conn, candidate["id"])["state"] == "accepted"
    (job,) = usage.jobs
    assert (job["type"], job["project_id"], job["queue_key"], job["created_by"]) == ("agent.run_task", "little-workshop", "little-workshop", "usage")
    description = job["params"]["description"]
    assert job["params"]["intent"] == "improvement" and job["params"]["review_mode"] == "manual"
    assert "`mcp:workshop_deployment_redeploy_pillar(commit, pillar)`" in description and "deterministic (determinism score 5 of 5" in description
    assert "never a free-form job" in description and "little-meals: `agent`" in description
    assert usage.post("/v1/jobs/usage.automate", json={"job_id": "j2", "params": {"candidate_id": "nope"}}).json()["outcome"] == "failed"


def test_a_schedule_is_recommended_from_how_often_it_happened_at_night_one_after_another():
    daily = mining.recommend_schedule([(NOW - timedelta(days=d)).isoformat() for d in range(10)], slot=0)
    assert daily["frequency"] == "daily" and daily["days_of_week"] == list(range(7)) and daily["time_of_day"] == "02:00"
    monday = datetime(2026, 9, 28, 10, tzinfo=timezone.utc)
    weekly = mining.recommend_schedule([monday.isoformat(), (monday + timedelta(days=7)).isoformat()], slot=3)
    assert weekly["frequency"] == "weekly" and weekly["days_of_week"] == [0] and weekly["time_of_day"] == "03:30"


def test_tokens_avoided_count_the_replacements_runs(usage, conn):
    for i in range(3):
        episode(conn, f"s{i}", "p1", [REDEPLOY])
    usage.mine()
    candidate = db.list_candidates(conn)[0]
    assert usage.post(f"/v1/candidates/{candidate['id']}", json={"replacement": "mcp:workshop_deployment_redeploy_changed_pillars", "state": "done"}).json()["ok"]
    for i in range(2):
        episode(conn, f"n{i}", "p1", [("mcp__little-workshop__workshop_deployment_redeploy_changed_pillars", {})])
    usage.mine()
    done = db.get_candidate(conn, candidate["id"])
    assert done["state"] == "done" and done["tokens_avoided"] == pytest.approx(2 * done["median_cost"]) and done["tokens_avoided"] > 0


# --- REQ-000000343: A trigger creates a Job when an event happens and never one that merges, finishes or deletes ---


@pytest.fixture
def orch(tmp_path):
    connection = orchestration_db.connect(tmp_path / "orchestration.db")
    yield connection, TestClient(build_orchestration_app(connection, socket_lookup=lambda pillar: None))
    connection.close()


def _trigger(client, event="job.succeeded", event_filter=None, job_type="usage.doctor", **template):
    return client.post("/v1/triggers", json={"name": "t", "on": {"event": event, "filter": event_filter or {}},
                                              "job_template": {"type": job_type, "target_pillar": "usage", **template}})


@pytest.mark.parametrize("job_type", ["version.feature_finish", "version.improvement_finish", "version.release_finish", "version.release",
                                      "project.remove", "build.package_delete", "agent.finish_task", "requirements.delete_requirement",
                                      "version.stabilize", "usage.purge_project"])
def test_a_trigger_cannot_target_a_job_that_merges_finishes_or_deletes(orch, job_type):
    _, client = orch
    response = _trigger(client, job_type=job_type)
    assert response.status_code == 422 and "automation never merges" in response.json()["detail"]
    assert client.get("/v1/triggers").json() == []


def test_a_settled_job_fires_the_matching_trigger_once(orch):
    connection, client = orch
    trigger_id = _trigger(client, "job.settled", {"type": "noop", "status": "failed"}, project_id=None,
                          params={"from": "$event.job_id", "fixed": 1}, queue_key="$event.project_id").json()["id"]
    source = client.post("/v1/jobs", json={"type": "noop", "target_pillar": "reference", "project_id": "little-meals"}).json()
    assert source["status"] == "failed"  # no pillar in this test: the job settles as failed
    created = [j for j in client.get("/v1/jobs").json() if j["created_by"] == f"trigger:{trigger_id}"]
    assert len(created) == 1
    assert created[0]["type"] == "usage.doctor" and created[0]["project_id"] == "little-meals" and created[0]["queue_key"] == "little-meals"
    assert created[0]["params"] == {"from": source["id"], "fixed": 1}
    listed = client.get("/v1/triggers").json()[0]
    assert listed["last_job_id"] == created[0]["id"] and listed["last_fired_at"]
    client.post("/v1/pump")
    assert len([j for j in client.get("/v1/jobs").json() if j["created_by"] == f"trigger:{trigger_id}"]) == 1  # the triggered job failing does not fire it again


def test_a_trigger_ignores_events_its_filter_does_not_match_and_does_nothing_when_disabled(orch):
    connection, client = orch
    trigger_id = _trigger(client, "job.settled", {"project_id": "little-workshop"}).json()["id"]
    client.post("/v1/jobs", json={"type": "noop", "target_pillar": "reference", "project_id": "little-meals"})
    assert len(client.get("/v1/jobs").json()) == 1
    assert client.patch(f"/v1/triggers/{trigger_id}", json={"enabled": False}).json()["enabled"] is False
    client.post("/v1/jobs", json={"type": "noop", "target_pillar": "reference", "project_id": "little-workshop"})
    assert len(client.get("/v1/jobs").json()) == 2
    assert client.delete(f"/v1/triggers/{trigger_id}").json() == {"removed": trigger_id}
    assert client.delete(f"/v1/triggers/{trigger_id}").status_code == 404


def test_a_chain_of_triggers_stops_at_the_depth_limit(orch):
    connection, client = orch
    _trigger(client, "job.settled", {"type": "a.x"}, job_type="b.x")
    _trigger(client, "job.settled", {"type": "b.x"}, job_type="a.x")
    client.post("/v1/jobs", json={"type": "a.x", "target_pillar": "reference"})
    for _ in range(6):
        client.post("/v1/pump")
    triggered = [j for j in client.get("/v1/jobs").json() if j["created_by"].startswith("trigger:")]
    assert len(triggered) == triggers.MAX_DEPTH


def test_a_triggered_job_is_policy_gated_like_any_other(tmp_path, monkeypatch):
    connection = orchestration_db.connect(tmp_path / "o.db")
    triggers.save(connection, triggers.Trigger(name="t", on=triggers.TriggerOn(event="job.settled"),
                                               job_template=JobTemplate(type="deployment.deploy", target_pillar="deployment", project_id="p")))
    gated = []
    from orchestration import policy_gate
    monkeypatch.setattr(policy_gate, "attach_policy_gate", lambda conn, socket_lookup, job: gated.append(job.type) or job)
    job = Job(type="noop", target_pillar="reference", status=JobStatus.SUCCEEDED)
    orchestration_db.insert_job(connection, job)
    orchestration_db.update_job(connection, job)
    dispatch_ready_jobs(connection, lambda pillar: None)
    assert gated == ["deployment.deploy"]
    connection.close()


# --- REQ-000000344: Events come from Jobs settling and from the pillars that own them ---


def test_a_finished_branch_and_a_finished_release_have_events_of_their_own():
    done = {"status": "succeeded", "type": "version.improvement_finish"}
    assert triggers.event_names("job.settled", done) == ("job.settled", "job.succeeded", "version.branch_finished")
    assert "version.release_finished" in triggers.event_names("job.settled", {"status": "succeeded", "type": "version.release"})
    assert triggers.event_names("job.settled", {"status": "failed", "type": "version.feature_finish"}) == ("job.settled", "job.failed")
    assert triggers.event_names("claude.session_ended", {}) == ("claude.session_ended",)


def test_a_pillar_reports_an_event_and_its_trigger_fires(orch):
    connection, client = orch
    trigger_id = _trigger(client, "claude.session_ended", job_type="usage.ingest_transcripts").json()["id"]
    fired = client.post("/v1/events", json={"name": "claude.session_ended", "project_id": "little-meals", "data": {"session_id": "s1"}}).json()
    assert len(fired["jobs"]) == 1
    job = client.get(f"/v1/jobs/{fired['jobs'][0]}").json()
    assert job["type"] == "usage.ingest_transcripts" and job["created_by"] == f"trigger:{trigger_id}" and job["project_id"] == "little-meals"
    assert client.post("/v1/events", json={"name": "job.succeeded"}).status_code == 422  # Orchestration's own events cannot be faked
    assert client.post("/v1/events", json={"name": "nobody.listens"}).json() == {"jobs": []}


def test_no_event_is_kept_while_there_is_no_trigger(orch):
    connection, client = orch
    client.post("/v1/jobs", json={"type": "noop", "target_pillar": "reference"})
    assert connection.execute("SELECT COUNT(*) FROM trigger_events").fetchone()[0] == 0


def test_the_usage_pillar_reports_a_session_ending_and_a_finding_opening(conn, paths):
    emitted = []
    app = build_usage_app(conn, paths, projects=lambda: [], agent_usage=lambda since: [], emit=lambda name, project, data: emitted.append((name, project, data)))
    client = TestClient(app)
    client.post("/v1/hooks/SessionEnd", json={"session_id": "s1", "cwd": "/x", "reason": "clear"})
    assert emitted == [("claude.session_ended", "unattributed", {"session_id": "s1", "reason": "clear"})]
    db.upsert_session(conn, "big", seen=NOW.isoformat(), tag_project="little-meals")
    db.upsert_request(conn, {"request_id": "r", "session_id": "big", "ts": NOW.isoformat(), "model": "m", "cache_read_tokens": 600_000, "source": "t"})
    app.state.tick()
    app.state.analyze()
    app.state.analyze()
    opened = [e for e in emitted if e[0] == "usage.finding_opened"]
    assert [(e[1], e[2]["detector"]) for e in opened] == [("little-meals", "D1")]  # once, not on every analysis


def test_emitting_an_event_is_best_effort(paths):
    assert events.emit(paths, "claude.session_ended", "p", {}) is False  # nobody to tell: dropped, no error


# --- REQ-000000345: The pillars a finished branch changed are redeployed by one Job ---


def test_changed_files_name_the_pillars_to_redeploy():
    live = ["orchestration", "agent", "mcp", "usage", "dashboard"]
    assert redeploy_plan.changed_pillars(["src/agent/flow.py", "src/mcp_server/tools.py", "docs/x.md", "tests/unit/test_a.py"], live) == ["agent", "mcp"]
    assert redeploy_plan.changed_pillars(["src/cli/main.py", "tools/x.py", "src/build/app.py"], live) == []  # the CLI and a pillar that is off
    assert redeploy_plan.changed_pillars(["src/common/paths.py"], live) == sorted(live)  # shared code: every live pillar


@pytest.fixture
def workshop_repo(tmp_path, monkeypatch):
    repo = tmp_path / "workshop"
    (repo / "src" / "agent").mkdir(parents=True)
    (repo / "src" / "usage").mkdir()
    git = lambda *args: subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()
    git("init", "-b", "dev")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "T")
    (repo / "src" / "agent" / "a.py").write_text("1")
    git("add", "-A")
    git("commit", "-m", "first")
    git("checkout", "-b", "feature/x")
    (repo / "src" / "agent" / "a.py").write_text("2")
    (repo / "src" / "usage" / "u.py").write_text("1")
    (repo / "README.md").write_text("docs")
    git("add", "-A")
    git("commit", "-m", "change")
    git("checkout", "dev")
    git("merge", "--no-ff", "feature/x", "-m", "finish")
    monkeypatch.setenv("LITTLE_WORKSHOP_REPO_DIR", str(repo))
    return repo, git


def test_the_job_redeploys_what_the_merge_commit_changed_in_dependency_order(workshop_repo, paths, tmp_path):
    repo, git = workshop_repo
    connection = deployment_db.connect(tmp_path / "deployment.db")
    created = []
    app = build_deployment_app(paths, connection, live_pillars=lambda: ["orchestration", "agent", "usage", "deployment"],
                               create_job=lambda template: created.append(template) or {"status": "succeeded"})
    explicit = {"job_id": "j1", "params": {"base": "HEAD^1"}}  # the merge commit against its first parent
    result = TestClient(app).post("/v1/jobs/deployment.redeploy_changed_pillars", json=explicit).json()["result"]
    assert result["pillars"] == ["agent", "usage"] and result["commit"] == git("rev-parse", "HEAD")
    assert result["batches"] == [[{"pillar": "agent"}], [{"pillar": "usage"}]]
    for _ in range(200):
        if len(created) == 2:
            break
        import time
        time.sleep(0.02)
    assert sorted((t["type"], t["params"]["pillar"], t["created_by"]) for t in created) == [
        ("deployment.redeploy_pillar", "agent", "job:j1"), ("deployment.redeploy_pillar", "usage", "job:j1")]
    usage_job = next(t for t in created if t["params"]["pillar"] == "usage")
    agent_job = next(t for t in created if t["params"]["pillar"] == "agent")
    assert usage_job["depends_on"] == [agent_job["id"]]

    nothing = TestClient(app).post("/v1/jobs/deployment.redeploy_changed_pillars", json={"job_id": "j2", "params": {"base": "HEAD"}}).json()["result"]
    assert nothing["pillars"] == [] and len(created) == 2
    assert TestClient(app).post("/v1/jobs/deployment.redeploy_changed_pillars", json={"job_id": "j3", "params": {"commit": "nope"}}).json()["outcome"] == "failed"
    connection.close()


def test_without_a_base_each_pillar_is_compared_with_the_commit_it_runs(workshop_repo, paths, tmp_path):
    """A finish here ends in a squash commit and a version-bump commit, not a merge commit: comparing
    HEAD with its parent sees only the bump (2026-10-04). What a pillar runs is what decides."""
    repo, git = workshop_repo
    (repo / "VERSION").write_text("2")
    git("add", "-A")
    git("commit", "-m", "chore: bump version")  # HEAD^1..HEAD touches no pillar
    first, head = git("rev-list", "--max-parents=0", "HEAD"), git("rev-parse", "HEAD")
    connection = deployment_db.connect(tmp_path / "deployment.db")
    deployment_db.save_pillar_deployment(connection, "agent", "/x/agent", first)  # runs the commit before the change
    deployment_db.save_pillar_deployment(connection, "orchestration", "/x/orchestration", head)  # already on HEAD
    created = []
    app = build_deployment_app(paths, connection, live_pillars=lambda: ["orchestration", "agent", "usage"],
                               create_job=lambda template: created.append(template) or {"status": "succeeded"})
    result = TestClient(app).post("/v1/jobs/deployment.redeploy_changed_pillars", json={"job_id": "j1"}).json()["result"]
    # agent: its code changed since the commit it runs. usage: never deployed by this pillar, so it is brought in.
    assert result["pillars"] == ["agent", "usage"] and result["compared_with"] == "the commit each pillar runs"
    connection.close()


def test_a_trigger_can_run_a_job_for_no_project_at_all(orch):
    connection, client = orch
    _trigger(client, "claude.session_ended", job_type="registry.sync_claude_files", project_id="*")
    fired = client.post("/v1/events", json={"name": "claude.session_ended", "project_id": "little-meals"}).json()
    assert client.get(f"/v1/jobs/{fired['jobs'][0]}").json()["project_id"] is None  # every project, not the event's


def test_little_workshops_own_triggers_are_installed_once(orch):
    connection, client = orch
    added = client.post("/v1/triggers/install-defaults").json()
    assert [t["job_template"]["type"] for t in added] == ["deployment.redeploy_changed_pillars", "registry.sync_claude_files", "usage.ingest_transcripts"]
    assert added[0]["on"] == {"event": "version.branch_finished", "filter": {"project_id": "little-workshop"}}
    assert added[1]["job_template"]["params"] == {"write": False}  # it reports; writing is a person's choice
    assert added[1]["job_template"]["project_id"] == "*" and added[2]["job_template"]["project_id"] == "*"  # every project
    assert client.post("/v1/triggers/install-defaults").json() == [] and len(client.get("/v1/triggers").json()) == 3


# --- REQ-000000346: The requirements the test markers name are created and linked by one Job ---


@pytest.fixture
def requirements(tmp_path, paths, monkeypatch):
    project = tmp_path / "little-widget"
    (project / "tests" / "unit").mkdir(parents=True)
    (project / "docs").mkdir()
    (project / "src").mkdir()
    (project / "tests" / "unit" / "test_a.py").write_text(
        "# --- REQ-000000001: Already there ---\n\n# --- REQ-000000002: Written in the register ---\n\n"
        "# --- REQ-000000003: No text anywhere ---\n# --- REQ-000000004: Not EARS ---\n# REQ-000000009 mentioned, not a marker\n")
    (project / "tests" / "unit" / "test_b.ts").write_text("// --- REQ-000000002: Written in the register ---\n")
    (project / "src" / "code.py").write_text("# --- REQ-000000008: not a test file ---\n")
    (project / "docs" / "requirements.md").write_text(
        "| ID | Description | Status |\n| --- | --- | --- |\n"
        "| REQ-000000002 | **Written in the register.** When asked, the widget shall answer. | draft |\n"
        "| REQ-000000004 | **Not EARS.** It works well. | draft |\n")
    connection = requirements_db.connect(tmp_path / "requirements.db")
    suite = TestSuite(id="suite-1", project_id="little-widget", name="Unit tests")
    requirements_db.insert_test_suite(connection, suite)
    requirements_db.insert_requirement(connection, Requirement(id="req-1", project_id="little-widget", key="REQ-000000001", title="Already there"))
    from requirements import app as requirements_app
    monkeypatch.setattr(requirements_app, "resolve_project_path", lambda paths, project_id: project)
    yield TestClient(build_requirements_app(connection, paths)), connection, project
    connection.close()


def test_markers_are_read_from_test_files_only(requirements):
    _, _, project = requirements
    found = markers.scan(project)
    assert sorted(found) == [f"REQ-00000000{n}" for n in (1, 2, 3, 4)]
    assert found["REQ-000000002"] == {"title": "Written in the register", "files": ["tests/unit/test_a.py", "tests/unit/test_b.ts"]}
    assert markers.register(project)["REQ-000000002"] == {"title": "Written in the register", "description": "When asked, the widget shall answer."}


def test_the_job_creates_what_the_register_describes_links_everything_and_reports_the_rest(requirements):
    client, connection, _ = requirements
    body = {"job_id": "j1", "project_id": "little-widget"}
    dry = client.post("/v1/jobs/requirements.sync_markers", json={**body, "params": {"dry_run": True}}).json()["result"]
    assert dry["created"] == ["REQ-000000002"] and len(requirements_db.list_requirements(connection, project_id="little-widget")) == 1

    result = client.post("/v1/jobs/requirements.sync_markers", json=body).json()["result"]
    assert result["created"] == ["REQ-000000002"] and result["linked"] == ["REQ-000000001", "REQ-000000002"]
    assert [m["key"] for m in result["missing_text"]] == ["REQ-000000003", "REQ-000000004"] and result["suite"] == "Unit tests" and result["markers"] == 4
    created = next(r for r in requirements_db.list_requirements(connection, project_id="little-widget") if r.key == "REQ-000000002")
    assert created.description == "When asked, the widget shall answer." and created.status.value == "draft"
    assert requirements_db.list_suites_for_requirement(connection, created.id) == ["suite-1"]

    again = client.post("/v1/jobs/requirements.sync_markers", json=body).json()["result"]
    assert again["created"] == [] and again["linked"] == [] and again["in_place"] == ["REQ-000000001", "REQ-000000002"]
    assert client.post("/v1/jobs/requirements.sync_markers", json={**body, "params": {"suite_id": "no-such-suite"}}).json()["outcome"] == "failed"


# --- REQ-000000347: Candidates, triggers and the new Jobs are reached through lws, the MCP server and the Dashboard ---


def test_lws_usage_candidates_trigger_and_the_new_job_commands(capsys, monkeypatch):
    monkeypatch.setattr(cli_main.client, "usage_candidates", lambda p, **kw: [{"signature": "x", "asked": kw}])
    assert cli_main.main(["usage", "candidates", "--state", "all"]) == 0
    assert json.loads(capsys.readouterr().out) == [{"signature": "x", "asked": {"state": "all"}}]

    calls = []
    monkeypatch.setattr(cli_main.client, "trigger_request", lambda p, method, path="", json=None: calls.append((method, path, json)) or {"ok": True})
    assert cli_main.main(["trigger", "list"]) == 0
    assert cli_main.main(["trigger", "add", "after finish", "--event", "version.branch_finished", "--filter", '{"project_id": "little-meals"}',
                          "--type", "requirements.sync_markers", "--target-pillar", "requirements", "--params", '{"dry_run": true}']) == 0
    assert cli_main.main(["trigger", "disable", "t1"]) == 0 and cli_main.main(["trigger", "remove", "t1"]) == 0
    assert cli_main.main(["trigger", "install-defaults"]) == 0
    assert cli_main.main(["trigger", "add", "bad", "--event", "e", "--type", "t", "--target-pillar", "p", "--params", "{not json"]) == 2
    assert calls[0] == ("GET", "", None)
    assert calls[1] == ("POST", "", {"name": "after finish", "on": {"event": "version.branch_finished", "filter": {"project_id": "little-meals"}},
                                     "job_template": {"type": "requirements.sync_markers", "target_pillar": "requirements", "project_id": None,
                                                      "params": {"dry_run": True}, "queue_key": None, "depends_on": []}})
    assert calls[2:] == [("PATCH", "/t1", {"enabled": False}), ("DELETE", "/t1", None), ("POST", "/install-defaults", None)]

    jobs = []
    monkeypatch.setattr(cli_main.client, "create_job", lambda p, **kw: jobs.append(kw) or {"status": "succeeded"})
    assert cli_main.main(["usage", "mine-automation"]) == 0
    assert cli_main.main(["deployment", "redeploy-changed-pillars", "--commit", "abc"]) == 0
    assert cli_main.main(["requirements", "sync-markers", "--project-id", "little-meals", "--dry-run"]) == 0
    assert [(j["type"], j["params"]) for j in jobs] == [("usage.mine_automation", {}), ("deployment.redeploy_changed_pillars", {"commit": "abc"}),
                                                        ("requirements.sync_markers", {"dry_run": True})]


def test_the_new_routes_are_allowed_asked_or_little_workshops_own_and_named_in_the_skill():
    project = claude_templates.settings("little-widget")["permissions"]
    mcp = claude_templates.MCP
    assert {f"{mcp}workshop_usage_candidates", f"{mcp}workshop_trigger_list", f"{mcp}workshop_requirements_sync_markers"} <= set(project["allow"])
    assert {f"{mcp}workshop_trigger_add", f"{mcp}workshop_trigger_remove", f"{mcp}workshop_deployment_redeploy_changed_pillars"} <= set(project["ask"])
    assert "Bash(lws requirements sync-markers --project-id little-widget *)" in project["allow"]
    assert f"{mcp}workshop_deployment_redeploy_changed_pillars" in claude_templates.settings("little-workshop", platform=True)["permissions"]["allow"]
    skill = claude_templates.lifecycle_skill_md("little-widget")
    assert "`workshop_requirements_sync_markers`: one call" in skill
    assert "workshop_deployment_redeploy_changed_pillars" in claude_templates.lifecycle_skill_md("little-workshop", platform=True)
