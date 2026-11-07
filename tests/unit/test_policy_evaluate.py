"""Policy & Compliance's early warnings (docs/pillars/policy_compliance.md, "Enforcement moves to
the moment of the change"): the `commit` gate point, `policy.evaluate`, and verdicts recorded with
their reasons."""

from __future__ import annotations

import subprocess
import threading
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


# --- REQ-000000483: the commit gate point ----------------------------------------------------

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


# --- REQ-000000483: a preflight records verdicts the gate reuses, and holds nothing ------------

def test_evaluate_runs_the_winning_policies_and_holds_nothing(client, conn, monkeypatch):
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
    # recorded (the checkout is clean), each under the fingerprint the gate's check computes
    recorded = db.list_check_results(conn)
    assert {r.outcome for r in recorded} == {CheckOutcome.FAIL, CheckOutcome.PASS} and len(recorded) == 3
    assert all(r.checked_state and r.job_id is None for r in recorded)


def _counting_sandbox(monkeypatch, outcome=CheckOutcome.PASS):
    calls = []
    monkeypatch.setattr(app_module.sandbox, "run_policy_check",
                        lambda *a, **kw: calls.append(1) or SandboxResult(outcome, "detail"))
    return calls


def test_a_second_preflight_on_the_same_commit_reuses_the_verdict(client, monkeypatch):
    _activate(client, "rule", ["branch_finish"])
    calls = _counting_sandbox(monkeypatch)
    body = {"project_id": "proj-a", "gate_point": "branch_finish"}
    first = client.post("/v1/policies/evaluate", json=body).json()["results"][0]
    second = client.post("/v1/policies/evaluate", json=body).json()["results"][0]
    assert len(calls) == 1
    assert (first["reused"], second["reused"]) == (False, True) and second["outcome"] == "pass"


def test_the_gates_check_reuses_the_preflights_verdict(client, conn, monkeypatch):
    policy = _activate(client, "rule", ["branch_finish"])
    calls = _counting_sandbox(monkeypatch, CheckOutcome.FAIL)
    client.post("/v1/policies/evaluate", json={"project_id": "proj-a", "gate_point": "branch_finish"})
    checked = client.post("/v1/jobs/policy.check_project",
                          json={"job_id": "gate", "project_id": "proj-a", "params": {"policy_id": policy["id"]}}).json()
    assert len(calls) == 1
    assert checked["skipped"] == "unchanged" and checked["result"]["outcome"] == "fail"


def test_an_uncommitted_change_is_judged_but_not_recorded(client, conn, checkout, monkeypatch):
    _activate(client, "rule", ["branch_finish"])
    calls = _counting_sandbox(monkeypatch)
    (checkout / "VERSION").write_text("0.1.1\n")
    body = {"project_id": "proj-a", "gate_point": "branch_finish"}
    client.post("/v1/policies/evaluate", json=body)
    client.post("/v1/policies/evaluate", json=body)
    assert len(calls) == 2 and db.list_check_results(conn) == []


def test_a_pending_commit_is_never_recorded(client, conn, monkeypatch):
    _activate(client, "commit-rule", ["commit"])
    _counting_sandbox(monkeypatch)
    body = {"project_id": "proj-a", "gate_point": "commit", "commit": {"message": "feat: x", "files": []}}
    client.post("/v1/policies/evaluate", json=body)
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


def test_a_coloured_failure_still_records_its_assertion_message():
    """FORCE_COLOR makes pytest colour every line: the reasons must survive it."""
    red, bold, reset = "\x1b[31m", "\x1b[1m", "\x1b[0m"
    coloured = "\n".join(f"{bold}{red}{line}{reset}" if line.startswith(("E ", "FAILED", "1 failed")) else line
                         for line in PYTEST_FAIL.splitlines()) + "\n"
    result = sandbox._parse_verdict(coloured, 1)
    assert result.outcome == CheckOutcome.FAIL
    assert result.detail.splitlines()[0] == "AssertionError: VERSION 0.1.0 is not higher than 0.1.0. Fix: raise VERSION."
    assert "\x1b" not in result.detail


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
    assert "--tb=short" in run and "--color=no" in run
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


