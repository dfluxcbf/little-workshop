"""Policy & Compliance's early warnings (docs/pillars/policy_compliance.md, "Enforcement moves to
the moment of the change"): the `commit` gate point, `policy.evaluate`, and verdicts recorded with
their reasons."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from common.paths import Paths
from common.shell import CommandResult
from policy import app as app_module
from policy import db, gates, sandbox
from policy.app import build_app
from policy.models import CheckOutcome, GatePoint
from policy.sandbox import SandboxResult, SandboxUnavailable


@pytest.fixture
def paths(tmp_path):
    p = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    p.ensure_layout()
    return p


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "policy.db")
    yield connection
    connection.close()


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


@pytest.fixture
def checkout(tmp_path):
    repo = tmp_path / "proj"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "dev")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "T")
    (repo / "VERSION").write_text("0.1.0\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


@pytest.fixture
def client(conn, paths, checkout, monkeypatch):
    monkeypatch.setattr(app_module.registry_client, "list_active_project_ids", lambda paths, **kw: ["proj-a", "proj-b"])
    monkeypatch.setattr(app_module, "resolve_project", lambda paths, project_id, **kw: {"path": str(checkout), "name": project_id})
    return TestClient(build_app(conn, paths))


def _activate(client, topic, gate_points, *, target_pillar="version", projects=None):
    params = {
        "ears_text": f"The project shall satisfy {topic}.", "test_script": f"def test_{topic.replace('-', '_')}(): pass\n",
        "topic": topic, "target_pillar": target_pillar, "scope_level": "all_projects", "created_by": "framework",
        "gate_points": gate_points, "approved": True,
    }
    response = client.post("/v1/jobs/policy.activate", json={"job_id": "j", "params": params})
    assert response.json()["outcome"] == "succeeded", response.json()
    policy = response.json()["result"]
    if projects is not None:
        client.post("/v1/jobs/policy.set_projects", json={"job_id": "j", "params": {"policy_id": policy["id"], "projects": projects}})
    return policy


# --- REQ-000000299: the commit gate point ----------------------------------------------------

def test_commit_is_a_gate_point_only_a_project_bound_policy_may_use(client):
    assert GatePoint("commit") is GatePoint.COMMIT
    _activate(client, "commit-rule", ["commit", "branch_finish"])
    params = {
        "ears_text": "The system shall x.", "test_script": "def test_x(): pass\n", "topic": "framework-rule",
        "target_pillar": "policy", "scope_level": "all_projects", "created_by": "framework",
        "gate_points": ["commit"], "approved": True,
    }
    refused = client.post("/v1/jobs/policy.activate", json={"job_id": "j", "params": params}).json()
    assert refused["outcome"] == "failed" and "only a project-bound policy" in refused["error"]


def test_evaluate_at_commit_hands_the_pending_commit_to_each_script(client, monkeypatch):
    _activate(client, "commit-rule", ["commit"])
    _activate(client, "finish-only", ["branch_finish"])
    seen = []

    def fake_run(script, mounts, *, env=None, inputs=None, **kw):
        seen.append((script, env, inputs))
        return SandboxResult(CheckOutcome.FAIL, "VERSION is not raised. Fix: raise it.")

    monkeypatch.setattr(app_module.sandbox, "run_policy_check", fake_run)
    body = {"project_id": "proj-a", "gate_point": "commit",
            "commit": {"message": "feat: x", "files": ["src/a.py", "b.md"], "version": "0.1.1\n"}}
    result = client.post("/v1/policies/evaluate", json=body).json()

    assert [r["topic"] for r in result["results"]] == ["commit-rule"]
    assert result["results"][0]["outcome"] == "fail"
    assert "Fix: raise it." in result["results"][0]["detail"]
    assert result["blocking"] == ["commit-rule"]
    _, env, inputs = seen[0]
    assert env == {"POLICY_COMMIT_DIR": "/policy/input"}
    assert inputs == {"message": "feat: x", "files": "src/a.py\nb.md", "VERSION": "0.1.1\n"}


def test_evaluate_at_commit_needs_the_pending_commit(client):
    response = client.post("/v1/policies/evaluate", json={"project_id": "proj-a", "gate_point": "commit"})
    assert response.status_code == 400


# --- REQ-000000300: evaluation records and holds nothing -------------------------------------

def test_evaluate_runs_the_winning_policies_and_records_nothing(client, conn, monkeypatch):
    gating = _activate(client, "gating-rule", ["branch_finish"])
    _activate(client, "reported-only", ["branch_finish"],
              projects=[{"project_id": "proj-a", "gating_enabled": False}])
    _activate(client, "passes", ["branch_finish"])
    verdicts = {"gating-rule": CheckOutcome.FAIL, "reported-only": CheckOutcome.FAIL, "passes": CheckOutcome.PASS}

    def fake_run(script, mounts, **kw):
        topic = next(t for t in verdicts if f"def test_{t.replace('-', '_')}" in script)
        return SandboxResult(verdicts[topic], f"{topic} detail")

    monkeypatch.setattr(app_module.sandbox, "run_policy_check", fake_run)
    result = client.post("/v1/policies/evaluate", json={"project_id": "proj-a", "gate_point": "branch_finish"}).json()

    by_topic = {r["topic"]: r for r in result["results"]}
    assert set(by_topic) == set(verdicts)
    assert by_topic["reported-only"]["gating"] is False
    assert result["blocking"] == ["gating-rule"]
    assert result["errors"] == []
    assert by_topic["gating-rule"]["policy_id"] == gating["id"]
    assert db.list_check_results(conn) == []


def test_evaluate_reports_a_sandbox_that_cannot_run_as_an_error(client, monkeypatch):
    _activate(client, "rule", ["release"])

    def boom(*a, **kw):
        raise SandboxUnavailable("docker is not running")

    monkeypatch.setattr(app_module.sandbox, "run_policy_check", boom)
    result = client.post("/v1/policies/evaluate", json={"project_id": "proj-a", "gate_point": "release"}).json()
    assert result["errors"] == ["rule"] and result["blocking"] == []
    assert "docker is not running" in result["results"][0]["detail"]


def test_evaluate_refuses_an_unknown_gate_point(client):
    assert client.post("/v1/policies/evaluate", json={"project_id": "proj-a", "gate_point": "nope"}).status_code == 400


def test_evaluate_judges_a_worktree_of_the_project(client, checkout, tmp_path, monkeypatch):
    _activate(client, "rule", ["branch_finish"])
    worktree = tmp_path / "wt"
    _git(checkout, "worktree", "add", "-q", "-b", "feature/w", str(worktree))
    mounted = []
    monkeypatch.setattr(app_module.sandbox, "run_policy_check",
                        lambda script, mounts, **kw: (mounted.append(mounts), SandboxResult(CheckOutcome.PASS, "ok"))[1])
    body = {"project_id": "proj-a", "gate_point": "branch_finish", "checkout": str(worktree)}
    assert client.post("/v1/policies/evaluate", json=body).status_code == 200
    assert any(path == worktree.resolve() for path in mounted[0])
    # The worktree's git directory lives in the main repository's: its common dir is mounted too.
    assert (checkout / ".git").resolve() in mounted[0]


def test_evaluate_refuses_a_checkout_of_another_repository(client, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    _git(other, "init", "-q")
    body = {"project_id": "proj-a", "gate_point": "branch_finish", "checkout": str(other)}
    response = client.post("/v1/policies/evaluate", json=body)
    assert response.status_code == 400 and "not a checkout of project" in response.json()["detail"]
    body["checkout"] = "relative/path"
    assert client.post("/v1/policies/evaluate", json=body).status_code == 400


# --- REQ-000000302: a check records why it failed ---------------------------------------------

PYTEST_FAIL = """F                                                                        [100%]
=================================== FAILURES ===================================
_________________________________ test_rule __________________________________
test_policy.py:12: in test_rule
    assert ok, "VERSION 0.1.0 is not higher than 0.1.0. Fix: raise VERSION."
