"""The Agentic Development job types report the steps `common/step_specs/agent.py` declares."""

import pytest

from agent import db
from agent.flow import AgentFlow, AgentFlowError
from agent.session_flow import SessionError, SessionFlow
from common.job_steps import StepReporter, reporting
from common.step_specs.agent import STEPS
from test_agent_flow import CFG, FakeCIGitHub, WritingClaudeRunner, make_repo, patch_version_client, _git  # noqa: F401
from test_agent_session_flow import FakeRunner
from test_steps_registry import Recorder
import agent.session_flow as session_flow_module
from common.paths import Paths


# --- REQ-000000310: Every job has a checklist of its actions ---

@pytest.fixture
def recorder():
    rec = Recorder()
    with reporting(StepReporter(None, "j1", call=rec)):
        yield rec


@pytest.fixture
def paths(tmp_path):
    p = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    p.ensure_layout()
    return p


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "agent.db")
    yield connection
    connection.close()


def test_run_task_reports_every_declared_step(recorder, paths, conn, tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    _git(["checkout", "dev"], repo)
    patch_version_client(monkeypatch, repo)
    flow = AgentFlow(paths=paths, runner=WritingClaudeRunner(), remote_control=False)
    flow.run_task(conn, "little-meals", repo, None, {"title": "fix it", "intent": "bug", "branching": {"mode": "new"}})
    assert recorder.keys() == [s.key for s in STEPS["agent.run_task"]]
    assert recorder.final("run_claude")[0] == "succeeded"


def test_run_task_reports_a_dirty_checkout_as_a_failed_step(recorder, paths, conn, tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    (repo / "dirty.txt").write_text("x")
    patch_version_client(monkeypatch, repo)
    flow = AgentFlow(paths=paths, runner=WritingClaudeRunner(), remote_control=False)
    result = flow.run_task(conn, "little-meals", repo, None, {"title": "t", "intent": "bug"})
    assert result.status == db.FAILED
    state, detail = recorder.final("check_checkout")
    assert state == "failed" and "uncommitted changes" in detail


def test_confirm_incident_declares_the_run_task_steps():
    assert STEPS["agent.confirm_incident"] == STEPS["agent.run_task"]


def test_ci_check_reports_every_declared_step(recorder, paths, conn, monkeypatch):
    import agent.flow as flow_module

    monkeypatch.setattr(flow_module.requirements_client, "run_job", lambda *a, **kw: {"ran": 1, "results": [{"verdict": "passed"}]})
    flow = AgentFlow(paths=paths, github=FakeCIGitHub())
    flow.run_ci_check(conn, "p1", "git@github.com:o/n.git", {"pr_number": 5, "suite_id": "s1"})
    assert recorder.keys() == [s.key for s in STEPS["agent.ci_check"]]


def test_ci_check_reports_an_unreadable_pull_request_as_failed(recorder, paths, conn):
    flow = AgentFlow(paths=paths, github=FakeCIGitHub(raise_on_pr=True))
    with pytest.raises(AgentFlowError):
        flow.run_ci_check(conn, "p1", "git@github.com:o/n.git", {"pr_number": 5, "suite_id": "s1"})
    state, detail = recorder.final("resolve_pr")
    assert state == "failed" and "no such PR" in detail


def test_session_start_reports_every_declared_step(recorder, paths, tmp_path, monkeypatch):
    monkeypatch.setattr(session_flow_module, "is_trusted", lambda project_dir: True)
    SessionFlow(paths, runner=FakeRunner()).start("little-widget", tmp_path)
    assert recorder.keys() == [s.key for s in STEPS["agent.session_start"]]


def test_session_start_reports_an_untrusted_folder_as_a_failed_check(recorder, paths, tmp_path, monkeypatch):
    monkeypatch.setattr(session_flow_module, "is_trusted", lambda project_dir: False)
    with pytest.raises(SessionError):
        SessionFlow(paths, runner=FakeRunner()).start("little-widget", tmp_path)
    state, detail = recorder.final("check_session")
    assert state == "failed" and "not trusted" in detail


from test_agent_doc_update import FakeSession, _comments, _flow, repo, worktrees  # noqa: E402,F401


def test_update_docs_reports_every_declared_step(recorder, paths, repo, worktrees):
    conn = db.connect(paths.pillar_db_path("agent"))
    _comments(repo)
    flow = _flow(paths, FakeSession(signal=True), [])
    claimed = flow.claim(conn, "j1", "proj", repo, "dev", remote_control=False)
    flow.launch(conn, "j1", "proj", repo, "dev", claimed, remote_control=False)
    assert recorder.keys() == [s.key for s in STEPS["agent.update_docs"]]
    assert recorder.final("finalize") == ("succeeded", "2 of 2 comment(s) done")


def test_update_docs_reports_a_refused_launch_as_a_failed_step(recorder, paths, repo, worktrees):
    from common.shell import CommandResult

    class Refuses(FakeSession):
        def run(self, argv, timeout=None, cwd=None):
            if argv[0] == "systemd-run":
                return CommandResult(1, "", "no user session")
            return super().run(argv, timeout, cwd)

    conn = db.connect(paths.pillar_db_path("agent"))
    _comments(repo)
    flow = _flow(paths, Refuses(), [])
    flow.launch(conn, "j1", "proj", repo, "dev", flow.claim(conn, "j1", "proj", repo, "dev", False), False)
    state, detail = recorder.final("start_session")
    assert state == "failed" and "no user session" in detail
