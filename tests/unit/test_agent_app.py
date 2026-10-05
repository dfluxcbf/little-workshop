import json
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent import db
from agent.app import build_app
from agent.flow import AgentFlow
from agent.launch import JOB_QUERY, JOB_QUERY_CHECKOUT, launch_for
from agent.query import CHECKOUT_QUERY_TIMEOUT_SECONDS, query_dir
from common.paths import Paths
from common.registry_client import ProjectResolutionError
from common.shell import CommandResult


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def make_repo(tmp_path: Path) -> Path:
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-b", "master", str(bare)], check=True, capture_output=True)
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-b", "master"], repo)
    _git(["config", "user.email", "t@example.com"], repo)
    _git(["config", "user.name", "T"], repo)
    _git(["remote", "add", "origin", str(bare)], repo)
    (repo / "README.md").write_text("hi\n", encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-m", "first"], repo)
    _git(["push", "-u", "origin", "master"], repo)
    _git(["checkout", "-b", "dev"], repo)
    _git(["push", "-u", "origin", "dev"], repo)
    return repo


CFG = {
    "production_branch": "master", "integration_branch": "dev",
    "branch_types": {"feature": {"prefix": "feature", "base": "integration"}, "improvement": {"prefix": "improvement", "base": "integration"}},
}


class WritingClaudeRunner:
    def __init__(self, status="done"):
        self.status = status
        self.calls = []

    def run(self, argv, timeout=None, cwd=None):
        self.calls.append(argv)
        if argv and argv[0] == "claude":
            prompt = argv[2]
            for line in prompt.splitlines():
                if line.strip().startswith("/") and line.strip().endswith(".json"):
                    path = Path(line.strip())
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(json.dumps({
                        "status": self.status, "background": "bg", "goal": "goal",
                        "technical_decisions": "td", "executed_tasks": "et", "test_results": "ok",
                    }))
            return CommandResult(0, json.dumps({"result": f"summary\nAGENT_STATUS: {self.status}", "is_error": False}), "")
        result = subprocess.run(argv, cwd=str(cwd) if cwd else None, capture_output=True, text=True)
        return CommandResult(result.returncode, result.stdout, result.stderr)


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


def make_client(paths, conn, tmp_path, monkeypatch, *, status="done"):
    repo = make_repo(tmp_path)
    _git(["checkout", "dev"], repo)

    import agent.flow as flow_module

    monkeypatch.setattr(flow_module.version_client, "get_config", lambda p, project_id, **kw: CFG)

    def fake_run_job(p, project_id, job_type, params, **kw):
        if job_type.endswith("_start"):
            branch_type = job_type.removeprefix("version.").removesuffix("_start")
            branch = f"{branch_type}/{params['name']}"
            _git(["checkout", "-b", branch], repo)
            return {"branch": branch}
        return {}

    monkeypatch.setattr(flow_module.version_client, "run_job", fake_run_job)

    def fake_resolve_project(paths_, project_id, **kw):
        return {"name": project_id, "path": str(repo), "repo_url": None, "status": "active"}

    monkeypatch.setattr("agent.app.resolve_project", fake_resolve_project)

    flow = AgentFlow(paths=paths, runner=WritingClaudeRunner(status=status), remote_control=False)
    app = build_app(paths, conn, flow=flow)
    return TestClient(app), repo


def _wait_for_run(client, project_id, run_id, *, timeout=5.0):
    """`agent.run_task` dispatches to a background thread and returns
    `accepted` immediately (it can run for up to an hour for real) — tests
    poll the run row the same way a real caller would."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        response = client.get(f"/v1/runs/{project_id}/{run_id}")
        if response.status_code == 200 and response.json()["status"] != "running":
            return response.json()
        time.sleep(0.02)
    raise AssertionError(f"run {run_id} never left 'running' within {timeout}s")


def test_health(paths, conn, tmp_path, monkeypatch):
    client, _ = make_client(paths, conn, tmp_path, monkeypatch)
    assert client.get("/v1/health").json()["ok"] is True


def test_run_task_job_is_accepted_and_completes_in_the_background(paths, conn, tmp_path, monkeypatch):
    client, _ = make_client(paths, conn, tmp_path, monkeypatch)
    response = client.post(
        "/v1/jobs/agent.run_task",
        json={"job_id": "j1", "project_id": "little-meals", "params": {"title": "fix it", "branching": {"mode": "new", "branch_type": "improvement"}}},
    )
    body = response.json()
    assert body["outcome"] == "accepted", body
    run_id = body["result"]["run_id"]

    run = _wait_for_run(client, "little-meals", run_id)
    assert run["status"] == "awaiting_finish"

    listed = client.get("/v1/runs/little-meals").json()
    assert len(listed) == 1


def test_run_task_missing_project_id_fails_cleanly(paths, conn, tmp_path, monkeypatch):
    client, _ = make_client(paths, conn, tmp_path, monkeypatch)
    response = client.post("/v1/jobs/agent.run_task", json={"job_id": "j1", "params": {}})
    assert response.json()["outcome"] == "failed"


def test_finish_task_job(paths, conn, tmp_path, monkeypatch):
    client, _ = make_client(paths, conn, tmp_path, monkeypatch, status="done")
    run_response = client.post(
        "/v1/jobs/agent.run_task",
        json={"job_id": "j1", "project_id": "little-meals", "params": {"title": "fix it", "review_mode": "manual", "branching": {"mode": "new", "branch_type": "improvement"}}},
    )
    run_id = run_response.json()["result"]["run_id"]
    _wait_for_run(client, "little-meals", run_id)

    finish_response = client.post("/v1/jobs/agent.finish_task", json={"job_id": "j2", "params": {"run_id": run_id}})
    assert finish_response.json()["outcome"] == "succeeded"

    run = client.get(f"/v1/runs/little-meals/{run_id}").json()
    assert run["status"] == "finished"


def test_skip_task_job(paths, conn, tmp_path, monkeypatch):
    client, _ = make_client(paths, conn, tmp_path, monkeypatch)
    run_response = client.post(
        "/v1/jobs/agent.run_task",
        json={"job_id": "j1", "project_id": "little-meals", "params": {"title": "fix it", "branching": {"mode": "new", "branch_type": "improvement"}}},
    )
    run_id = run_response.json()["result"]["run_id"]
    _wait_for_run(client, "little-meals", run_id)

    skip_response = client.post("/v1/jobs/agent.skip_task", json={"job_id": "j2", "params": {"run_id": run_id}})
    assert skip_response.json()["outcome"] == "succeeded"


def test_get_run_not_found(paths, conn, tmp_path, monkeypatch):
    client, _ = make_client(paths, conn, tmp_path, monkeypatch)
    response = client.get("/v1/runs/little-meals/nonexistent")
    assert response.status_code == 404


def test_complete_orchestration_job_is_a_noop_when_orchestration_unavailable(paths):
    from agent.app import _complete_orchestration_job

    # No Core running at all -- resolve_pillar_socket returns None -- must
    # not raise, matching every other best-effort notification path.
    _complete_orchestration_job(paths, "job-1", succeeded=True, result={"x": 1}, error=None)


def test_complete_orchestration_job_posts_to_the_callback_when_reachable(paths, tmp_path, monkeypatch):
    import httpx

    import agent.app as app_module

    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(app_module, "resolve_pillar_socket", lambda paths, pillar, **kw: tmp_path / "orch.sock")
    monkeypatch.setattr(
        app_module, "client_for_socket",
        lambda socket_path, *, timeout=5.0: httpx.Client(transport=httpx.MockTransport(handler), base_url="http://pillar"),
    )

    app_module._complete_orchestration_job(paths, "job-1", succeeded=True, result={"x": 1}, error=None)
    assert len(calls) == 1
    assert calls[0].url.path == "/v1/jobs/job-1/complete"


def test_complete_orchestration_job_swallows_connection_errors(paths, tmp_path, monkeypatch):
    import httpx

    import agent.app as app_module

    def handler(request):
        raise httpx.ConnectError("refused", request=request)

    monkeypatch.setattr(app_module, "resolve_pillar_socket", lambda paths, pillar, **kw: tmp_path / "orch.sock")
    monkeypatch.setattr(
        app_module, "client_for_socket",
        lambda socket_path, *, timeout=5.0: httpx.Client(transport=httpx.MockTransport(handler), base_url="http://pillar"),
    )
    app_module._complete_orchestration_job(paths, "job-1", succeeded=False, result=None, error="boom")  # must not raise


def test_run_task_job_fails_cleanly_when_project_resolution_fails(paths, conn, tmp_path, monkeypatch):
    from common.registry_client import ProjectResolutionError

    client, _ = make_client(paths, conn, tmp_path, monkeypatch)

    def raising_resolve(paths_, project_id, **kw):
        raise ProjectResolutionError(f"no such project {project_id!r}")

    monkeypatch.setattr("agent.app.resolve_project", raising_resolve)
    response = client.post(
        "/v1/jobs/agent.run_task", json={"job_id": "j1", "project_id": "ghost", "params": {"title": "t"}},
    )
    assert response.json()["outcome"] == "failed"


def test_run_task_worker_failure_reports_failed_status(paths, conn, tmp_path, monkeypatch):
    client, _ = make_client(paths, conn, tmp_path, monkeypatch)
    # A dirty checkout (never committed) makes flow.run_task fail before
    # ever invoking claude -- the app-level worker branch that reports
    # `succeeded=False` back to Orchestration.
    response = client.post(
        "/v1/jobs/agent.run_task",
        json={"job_id": "j1", "project_id": "little-meals", "params": {"title": "t", "branching": {"mode": "existing", "branch": "ghost-branch"}}},
    )
    run_id = response.json()["result"]["run_id"]
    run = _wait_for_run(client, "little-meals", run_id)
    assert run["status"] == "failed"


def test_finish_task_job_missing_run_id_fails_cleanly(paths, conn, tmp_path, monkeypatch):
    client, _ = make_client(paths, conn, tmp_path, monkeypatch)
    response = client.post("/v1/jobs/agent.finish_task", json={"job_id": "j2", "params": {}})
    assert response.json()["outcome"] == "failed"
    assert "run_id" in response.json()["error"]


def test_finish_task_job_run_not_found_fails_cleanly(paths, conn, tmp_path, monkeypatch):
    client, _ = make_client(paths, conn, tmp_path, monkeypatch)
    response = client.post("/v1/jobs/agent.finish_task", json={"job_id": "j2", "params": {"run_id": "nonexistent"}})
    assert response.json()["outcome"] == "failed"
    assert "no such run" in response.json()["error"]


def test_finish_task_job_project_resolution_failure(paths, conn, tmp_path, monkeypatch):
    from common.registry_client import ProjectResolutionError

    client, repo = make_client(paths, conn, tmp_path, monkeypatch)
    response = client.post(
        "/v1/jobs/agent.run_task",
        json={"job_id": "j1", "project_id": "little-meals", "params": {"title": "fix it", "branching": {"mode": "new", "branch_type": "improvement"}}},
    )
    run_id = response.json()["result"]["run_id"]
    _wait_for_run(client, "little-meals", run_id)

    def raising_resolve(paths_, project_id, **kw):
        raise ProjectResolutionError("gone")

    monkeypatch.setattr("agent.app.resolve_project", raising_resolve)
    finish_response = client.post("/v1/jobs/agent.finish_task", json={"job_id": "j2", "params": {"run_id": run_id}})
    assert finish_response.json()["outcome"] == "failed"


def test_finish_task_job_agent_flow_error_is_reported_as_failed(paths, conn, tmp_path, monkeypatch):
    from agent.app import build_app
    from agent.flow import AgentFlow, AgentFlowError
    from fastapi.testclient import TestClient

    repo = make_repo(tmp_path)

    class RaisingFlow(AgentFlow):
        def finish_task(self, conn, run_id, *, project_dir):
            raise AgentFlowError("cannot finish")

    monkeypatch.setattr(
        "agent.app.resolve_project",
        lambda paths_, project_id, **kw: {"name": project_id, "path": str(repo), "repo_url": None, "status": "active"},
    )
    run_id = db.new_run_id()
    db.create_run(conn, run_id, job_id=None, project_id="little-meals", intent="task", review_mode="manual", title="t", source={})
    db.update_run(conn, run_id, status=db.AWAITING_FINISH, branch="feature/x", branch_type="feature")

    app = build_app(paths, conn, flow=RaisingFlow(paths=paths))
    client = TestClient(app)
    response = client.post("/v1/jobs/agent.finish_task", json={"job_id": "j3", "params": {"run_id": run_id}})
    assert response.json()["outcome"] == "failed"
    assert "cannot finish" in response.json()["error"]


def test_skip_task_job_missing_run_id_fails_cleanly(paths, conn, tmp_path, monkeypatch):
    client, _ = make_client(paths, conn, tmp_path, monkeypatch)
    response = client.post("/v1/jobs/agent.skip_task", json={"job_id": "j2", "params": {}})
    assert response.json()["outcome"] == "failed"
    assert "run_id" in response.json()["error"]


def test_skip_task_job_agent_flow_error_is_reported_as_failed(paths, conn, tmp_path, monkeypatch):
    from agent.app import build_app
    from agent.flow import AgentFlow, AgentFlowError
    from fastapi.testclient import TestClient

    class RaisingSkipFlow(AgentFlow):
        def skip_task(self, conn, run_id):
            raise AgentFlowError("cannot skip")

    app = build_app(paths, conn, flow=RaisingSkipFlow(paths=paths))
    client = TestClient(app)
    response = client.post("/v1/jobs/agent.skip_task", json={"job_id": "j2", "params": {"run_id": "whatever"}})
    assert response.json()["outcome"] == "failed"
    assert "cannot skip" in response.json()["error"]


class FakeQueryRunner:
    def __init__(self, *, response_text="ok", is_error=False):
        self.response_text = response_text
        self.is_error = is_error
        self.calls = []

    def run(self, argv, timeout=None, cwd=None):
        self.calls.append({"argv": argv, "cwd": cwd, "timeout": timeout})
        payload = {"result": self.response_text, "is_error": self.is_error}
        return CommandResult(0, json.dumps(payload), "")


def test_agent_query_returns_claudes_text(paths, conn):
    fake = FakeQueryRunner(response_text="EARS_OK: WHEN X, the system shall Y.")
    app = build_app(paths, conn, flow=AgentFlow(paths=paths, runner=fake))
    client = TestClient(app)

    response = client.post("/v1/jobs/agent.query", json={"job_id": "j1", "params": {"prompt": "refine this"}})
    assert response.json()["outcome"] == "succeeded"
    assert response.json()["result"]["text"] == "EARS_OK: WHEN X, the system shall Y."
    assert fake.calls[0]["cwd"] is not None  # runs in a scratch dir, never the caller's cwd


def test_agent_query_requires_a_prompt(paths, conn):
    app = build_app(paths, conn, flow=AgentFlow(paths=paths, runner=FakeQueryRunner()))
    client = TestClient(app)

    response = client.post("/v1/jobs/agent.query", json={"job_id": "j1", "params": {}})
    assert response.json()["outcome"] == "failed"
    assert "prompt" in response.json()["error"]


def test_agent_query_reports_failed_when_claude_reports_is_error(paths, conn):
    fake = FakeQueryRunner(response_text="couldn't do that", is_error=True)
    app = build_app(paths, conn, flow=AgentFlow(paths=paths, runner=fake))
    client = TestClient(app)

    response = client.post("/v1/jobs/agent.query", json={"job_id": "j1", "params": {"prompt": "x"}})
    assert response.json()["outcome"] == "failed"
    assert response.json()["error"] == "couldn't do that"


def test_agent_query_never_creates_a_run_record(paths, conn):
    """Distinct from agent.run_task: no branch, no PR, no run history —
    this call has no side effects on this pillar's own database at all."""
    fake = FakeQueryRunner(response_text="fine")
    app = build_app(paths, conn, flow=AgentFlow(paths=paths, runner=fake))
    client = TestClient(app)

    client.post("/v1/jobs/agent.query", json={"job_id": "j1", "params": {"prompt": "x"}})
    assert db.list_runs(conn, "any-project") == []


# --- REQ-000000397: A query can read a project's checkout and change nothing ---


def test_a_query_that_reads_the_checkout_runs_in_it_with_only_the_tools_that_read(paths, conn, tmp_path, monkeypatch):
    checkout = tmp_path / "little-meals"
    checkout.mkdir()
    monkeypatch.setattr("agent.app.resolve_project", lambda paths_, project_id, **kw: {"name": project_id, "path": str(checkout)})
    fake = FakeQueryRunner(response_text="# Refined")
    client = TestClient(build_app(paths, conn, flow=AgentFlow(paths=paths, runner=fake)))

    answer = client.post("/v1/jobs/agent.query", json={"job_id": "j1", "project_id": "little-meals",
                                                       "params": {"prompt": "refine this", "read_checkout": True}}).json()
    assert answer == {"outcome": "succeeded", "result": {"text": "# Refined"}}
    call = fake.calls[0]
    argv = call["argv"]
    assert Path(call["cwd"]) == checkout.resolve()
    assert "--restricted" in argv and argv[argv.index("--tools") + 1] == "Read,Grep,Glob"
    assert "--dangerously-skip-permissions" not in argv
    assert call["timeout"] == CHECKOUT_QUERY_TIMEOUT_SECONDS
    budget = float(argv[argv.index("--max-budget-usd") + 1])
    assert budget == launch_for(JOB_QUERY_CHECKOUT).max_budget_usd > launch_for(JOB_QUERY).max_budget_usd

    # A query that does not ask for the checkout (a policy's authoring) runs as before.
    client.post("/v1/jobs/agent.query", json={"job_id": "j2", "project_id": "little-meals", "params": {"prompt": "x"}})
    assert Path(fake.calls[1]["cwd"]) == query_dir(paths) and "--restricted" not in fake.calls[1]["argv"]


def test_a_query_that_reads_the_checkout_fails_without_one(paths, conn, tmp_path, monkeypatch):
    fake = FakeQueryRunner()
    client = TestClient(build_app(paths, conn, flow=AgentFlow(paths=paths, runner=fake)))

    def ask(project_id):
        return client.post("/v1/jobs/agent.query", json={"job_id": "j1", "project_id": project_id,
                                                          "params": {"prompt": "x", "read_checkout": True}}).json()

    def unregistered(paths_, project_id, **kw):
        raise ProjectResolutionError(f"project {project_id!r} is not registered")

    monkeypatch.setattr("agent.app.resolve_project", unregistered)
    assert ask("nobody") == {"outcome": "failed", "error": "project 'nobody' is not registered"}
    monkeypatch.setattr("agent.app.resolve_project", lambda paths_, project_id, **kw: {"name": project_id, "path": str(tmp_path / "gone")})
    gone = ask("little-meals")
    assert gone["outcome"] == "failed" and str(tmp_path / "gone") in gone["error"]
    assert fake.calls == []  # Claude never started


# -- agent.ci_check (M16 little-runner parity) ------------------------------


class FakeCIGitHub:
    def __init__(self, *, pr=None, raise_on_pr=False):
        self.pr = pr if pr is not None else {"headRefName": "feature/widgets", "headRefOid": "abc123"}
        self.raise_on_pr = raise_on_pr
        self.statuses: list[dict] = []

    def pull_request(self, repo, number):
        if self.raise_on_pr:
            from agent.github_adapter import GitHubError

            raise GitHubError("no such PR")
        return self.pr

    def post_status(self, repo, sha, *, state, context, description=""):
        self.statuses.append({"state": state})


def _wait_for_ci_check(client, project_id, *, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        response = client.get(f"/v1/ci_checks/{project_id}")
        if response.status_code == 200 and response.json():
            return response.json()
        time.sleep(0.02)
    raise AssertionError(f"no ci_check recorded for {project_id!r} within {timeout}s")


def test_ci_check_job_is_accepted_and_completes_in_the_background(paths, conn, monkeypatch):
    import agent.flow as flow_module

    monkeypatch.setattr(
        flow_module.requirements_client, "run_job",
        lambda *a, **kw: {"ran": 1, "results": [{"verdict": "passed"}]},
    )
    monkeypatch.setattr(
        "agent.app.resolve_project",
        lambda paths_, project_id, **kw: {"name": project_id, "path": "/tmp/x", "repo_url": "git@github.com:o/n.git", "status": "active"},
    )
    flow = AgentFlow(paths=paths, github=FakeCIGitHub())
    app = build_app(paths, conn, flow=flow)
    client = TestClient(app)

    response = client.post(
        "/v1/jobs/agent.ci_check",
        json={"job_id": "j1", "project_id": "little-widget", "params": {"pr_number": 5, "suite_id": "s1"}},
    )
    assert response.json()["outcome"] == "accepted"

    checks = _wait_for_ci_check(client, "little-widget")
    assert len(checks) == 1
    assert checks[0]["state"] == "success"

    latest = client.get("/v1/ci_checks/little-widget/latest").json()
    assert latest["state"] == "success"


def test_ci_check_missing_project_id_fails_cleanly(paths, conn):
    app = build_app(paths, conn, flow=AgentFlow(paths=paths, github=FakeCIGitHub()))
    client = TestClient(app)
    response = client.post("/v1/jobs/agent.ci_check", json={"job_id": "j1", "params": {}})
    assert response.json()["outcome"] == "failed"


def test_ci_check_unresolvable_project_fails_cleanly(paths, conn, monkeypatch):
    from common.registry_client import ProjectResolutionError

    def raising_resolve(paths_, project_id, **kw):
        raise ProjectResolutionError(f"no such project {project_id!r}")

    monkeypatch.setattr("agent.app.resolve_project", raising_resolve)
    app = build_app(paths, conn, flow=AgentFlow(paths=paths, github=FakeCIGitHub()))
    client = TestClient(app)
    response = client.post(
        "/v1/jobs/agent.ci_check", json={"job_id": "j1", "project_id": "ghost", "params": {"pr_number": 1, "suite_id": "s1"}},
    )
    assert response.json()["outcome"] == "failed"


def test_ci_checks_latest_404_when_none_recorded(paths, conn):
    app = build_app(paths, conn, flow=AgentFlow(paths=paths, github=FakeCIGitHub()))
    client = TestClient(app)
    response = client.get("/v1/ci_checks/never-checked/latest")
    assert response.status_code == 404


def test_ci_checks_list_empty_for_unknown_project(paths, conn):
    app = build_app(paths, conn, flow=AgentFlow(paths=paths, github=FakeCIGitHub()))
    client = TestClient(app)
    assert client.get("/v1/ci_checks/never-checked").json() == []


# -- agent.session_start/stop (M16 little-runner parity) --------------------


class FakeSessionFlow:
    def __init__(self, *, running=False, start_error=None):
        self.running = running
        self.start_error = start_error
        self.started: list[str] = []
        self.stopped: list[str] = []

    def start(self, project_id, project_dir):
        if self.start_error:
            from agent.session_flow import SessionError

            raise SessionError(self.start_error)
        self.started.append(project_id)
        self.running = True
        return f"lws-{project_id}-session"

    def stop(self, project_id):
        if not self.running:
            return False
        self.stopped.append(project_id)
        self.running = False
        return True

    def is_running(self, project_id):
        return self.running


def test_session_start_succeeds(paths, conn, monkeypatch):
    monkeypatch.setattr(
        "agent.app.resolve_project",
        lambda paths_, project_id, **kw: {"name": project_id, "path": "/tmp/x", "repo_url": None, "status": "active"},
    )
    session_flow = FakeSessionFlow()
    app = build_app(paths, conn, session_flow=session_flow)
    client = TestClient(app)
    response = client.post("/v1/jobs/agent.session_start", json={"job_id": "j1", "project_id": "little-widget", "params": {}})
    body = response.json()
    assert body["outcome"] == "succeeded"
    assert body["result"]["session_name"] == "lws-little-widget-session"
    assert session_flow.started == ["little-widget"]


def test_session_start_missing_project_id_fails_cleanly(paths, conn):
    app = build_app(paths, conn, session_flow=FakeSessionFlow())
    client = TestClient(app)
    response = client.post("/v1/jobs/agent.session_start", json={"job_id": "j1", "params": {}})
    assert response.json()["outcome"] == "failed"


def test_session_start_unresolvable_project_fails_cleanly(paths, conn, monkeypatch):
    from common.registry_client import ProjectResolutionError

    def raising_resolve(paths_, project_id, **kw):
        raise ProjectResolutionError(f"no such project {project_id!r}")

    monkeypatch.setattr("agent.app.resolve_project", raising_resolve)
    app = build_app(paths, conn, session_flow=FakeSessionFlow())
    client = TestClient(app)
    response = client.post("/v1/jobs/agent.session_start", json={"job_id": "j1", "project_id": "ghost", "params": {}})
    assert response.json()["outcome"] == "failed"


def test_session_start_surfaces_a_session_error(paths, conn, monkeypatch):
    monkeypatch.setattr(
        "agent.app.resolve_project",
        lambda paths_, project_id, **kw: {"name": project_id, "path": "/tmp/x", "repo_url": None, "status": "active"},
    )
    session_flow = FakeSessionFlow(start_error="not trusted")
    app = build_app(paths, conn, session_flow=session_flow)
    client = TestClient(app)
    response = client.post("/v1/jobs/agent.session_start", json={"job_id": "j1", "project_id": "little-widget", "params": {}})
    body = response.json()
    assert body["outcome"] == "failed"
    assert "not trusted" in body["error"]


def test_session_stop_when_running(paths, conn):
    session_flow = FakeSessionFlow(running=True)
    app = build_app(paths, conn, session_flow=session_flow)
    client = TestClient(app)
    response = client.post("/v1/jobs/agent.session_stop", json={"job_id": "j1", "project_id": "little-widget", "params": {}})
    body = response.json()
    assert body["outcome"] == "succeeded"
    assert body["result"]["stopped"] is True
    assert session_flow.stopped == ["little-widget"]


def test_session_stop_when_not_running(paths, conn):
    app = build_app(paths, conn, session_flow=FakeSessionFlow(running=False))
    client = TestClient(app)
    response = client.post("/v1/jobs/agent.session_stop", json={"job_id": "j1", "project_id": "little-widget", "params": {}})
    assert response.json()["result"]["stopped"] is False


def test_session_stop_missing_project_id_fails_cleanly(paths, conn):
    app = build_app(paths, conn, session_flow=FakeSessionFlow())
    client = TestClient(app)
    response = client.post("/v1/jobs/agent.session_stop", json={"job_id": "j1", "params": {}})
    assert response.json()["outcome"] == "failed"


def test_session_status_running(paths, conn):
    app = build_app(paths, conn, session_flow=FakeSessionFlow(running=True))
    client = TestClient(app)
    response = client.get("/v1/sessions/little-widget")
    body = response.json()
    assert body == {"project_id": "little-widget", "running": True, "trusted": None, "session_name": "lws-little-widget-session"}


def test_session_status_idle(paths, conn):
    app = build_app(paths, conn, session_flow=FakeSessionFlow(running=False))
    client = TestClient(app)
    response = client.get("/v1/sessions/little-widget")
    body = response.json()
    assert body == {"project_id": "little-widget", "running": False, "trusted": None, "session_name": None}


def test_session_status_reports_trust(paths, conn, monkeypatch):
    monkeypatch.setattr(
        "agent.app.resolve_project",
        lambda paths_, project_id, **kw: {"name": project_id, "path": "/tmp/x", "repo_url": None, "status": "active"},
    )
    monkeypatch.setattr("agent.app.is_trusted", lambda project_dir: True)
    app = build_app(paths, conn, session_flow=FakeSessionFlow(running=False))
    assert TestClient(app).get("/v1/sessions/little-widget").json()["trusted"] is True


def test_run_reads_do_not_wait_on_a_long_running_task(tmp_path, paths, monkeypatch):
    """The whole point of the read-only connection: agent.run_task holds the
    write lock for up to an hour, and run history must stay readable."""
    import threading

    file_conn = db.connect(tmp_path / "file.db")
    db.create_run(file_conn, "r1", job_id=None, project_id="p", intent="task", review_mode="manual", title="t", source={})
    monkeypatch.setattr(
        "agent.app.resolve_project",
        lambda paths_, project_id, **kw: {"name": project_id, "path": "/tmp/x", "repo_url": None, "status": "active"},
    )

    started, release = threading.Event(), threading.Event()

    class BlockingFlow(AgentFlow):
        def run_task(self, *args, **kwargs):
            started.set()
            release.wait(timeout=10)  # holds the pillar's write lock meanwhile
            from agent.flow import RunTaskResult
            return RunTaskResult("r2", db.FAILED, None, "failed", None, None, "stopped by test")

    client = TestClient(build_app(paths, file_conn, flow=BlockingFlow(paths=paths), session_flow=FakeSessionFlow()))
    client.post("/v1/jobs/agent.run_task", json={"job_id": "j1", "project_id": "p", "params": {"title": "x"}})
    try:
        assert started.wait(timeout=5)
        response = client.get("/v1/runs/p")  # would hang for the whole run if it queued behind the lock
        assert [r["id"] for r in response.json()] == ["r1"]
    finally:
        release.set()


# -- the workspace-level session (a session across projects) ------------------


def test_workspace_session_starts_in_the_projects_root_without_a_registry_lookup(paths, conn, monkeypatch, tmp_path):
    monkeypatch.setenv("LITTLE_WORKSHOP_WORKSPACE_DIR", str(tmp_path))

    def never(*a, **kw):
        raise AssertionError("the workspace is not a registered project")

    monkeypatch.setattr("agent.app.resolve_project", never)
    seen = {}

    class Flow(FakeSessionFlow):
        def start(self, project_id, project_dir):
            seen["dir"] = project_dir
            return super().start(project_id, project_dir)

    client = TestClient(build_app(paths, conn, session_flow=Flow()))
    body = client.post("/v1/jobs/agent.session_start", json={"job_id": "j", "project_id": "workspace", "params": {}}).json()
    assert body["outcome"] == "succeeded" and seen["dir"] == tmp_path


def test_workspace_session_status_reports_trust_of_the_projects_root(paths, conn, monkeypatch, tmp_path):
    claude = tmp_path / "claude.json"
    claude.write_text(json.dumps({"projects": {str(tmp_path): {"hasTrustDialogAccepted": True}}}))
    monkeypatch.setenv("LITTLE_WORKSHOP_WORKSPACE_DIR", str(tmp_path))
    monkeypatch.setenv("LITTLE_WORKSHOP_CLAUDE_CONFIG_PATH", str(claude))
    client = TestClient(build_app(paths, conn, session_flow=FakeSessionFlow()))
    status = client.get("/v1/sessions/workspace").json()
    assert status["trusted"] is True and status["running"] is False


def test_workspace_dir_defaults_to_the_parent_of_the_workshop_checkout(monkeypatch, tmp_path):
    from common.workspace import workspace_dir

    monkeypatch.delenv("LITTLE_WORKSHOP_WORKSPACE_DIR", raising=False)
    monkeypatch.setenv("LITTLE_WORKSHOP_REPO_DIR", str(tmp_path / "little-workshop"))
    assert workspace_dir() == tmp_path.resolve()


# -- incidents: an unhandled exception, awaiting the user's confirmation --------------


def _report(client, **overrides):
    body = {"project_id": "little-widget", "pillar": "agent", "context": "POST /v1/jobs/x",
            "exception_type": "ValueError", "message": "boom", "traceback": "Traceback...\nValueError: boom"}
    body.update(overrides)
    return client.post("/v1/incidents", json=body)


def test_report_incident_creates_an_open_incident(paths, conn, tmp_path, monkeypatch):
    client, _ = make_client(paths, conn, tmp_path, monkeypatch)
    incident_id = _report(client).json()["id"]
    incident = client.get(f"/v1/incidents/{incident_id}").json()
    assert incident["status"] == "open" and incident["project_id"] == "little-widget"
    assert incident["exception_type"] == "ValueError" and incident["message"] == "boom"


def test_report_incident_requires_the_essentials(paths, conn, tmp_path, monkeypatch):
    client, _ = make_client(paths, conn, tmp_path, monkeypatch)
    assert client.post("/v1/incidents", json={"pillar": "agent"}).status_code == 422


def test_list_incidents_filters_by_status(paths, conn, tmp_path, monkeypatch):
    client, _ = make_client(paths, conn, tmp_path, monkeypatch)
    a = _report(client).json()["id"]
    _report(client)
    client.post("/v1/jobs/agent.dismiss_incident", json={"job_id": "j", "params": {"incident_id": a}})
    assert len(client.get("/v1/incidents", params={"status": "open"}).json()) == 1
    assert len(client.get("/v1/incidents", params={"status": "dismissed"}).json()) == 1
    assert len(client.get("/v1/incidents").json()) == 2


def test_get_unknown_incident_404s(paths, conn, tmp_path, monkeypatch):
    client, _ = make_client(paths, conn, tmp_path, monkeypatch)
    assert client.get("/v1/incidents/ghost").status_code == 404


def test_dismiss_incident_discards_it_without_a_task(paths, conn, tmp_path, monkeypatch):
    client, _ = make_client(paths, conn, tmp_path, monkeypatch)
    incident_id = _report(client).json()["id"]
    response = client.post("/v1/jobs/agent.dismiss_incident", json={"job_id": "j", "params": {"incident_id": incident_id}})
    assert response.json()["outcome"] == "succeeded"
    assert client.get(f"/v1/incidents/{incident_id}").json()["status"] == "dismissed"
    assert client.get("/v1/runs/little-widget").json() == []


def test_dismiss_unknown_or_already_decided_incident_fails_cleanly(paths, conn, tmp_path, monkeypatch):
    client, _ = make_client(paths, conn, tmp_path, monkeypatch)
    assert client.post("/v1/jobs/agent.dismiss_incident", json={"job_id": "j", "params": {"incident_id": "ghost"}}).json()["outcome"] == "failed"
    incident_id = _report(client).json()["id"]
    client.post("/v1/jobs/agent.dismiss_incident", json={"job_id": "j", "params": {"incident_id": incident_id}})
    second = client.post("/v1/jobs/agent.dismiss_incident", json={"job_id": "j", "params": {"incident_id": incident_id}})
    assert second.json()["outcome"] == "failed" and "already" in second.json()["error"]


def test_confirm_incident_starts_a_bug_task_and_links_the_run(paths, conn, tmp_path, monkeypatch):
    client, _ = make_client(paths, conn, tmp_path, monkeypatch)
    incident_id = _report(client, project_id="little-meals").json()["id"]
    response = client.post("/v1/jobs/agent.confirm_incident", json={"job_id": "j", "params": {"incident_id": incident_id}})
    body = response.json()
    assert body["outcome"] == "accepted"
    run_id = body["result"]["run_id"]

    run = _wait_for_run(client, "little-meals", run_id)
    assert run["intent"] == "bug" and "ValueError" in run["title"]

    incident = client.get(f"/v1/incidents/{incident_id}").json()
    assert incident["status"] == "confirmed" and incident["run_id"] == run_id


def test_confirm_incident_honors_the_chosen_review_mode(paths, conn, tmp_path, monkeypatch):
    client, _ = make_client(paths, conn, tmp_path, monkeypatch, status="done")
    incident_id = _report(client, project_id="little-meals").json()["id"]
    response = client.post("/v1/jobs/agent.confirm_incident", json={"job_id": "j", "params": {"incident_id": incident_id, "review_mode": "auto_approve"}})
    run_id = response.json()["result"]["run_id"]
    run = _wait_for_run(client, "little-meals", run_id)
    assert run["review_mode"] == "auto_approve"


def test_confirm_incident_defaults_to_auto_approve(paths, conn, tmp_path, monkeypatch):
    client, _ = make_client(paths, conn, tmp_path, monkeypatch, status="done")
    incident_id = _report(client, project_id="little-meals").json()["id"]
    response = client.post("/v1/jobs/agent.confirm_incident", json={"job_id": "j", "params": {"incident_id": incident_id}})
    run = _wait_for_run(client, "little-meals", response.json()["result"]["run_id"])
    assert run["review_mode"] == "auto_approve"


def test_confirm_unknown_or_already_decided_incident_fails_cleanly(paths, conn, tmp_path, monkeypatch):
    client, _ = make_client(paths, conn, tmp_path, monkeypatch)
    assert client.post("/v1/jobs/agent.confirm_incident", json={"job_id": "j", "params": {"incident_id": "ghost"}}).json()["outcome"] == "failed"
    incident_id = _report(client).json()["id"]
    client.post("/v1/jobs/agent.dismiss_incident", json={"job_id": "j", "params": {"incident_id": incident_id}})
    second = client.post("/v1/jobs/agent.confirm_incident", json={"job_id": "j", "params": {"incident_id": incident_id}})
    assert second.json()["outcome"] == "failed" and "already" in second.json()["error"]


def test_recover_interrupted_runs_fails_orphaned_runs_and_reports_their_jobs(paths, conn, monkeypatch):
    import agent.app as app_module

    for run_id, job_id in (("orphan-1", "job-1"), ("orphan-2", None)):
        db.create_run(conn, run_id, job_id=job_id, project_id="proj", intent="task",
                      review_mode="auto_approve", title=run_id, source={"type": "manual"})
    db.create_run(conn, "waiting", job_id="job-3", project_id="proj", intent="task",
                  review_mode="manual", title="waiting", source={"type": "manual"})
    db.update_run(conn, "waiting", status=db.AWAITING_FINISH)

    reported = []
    monkeypatch.setattr(app_module, "_complete_orchestration_job",
                        lambda paths_, job_id, **kw: reported.append((job_id, kw)))

    recovered = app_module.recover_interrupted_runs(paths, conn)

    assert sorted(r["id"] for r in recovered) == ["orphan-1", "orphan-2"]
    for run_id in ("orphan-1", "orphan-2"):
        run = db.get_run(conn, run_id)
        assert run["status"] == db.FAILED and run["summary"] == app_module.INTERRUPTED_RUN_REASON
    assert db.get_run(conn, "waiting")["status"] == db.AWAITING_FINISH  # a finished run awaiting review is untouched
    # Only a run that came from a Job has one to report; a failure, never a success.
    assert reported == [("job-1", {"succeeded": False, "result": None, "error": app_module.INTERRUPTED_RUN_REASON})]


def test_recover_interrupted_runs_is_a_noop_with_nothing_running(paths, conn, monkeypatch):
    import agent.app as app_module

    monkeypatch.setattr(app_module, "_complete_orchestration_job", lambda *a, **kw: pytest.fail("nothing to report"))
    assert app_module.recover_interrupted_runs(paths, conn) == []


# --- REQ-000000313: A failed task leaves the attention list when moved to done, or after a week ---

def test_dismiss_task_job_marks_a_failed_run_and_keeps_its_status(paths, conn, tmp_path, monkeypatch):
    client, _ = make_client(paths, conn, tmp_path, monkeypatch)
    db.create_run(conn, "r1", job_id="j1", project_id="little-meals", intent="task", review_mode="manual", title="t", source={})
    db.update_run(conn, "r1", status=db.FAILED)
    answer = client.post("/v1/jobs/agent.dismiss_task", json={"job_id": "j9", "params": {"run_id": "r1"}}).json()
    assert answer["outcome"] == "succeeded" and "done list" in answer["result"]["summary"]
    run = client.get("/v1/runs/little-meals/r1").json()
    assert run["status"] == db.FAILED and run["dismissed_at"]
    client.post("/v1/jobs/agent.dismiss_task", json={"job_id": "j9", "params": {"run_id": "r1"}})
    assert client.get("/v1/runs/little-meals/r1").json()["dismissed_at"] == run["dismissed_at"]  # idempotent
    assert client.post("/v1/jobs/agent.dismiss_task", json={"job_id": "j9", "params": {}}).json()["outcome"] == "failed"
    assert client.post("/v1/jobs/agent.dismiss_task", json={"job_id": "j9", "params": {"run_id": "nope"}}).json()["outcome"] == "failed"


def test_dismiss_task_refuses_a_run_that_did_not_fail(conn):
    from agent.flow import AgentFlowError

    db.create_run(conn, "r2", job_id="j2", project_id="p", intent="task", review_mode="manual", title="t", source={})
    flow = AgentFlow.__new__(AgentFlow)
    with pytest.raises(AgentFlowError, match="did not fail"):
        flow.dismiss_task(conn, "r2")
    with pytest.raises(AgentFlowError, match="no such run"):
        flow.dismiss_task(conn, "ghost")


def test_an_existing_database_gets_the_dismissed_column(tmp_path):
    import sqlite3

    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE agent_runs (id TEXT PRIMARY KEY, job_id TEXT, project_id TEXT NOT NULL, status TEXT NOT NULL, intent TEXT NOT NULL, branch TEXT, branch_type TEXT, review_mode TEXT NOT NULL, completion_status TEXT, title TEXT NOT NULL, summary TEXT, report TEXT, pr_url TEXT, source_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
    old.commit()
    old.close()
    conn = db.connect(path)
    assert "dismissed_at" in {row["name"] for row in conn.execute("PRAGMA table_info(agent_runs)")}


# --- REQ-000000398, REQ-000000399: remote control on a running Claude job ---


class RCRunner:
    """Records systemd-run/systemctl/dtach calls; nothing real starts."""

    def __init__(self, ok=True):
        self.calls, self.ok = [], ok

    def run(self, argv, timeout=None, cwd=None):
        self.calls.append((argv, cwd))
        return CommandResult(0 if self.ok else 1, "", "boom")


def _rc_client(paths, conn, tmp_path, monkeypatch, *, runner=None, status=db.RUNNING, session_id="11111111-aaaa"):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    monkeypatch.setattr("agent.app.resolve_project", lambda paths_, project_id, **kw: {"name": project_id, "path": str(checkout)})
    monkeypatch.setattr("agent.remote_control.is_trusted", lambda directory: True)
    runner = runner or RCRunner()
    db.create_run(conn, "run00000001", job_id="j1", project_id="little-meals", intent="task", review_mode="manual", title="t", source={})
    db.update_run(conn, "run00000001", status=status, session_id=session_id)
    return TestClient(build_app(paths, conn, flow=AgentFlow(paths=paths, runner=runner))), runner


def test_remote_control_of_a_running_job_without_a_session_can_be_activated(paths, conn, tmp_path, monkeypatch):
    c, _ = _rc_client(paths, conn, tmp_path, monkeypatch)
    state = c.get("/v1/remote-control/j1", params={"project_id": "little-meals"}).json()
    assert state["running"] and state["can_activate"] and not state["active"] and state["session_name"] is None


def test_a_job_without_a_claude_session_has_no_remote_control(paths, conn, tmp_path, monkeypatch):
    c, _ = _rc_client(paths, conn, tmp_path, monkeypatch)
    assert c.get("/v1/remote-control/nope").status_code == 404
    assert c.post("/v1/remote-control/nope/activate").status_code == 404


def test_activating_resumes_the_session_with_remote_control_and_never_touches_the_live_process(paths, conn, tmp_path, monkeypatch):
    c, runner = _rc_client(paths, conn, tmp_path, monkeypatch)
    name = c.post("/v1/remote-control/j1/activate", params={"project_id": "little-meals"}).json()["session_name"]
    assert name == "lws-little-meals-rc-run00000"
    launch = next(argv for argv, _ in runner.calls if argv[0] == "systemd-run")
    assert launch[launch.index("claude"):][:5] == ["claude", "--resume", "11111111-aaaa", "--remote-control", name]
    assert not any(a[0] in ("kill", "pkill") or a[:3] == ["systemctl", "--user", "stop"] for a, _ in runner.calls)
    state = c.get("/v1/remote-control/j1").json()
    assert state["active"] and state["can_deactivate"] and not state["can_activate"] and state["session_name"] == name
    assert db.list_sessions(conn, "run00000001")[0]["stopped_at"] is None


def test_a_second_activation_is_refused_and_creates_no_second_session(paths, conn, tmp_path, monkeypatch):
    c, _ = _rc_client(paths, conn, tmp_path, monkeypatch)
    c.post("/v1/remote-control/j1/activate")
    assert c.post("/v1/remote-control/j1/activate").status_code == 409
    assert len(db.list_sessions(conn, "run00000001")) == 1


def test_an_existing_remote_control_session_is_shown_as_set_up_and_cannot_be_duplicated_or_dropped(paths, conn, tmp_path, monkeypatch):
    c, runner = _rc_client(paths, conn, tmp_path, monkeypatch)
    db.record_session_start(conn, "run00000001", "little-meals", "lws-little-meals-run-run00000")
    state = c.get("/v1/remote-control/j1").json()
    assert state["already_set_up"] and state["active"] and not state["can_activate"] and not state["can_deactivate"]
    assert c.post("/v1/remote-control/j1/activate").status_code == 409
    assert c.post("/v1/remote-control/j1/deactivate").status_code == 409  # dropping it would end the working Claude
    assert runner.calls == [] and len(db.list_sessions(conn, "run00000001")) == 1


def test_deactivating_stops_only_the_activated_scope_and_sets_stopped_at(paths, conn, tmp_path, monkeypatch):
    c, runner = _rc_client(paths, conn, tmp_path, monkeypatch)
    c.post("/v1/remote-control/j1/activate")
    runner.calls.clear()
    assert c.post("/v1/remote-control/j1/deactivate").json() == {"deactivated": True}
    assert [a for a, _ in runner.calls if a[:3] == ["systemctl", "--user", "stop"]] == [["systemctl", "--user", "stop", "lws-rc-run00000001.scope"]]
    assert not any(a[0] in ("kill", "pkill") for a, _ in runner.calls)
    assert db.get_run(conn, "run00000001")["status"] == db.RUNNING  # the task keeps running
    state = c.get("/v1/remote-control/j1").json()
    assert not state["active"] and state["stopped_at"] and state["session_name"] and state["can_activate"]


def test_a_job_that_is_not_running_offers_nothing_and_keeps_its_history(paths, conn, tmp_path, monkeypatch):
    c, _ = _rc_client(paths, conn, tmp_path, monkeypatch, status=db.AWAITING_FINISH)
    state = c.get("/v1/remote-control/j1").json()
    assert not state["running"] and not state["can_activate"] and not state["active"]
    assert c.post("/v1/remote-control/j1/activate").status_code == 409
    db.record_session_start(conn, "run00000001", "little-meals", "lws-little-meals-run-run00000")  # never got its stopped_at
    state = c.get("/v1/remote-control/j1").json()
    assert not state["active"] and state["session_name"]  # history, not active: the job is not running


def test_activation_failures_are_reported_and_record_nothing(paths, conn, tmp_path, monkeypatch):
    c, _ = _rc_client(paths, conn, tmp_path, monkeypatch, runner=RCRunner(ok=False))
    response = c.post("/v1/remote-control/j1/activate")
    assert response.status_code == 409 and db.list_sessions(conn, "run00000001") == []


def test_stopping_a_task_ends_its_activated_remote_control(paths, conn, tmp_path, monkeypatch):
    c, runner = _rc_client(paths, conn, tmp_path, monkeypatch)
    c.post("/v1/remote-control/j1/activate")
    assert c.post("/v1/jobs/j1/stop", json={"type": "agent.run_task"}).json() == {"stopped": True}
    assert db.list_sessions(conn, "run00000001")[0]["stopped_at"] is not None
    assert any(a == ["systemctl", "--user", "stop", "lws-rc-run00000001.scope"] for a, _ in runner.calls)


def test_a_running_query_session_can_get_remote_control_in_its_own_folder(paths, conn, tmp_path, monkeypatch):
    c, runner = _rc_client(paths, conn, tmp_path, monkeypatch)
    folder = tmp_path / "qfolder"
    folder.mkdir()
    db.start_query_session(conn, job_id="q1", session_id="22222222-bbbb", ref=None, cwd=str(folder))
    assert c.post("/v1/remote-control/q1/activate", params={"project_id": "little-meals"}).status_code == 200
    argv, cwd = next((a, d) for a, d in runner.calls if a[0] == "systemd-run")
    assert "22222222-bbbb" in argv and cwd == folder and "--dangerously-skip-permissions" not in argv
    db.finish_query_session(conn, "22222222-bbbb")
    assert not c.get("/v1/remote-control/q1").json()["can_activate"]