E   AssertionError: VERSION 0.1.0 is not higher than 0.1.0. Fix: raise VERSION.
E   assert False
=========================== short test summary info ============================
FAILED test_policy.py::test_rule - AssertionError: VERSION 0.1.0 is not higher than 0.1.0. Fix: raise VERSION.
1 failed in 0.05s
"""


def test_a_failed_checks_detail_is_its_reason():
    result = sandbox._parse_verdict(PYTEST_FAIL, 1)
    assert result.outcome == CheckOutcome.FAIL
    lines = result.detail.splitlines()
    assert lines[0] == "AssertionError: VERSION 0.1.0 is not higher than 0.1.0. Fix: raise VERSION."
    assert lines[-1] == "1 failed in 0.05s"
    assert len(lines) == len(set(lines))


def test_a_skipped_checks_detail_is_the_skip_reason():
    out = "s [100%]\n=== short test summary info ===\nSKIPPED [1] test_policy.py:9: no release yet\n1 skipped in 0.01s\n"
    result = sandbox._parse_verdict(out, 0)
    assert result.outcome == CheckOutcome.NOT_APPLICABLE
    assert "no release yet" in result.detail


def test_a_long_detail_is_capped():
    out = "".join(f"E   line {i} {'x' * 80}\n" for i in range(200)) + "1 failed in 1s\n"
    detail = sandbox._parse_verdict(out, 1).detail
    assert len(detail) <= sandbox.DETAIL_LIMIT and detail.endswith("[... truncated]")


class _Runner:
    def __init__(self):
        self.calls = []
        self.files = {}

    def run(self, argv, timeout=None, cwd=None):
        self.calls.append(argv)
        if argv[:2] == ["docker", "run"]:
            mount = next(a for a in argv if a.endswith(":/policy/input:ro"))
            folder = Path(mount.split(":")[0])
            self.files = {p.name: p.read_text() for p in folder.iterdir()}
            return CommandResult(0, "1 passed in 0.01s", "")
        return CommandResult(0, "", "")


def test_the_sandbox_passes_environment_and_input_files_to_the_script():
    runner = _Runner()
    sandbox.run_policy_check("def test_x(): pass", {}, env={"POLICY_COMMIT_DIR": "/policy/input"},
                             inputs={"message": "feat: x", "VERSION": "1.0.0\n"}, runner=runner, timeout=5.0)
    run = next(c for c in runner.calls if c[:2] == ["docker", "run"])
    assert "POLICY_COMMIT_DIR=/policy/input" in run and "COLUMNS=400" in run
    assert "--tb=short" in run
    assert runner.files == {"message": "feat: x", "VERSION": "1.0.0\n"}


def test_the_sandbox_refuses_an_input_name_that_is_a_path():
    with pytest.raises(ValueError):
        sandbox.run_policy_check("x", {}, inputs={"../escape": "x"}, runner=_Runner(), timeout=5.0)


def test_a_worktrees_common_git_directory_is_mounted(tmp_path, checkout):
    worktree = tmp_path / "wt2"
    _git(checkout, "worktree", "add", "-q", "-b", "feature/z", str(worktree))
    mounts = sandbox.git_dir_mount(worktree)
    assert (checkout / ".git").resolve() in mounts


# --- the drafting prompt states the script contract -------------------------------------------

def test_the_drafting_prompt_states_the_script_contract():
    captured = {}

    def query(prompt):
        captured["prompt"] = prompt
        return "def test_x(): pass"

    gates.generate_test_script(query, "x", project_bound=True)
    assert 'os.environ.get(\\"POLICY_PROJECT_DIR\\", \\"/project\\")' in captured["prompt"] or \
        'os.environ.get("POLICY_PROJECT_DIR", "/project")' in captured["prompt"]
    assert "how to fix it" in captured["prompt"]
    assert "POLICY_COMMIT_DIR" not in captured["prompt"]
    gates.generate_test_script(query, "x", project_bound=True, judges_commits=True)
    assert "POLICY_COMMIT_DIR" in captured["prompt"]