# --- REQ-000000482: a policy check never re-runs on unchanged inputs ---------------------------

def _activate_with_inputs(client, topic, inputs):
    params = {
        "ears_text": f"The project shall satisfy {topic}.", "test_script": f"def test_{topic.replace('-', '_')}(): pass\n",
        "topic": topic, "target_pillar": "version", "scope_level": "all_projects", "created_by": "framework",
        "gate_points": ["release"], "inputs": inputs, "approved": True,
    }
    response = client.post("/v1/jobs/policy.activate", json={"job_id": "j", "params": params}).json()
    assert response["outcome"] == "succeeded", response
    return response["result"]


def _check(client, policy):
    answer = client.post("/v1/jobs/policy.check_project",
                         json={"job_id": "c", "project_id": "proj-a", "params": {"policy_id": policy["id"]}}).json()
    # an accepted check runs on a worker thread: let it finish while the fake sandbox is still in place
    for thread in threading.enumerate():
        if "(worker)" in thread.name:
            thread.join(timeout=10)
    return answer


def _commit(repo, name, text):
    (repo / name).parent.mkdir(parents=True, exist_ok=True)
    (repo / name).write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", f"change {name}")


def test_a_policy_keeps_its_inputs(client, conn):
    policy = _activate_with_inputs(client, "coverage", ["tests/", "src", "src"])
    assert policy["inputs"] == ["src", "tests"]
    assert db.get_policy(conn, policy["id"]).inputs == ["src", "tests"]
    # an edit that does not name inputs keeps them
    params = {"policy_id": policy["id"], "ears_text": "The project shall be covered.", "test_script": "def test_x(): pass\n",
              "topic": "coverage", "target_pillar": "version", "scope_level": "all_projects", "created_by": "framework",
              "approved": True}
    edited = client.post("/v1/jobs/policy.activate", json={"job_id": "j", "params": params}).json()["result"]
    assert edited["inputs"] == ["src", "tests"]


def test_inputs_must_be_paths_inside_the_repository(client):
    params = {"ears_text": "The project shall x.", "test_script": "def test_x(): pass\n", "topic": "t",
              "target_pillar": "version", "scope_level": "all_projects", "created_by": "framework",
              "inputs": ["../outside"], "approved": True}
    refused = client.post("/v1/jobs/policy.activate", json={"job_id": "j", "params": params}).json()
    assert refused["outcome"] == "failed" and "inputs" in refused["error"]


def test_a_docs_only_commit_keeps_the_verdict_of_a_policy_with_inputs(client, checkout, monkeypatch):
    _commit(checkout, "src/a.py", "x = 1\n")
    policy = _activate_with_inputs(client, "coverage", ["src"])
    calls = _counting_sandbox(monkeypatch)
    client.post("/v1/policies/evaluate", json={"project_id": "proj-a", "gate_point": "release"})
    _commit(checkout, "docs/notes.md", "notes\n")
    assert _check(client, policy)["skipped"] == "unchanged"
    assert len(calls) == 1
    _commit(checkout, "src/a.py", "x = 2\n")
    assert _check(client, policy)["outcome"] == "accepted"  # src changed: the check runs again


def test_without_inputs_any_new_commit_runs_the_check_again(client, checkout, monkeypatch):
    policy = _activate(client, "rule", ["release"])
    _counting_sandbox(monkeypatch)
    client.post("/v1/policies/evaluate", json={"project_id": "proj-a", "gate_point": "release"})
    assert _check(client, policy)["skipped"] == "unchanged"
    _commit(checkout, "docs/notes.md", "notes\n")
    assert _check(client, policy)["outcome"] == "accepted"


def test_the_fingerprint_needs_a_clean_checkout_only_when_asked(checkout):
    from policy.models import Policy
    policy = Policy(id="p", topic="t", target_pillar="version", ears_text="x", test_script="x",
                    scope_level="all_projects", created_by="framework", inputs=["src"])
    _commit(checkout, "src/a.py", "x = 1\n")
    clean = app_module.check_fingerprint(policy, checkout, clean_only=True)
    assert clean.startswith("tree:")
    (checkout / "README.md").write_text("outside the inputs\n")
    assert app_module.check_fingerprint(policy, checkout, clean_only=True) == clean
    (checkout / "src" / "b.py").write_text("untracked\n")
    assert app_module.check_fingerprint(policy, checkout, clean_only=True) == ""
    assert app_module.check_fingerprint(policy, checkout) == clean


