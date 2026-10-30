"""An `agent.run_task` run never outlives its worker: an unknown intent is refused before a run
exists, a crash fails the run and its Job, and a running task can be stopped whether its worker
is alive or gone."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent import db, procs
from agent import flow as flow_module
from agent.app import build_app
from agent.flow import AgentFlow, AgentFlowError
from common.shell import CommandResult
from test_agent_app import WritingClaudeRunner, _wait_for_run, make_client, make_repo, CFG, _git


@pytest.fixture
def paths(tmp_path):
    from common.paths import Paths

    p = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    p.ensure_layout()
    return p


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "agent.db")
    yield connection
    connection.close()


@pytest.fixture
def completions(monkeypatch):
    """What the pillar reported back to Orchestration: [(job_id, succeeded, error)]."""
    calls = []
    monkeypatch.setattr("agent.app._complete_orchestration_job",
                        lambda paths, job_id, *, succeeded, result, error: calls.append((job_id, succeeded, error)))
    return calls


@pytest.fixture
def incidents(monkeypatch):
    calls = []
    monkeypatch.setattr("agent.app.report_crash", lambda paths, **kw: calls.append(kw))
    return calls


def _start(client, job_id, **params):
    body = {"job_id": job_id, "project_id": "proj", "params": {"title": "Do a thing", **params}}
    return client.post("/v1/jobs/agent.run_task", json=body).json()


def _wait(predicate, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("condition never became true")


# --- REQ-000000362: Usage tasks use an intent the agent knows ---


def test_the_usage_pillars_tasks_use_an_intent_with_a_prompt():
    from usage import mining, optimize

    candidate = {
        "signature": "mcp:workshop_x(a)", "class": "deterministic", "score": 5, "checks": {}, "occurrences": 3,
        "sessions": 2, "proposed_form": "one tool", "estimate": {}, "schedule": {}, "examples": [],
    }
    automate = mining.automate_job(candidate, "little-workshop")["params"]["intent"]
    optimize_intent = optimize.task_job("little-meals", [])["params"]["intent"]
    assert automate == optimize_intent == "task"
    assert {automate, optimize_intent} <= set(flow_module.INTENTS)


def test_every_known_intent_has_its_prompt_template():
    from agent import templates_util

    for intent in flow_module.INTENTS:
        assert templates_util.read_prompt(f"{intent}.md").strip()


# --- REQ-000000363: An unknown intent is refused before a run starts ---


def test_an_unknown_intent_fails_the_job_at_once_and_creates_no_run(paths, conn, tmp_path, monkeypatch):
    client, _repo = make_client(paths, conn, tmp_path, monkeypatch)
    answer = _start(client, "job-1", intent="improvement")
    assert answer["outcome"] == "failed"
    assert "unknown intent 'improvement'" in answer["error"] and "bug, docs, milestone, task" in answer["error"]
    assert client.get("/v1/runs/proj").json() == []


def test_the_flow_itself_refuses_an_unknown_intent_before_recording_a_run(paths, conn, tmp_path):
    repo = make_repo(tmp_path)
    flow = AgentFlow(paths=paths, runner=WritingClaudeRunner(), remote_control=False)
    with pytest.raises(AgentFlowError, match="unknown intent 'improvement'"):
        flow.run_task(conn, "proj", repo, None, {"title": "t", "intent": "improvement"}, run_id="r1")
    assert db.get_run(conn, "r1") is None


# --- REQ-000000364: A crashed task fails its run and its Job ---


def test_a_crash_inside_the_run_fails_the_run_reports_the_job_failed_and_records_an_incident(
    paths, conn, tmp_path, monkeypatch, completions, incidents,
):
    client, _repo = make_client(paths, conn, tmp_path, monkeypatch)

    def crash(self, **kwargs):
        raise FileNotFoundError("templates/prompts/task.md")

    monkeypatch.setattr(AgentFlow, "_render_prompt", crash)
    run_id = _start(client, "job-1", intent="task")["result"]["run_id"]
    run = _wait_for_run(client, "proj", run_id)
    assert run["status"] == "failed" and "FileNotFoundError" in run["summary"]
    _wait(lambda: completions)
    assert completions == [("job-1", False, run["summary"])]
    (incident,) = incidents
    assert incident["pillar"] == "agent" and isinstance(incident["exc"], FileNotFoundError) and "job-1" in incident["context"]


def test_a_crash_before_the_run_exists_still_fails_the_job(paths, conn, tmp_path, monkeypatch, completions, incidents):
    client, _repo = make_client(paths, conn, tmp_path, monkeypatch)

    def crash(self, *args, **kwargs):
        raise RuntimeError("no standards")

    monkeypatch.setattr(AgentFlow, "_standards_copy", crash)
    _start(client, "job-1", intent="task")
    _wait(lambda: completions)
    assert completions[0][:2] == ("job-1", False) and "RuntimeError: no standards" in completions[0][2]
    assert client.get("/v1/runs/proj").json() == [] and len(incidents) == 1


# --- REQ-000000365: A running task can be stopped ---


def test_a_run_whose_worker_is_gone_is_stopped_at_once(paths, conn, tmp_path, monkeypatch):
    client, _repo = make_client(paths, conn, tmp_path, monkeypatch)
    db.create_run(conn, "r1", job_id="job-9", project_id="proj", intent="task", review_mode="manual", title="t", source={})
    answer = client.post("/v1/jobs/job-9/stop", json={"type": "agent.run_task"}).json()
    assert answer == {"stopped": True}
    run = db.get_run(conn, "r1")
    assert run["status"] == "stopped" and "no longer running" in run["summary"]


def test_a_task_job_with_neither_run_nor_worker_is_stopped(paths, conn, tmp_path, monkeypatch):
    client, _repo = make_client(paths, conn, tmp_path, monkeypatch)
    assert client.post("/v1/jobs/job-9/stop", json={"type": "agent.run_task"}).json() == {"stopped": True}
    # A job the pillar knows nothing about, of no type it runs, is still refused.
    assert client.post("/v1/jobs/other/stop").json() == {"stopped": False, "reason": "not stoppable"}


class BlockingClaudeRunner(WritingClaudeRunner):
    """Claude "runs" until the stop kills it, then exits as a terminated process does."""

    def __init__(self):
        super().__init__()
        self.started = threading.Event()
        self.killed = threading.Event()

    def run(self, argv, timeout=None, cwd=None):
        if argv and argv[0] == "claude":
            self.calls.append(argv)
            self.started.set()
            self.killed.wait(5)
            return CommandResult(-15, "", "terminated")
        return super().run(argv, timeout=timeout, cwd=cwd)


def test_a_live_run_has_its_claude_terminated_and_ends_stopped(paths, conn, tmp_path, monkeypatch, completions):
    repo = make_repo(tmp_path)
    _git(["checkout", "dev"], repo)
    monkeypatch.setattr(flow_module.version_client, "get_config", lambda p, project_id, **kw: CFG)
    monkeypatch.setattr("agent.app.resolve_project", lambda p, project_id, **kw: {"name": project_id, "path": str(repo), "repo_url": None})
    runner = BlockingClaudeRunner()
    killed_sessions = []

    def kill(session_id):
        killed_sessions.append(session_id)
        runner.killed.set()
        return [4242]

    flow = AgentFlow(paths=paths, runner=runner, remote_control=False)
    client = TestClient(build_app(paths, conn, flow=flow, kill_session=kill))
    run_id = _start(client, "job-1", intent="task", branching={"mode": "existing", "branch": "dev"})["result"]["run_id"]
    assert runner.started.wait(5)
    session_id = db.get_run(conn, run_id)["session_id"]
    assert session_id and f"--session-id" in runner.calls[-1] and session_id in runner.calls[-1]

    assert client.post("/v1/jobs/job-1/stop", json={"type": "agent.run_task"}).json() == {"stopped": True}
    assert killed_sessions == [session_id]
    _wait(lambda: completions)
    run = db.get_run(conn, run_id)
    assert run["status"] == "stopped" and run["summary"] == "stopped by user"


def test_a_stop_asked_before_claude_starts_means_claude_never_starts(paths, conn, tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    _git(["checkout", "dev"], repo)
    monkeypatch.setattr(flow_module.version_client, "get_config", lambda p, project_id, **kw: CFG)
    runner = WritingClaudeRunner()
    flow = AgentFlow(paths=paths, runner=runner, remote_control=False)
    result = flow.run_task(conn, "proj", repo, None, {"title": "t", "intent": "task", "branching": {"mode": "existing", "branch": "dev"}},
                           run_id="r1", should_stop=lambda: True)
    assert result.status == db.FAILED and result.error == "stopped by user"
    assert not any(call and call[0] == "claude" for call in runner.calls)


def test_the_processes_of_a_session_are_its_claude_and_everything_under_it(tmp_path):
    proc = tmp_path / "proc"

    def process(pid, ppid, *argv):
        (proc / str(pid)).mkdir(parents=True)
        (proc / str(pid) / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")
        (proc / str(pid) / "stat").write_text(f"{pid} (x y) S {ppid} 1 1 0", encoding="utf-8")

    process(10, 1, "node", "claude", "-p", "prompt", "--session-id", "abc-123")
    process(11, 10, "bash", "-c", "pytest")
    process(12, 11, "python3", "-m", "pytest")
    process(20, 1, "node", "claude", "--session-id", "other")
    process(21, 20, "bash")
    (proc / "self").mkdir()  # not a process directory
    assert procs.session_processes("abc-123", proc_root=proc) == [10, 11, 12]
    sent = []
    assert procs.terminate_session("abc-123", proc_root=proc, kill=lambda pid, sig: sent.append(pid)) == [12, 11, 10]
    assert sent == [12, 11, 10]
    assert procs.session_processes("missing", proc_root=proc) == []
