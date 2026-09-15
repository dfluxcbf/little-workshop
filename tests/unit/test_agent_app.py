import json
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent import db
from agent.app import build_app
from agent.flow import AgentFlow
from common.paths import Paths
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