def test_an_existing_database_gains_the_inputs_column(tmp_path):
    import sqlite3
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE policies (id TEXT PRIMARY KEY, topic TEXT NOT NULL, target_pillar TEXT NOT NULL, "
                "ears_text TEXT NOT NULL, test_script TEXT NOT NULL, scope_level TEXT NOT NULL, scope_target TEXT, "
                "created_by TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1, gate_points TEXT NOT NULL DEFAULT '[]', "
                "created_at TEXT NOT NULL, amended_at TEXT NOT NULL)")
    old.execute("INSERT INTO policies VALUES ('p', 't', 'version', 'x', 'x', 'all_projects', NULL, 'framework', 1, '[]', "
                "'2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')")
    old.commit()
    old.close()
    conn = db.connect(path)
    assert db.get_policy(conn, "p").inputs == []
    conn.close()


# --- REQ-000000484: a check that runs the test suite records the Unit tests run ----------------

class _FakeOrchestration:
    def __init__(self):
        self.posted = []

    def __call__(self, socket_path, timeout=None):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, url, json=None):
        self.posted.append((url, json))
        return type("R", (), {"status_code": 200, "json": lambda self: {"id": "import-job"}})()


def test_a_junit_report_left_by_a_check_is_recorded_as_the_unit_tests_run(client, monkeypatch, tmp_path):
    _activate(client, "coverage", ["release"])
    orchestration = _FakeOrchestration()
    monkeypatch.setattr(app_module, "resolve_pillar_socket", lambda paths, pillar, **kw: tmp_path / f"{pillar}.sock")
    monkeypatch.setattr(app_module, "client_for_socket", orchestration)
    monkeypatch.setattr(app_module.sandbox, "run_policy_check",
                        lambda *a, **kw: SandboxResult(CheckOutcome.PASS, "ok", junit="<testsuite/>"))
    client.post("/v1/policies/evaluate", json={"project_id": "proj-a", "gate_point": "release"})
    [(url, job)] = orchestration.posted
    assert url == "/v1/jobs" and job["type"] == "requirements.import_junit" and job["project_id"] == "proj-a"
    assert job["params"] == {"suite_name": "Unit tests", "xml": "<testsuite/>"}


def test_a_check_without_a_report_records_no_test_run(client, monkeypatch, tmp_path):
    _activate(client, "rule", ["release"])
    orchestration = _FakeOrchestration()
    monkeypatch.setattr(app_module, "resolve_pillar_socket", lambda paths, pillar, **kw: tmp_path / f"{pillar}.sock")
    monkeypatch.setattr(app_module, "client_for_socket", orchestration)
    _counting_sandbox(monkeypatch)
    client.post("/v1/policies/evaluate", json={"project_id": "proj-a", "gate_point": "release"})
    assert orchestration.posted == []


class _ReportingRunner(_Runner):
    def run(self, argv, timeout=None, cwd=None):
        if argv[:2] == ["docker", "run"]:
            mount = next(a for a in argv if a.endswith(f":{sandbox.OUTPUT_DIR}"))
            (Path(mount.rsplit(":", 1)[0]) / sandbox.OUTPUT_JUNIT).write_text("<testsuite/>")
            assert f"POLICY_OUTPUT_DIR={sandbox.OUTPUT_DIR}" in argv
        return super().run(argv, timeout=timeout, cwd=cwd)


def test_the_sandbox_hands_back_the_report_a_check_wrote():
    assert sandbox.run_policy_check("def test_x(): pass", {}, runner=_ReportingRunner(), timeout=5.0).junit == "<testsuite/>"
    assert sandbox.run_policy_check("def test_x(): pass", {}, runner=_Runner(), timeout=5.0).junit is None
