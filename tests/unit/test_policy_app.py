import time

import threading

import pytest
from fastapi.testclient import TestClient

from common.paths import Paths
from policy import app as app_module
from policy import db
from policy.app import build_app
from policy.sandbox import SandboxResult, SandboxUnavailable
from policy.models import CheckOutcome, GatePoint


@pytest.fixture
def paths(tmp_path):
    p = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    p.ensure_layout()
    return p


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "policy.db")
    yield connection
    # A policy check's worker thread may still be writing its result: closing the connection under
    # it crashed the interpreter (a segfault in sqlite3, seen 2026-10-04), so let the workers end first.
    for thread in threading.enumerate():
        if "(worker)" in thread.name:
            thread.join(timeout=10)
    connection.close()


@pytest.fixture
def client(conn, paths, monkeypatch):
    def fake_query(paths, prompt, **kw):
        if "ENFORCEABLE:" in prompt:
            return "ENFORCEABLE: yes"
        if "NO_CONFLICT" in prompt:
            return "NO_CONFLICT"
        if "pytest test script" in prompt:
            return "def test_generated():\n    assert True\n"
        if "EARS_OK" in prompt:
            return "EARS_OK: WHEN x, the system shall y."
        return "unexpected prompt shape"

    monkeypatch.setattr(app_module.agent_client, "query", fake_query)
    monkeypatch.setattr(app_module.registry_client, "list_active_project_ids", lambda paths, **kw: ["proj-a", "proj-b"])
    monkeypatch.setattr(app_module.registry_client, "project_set_members", lambda paths, name, **kw: ["proj-a"] if name == "household" else [])
    return TestClient(build_app(conn, paths))


def _author(client, **overrides):
    params = {
        "ears_text": "when x happens, do y", "topic": "t", "target_pillar": "deployment",
        "scope_level": "all_projects", "created_by": "user",
    }
    params.update(overrides)
    return client.post("/v1/jobs/policy.author", json={"job_id": "j", "params": params})


def _activate(client, draft, **overrides):
    params = {**draft, "approved": True}
    params.update(overrides)
    return client.post("/v1/jobs/policy.activate", json={"job_id": "j", "params": params})


def test_health_endpoint(client):
    response = client.get("/v1/health")
    assert response.status_code == 200
    assert response.json()["ok"] is True


# -- policy.author -------------------------------------------------------------


def test_author_returns_a_draft_never_saves(client, conn):
    response = _author(client)
    assert response.json()["outcome"] == "succeeded"
    result = response.json()["result"]
    assert result["ears_text"] == "WHEN x, the system shall y."
    assert result["test_script"] == "def test_generated():\n    assert True"
    assert db.list_policies(conn) == []


def test_author_requires_the_core_fields(client):
    response = client.post("/v1/jobs/policy.author", json={"job_id": "j", "params": {"ears_text": "x"}})
    assert response.json()["outcome"] == "failed"


def test_author_rejects_invalid_scope_level(client):
    response = _author(client, scope_level="not-a-real-scope")
    assert response.json()["outcome"] == "failed"


def test_author_requires_scope_target_for_project_level(client):
    response = _author(client, scope_level="project")
    assert response.json()["outcome"] == "failed"
    assert "scope_target" in response.json()["error"]


def test_author_rejects_user_policy_targeting_policy_pillar_itself(client):
    response = _author(client, target_pillar="policy")
    assert response.json()["outcome"] == "failed"
    assert "never target Policy & Compliance itself" in response.json()["error"]


def test_author_allows_framework_policy_targeting_policy_pillar_itself(client, monkeypatch):
    response = _author(client, target_pillar="policy", created_by="framework")
    assert response.json()["outcome"] == "succeeded"


def test_author_gate2_rejects_unenforceable_requirements(client, monkeypatch):
    def fake_query(paths, prompt, **kw):
        if "ENFORCEABLE:" in prompt:
            return "ENFORCEABLE: no - needs subjective human judgment"
        return "EARS_OK: shall x"

    monkeypatch.setattr(app_module.agent_client, "query", fake_query)
    response = _author(client)
    assert response.json()["outcome"] == "failed"
    assert "Gate 2" in response.json()["error"]


def test_author_gate3a_rejects_exact_scope_duplicate(client, conn):
    first = _author(client)
    activated = _activate(client, first.json()["result"])
    assert activated.json()["outcome"] == "succeeded"

    second = _author(client)  # same topic, same scope_level=all_projects
    assert second.json()["outcome"] == "failed"
    assert "Gate 3a" in second.json()["error"]


def test_author_gate3a2_rejects_ownership_conflict(client, conn):
    first = _author(client, topic="shared-topic", target_pillar="deployment", scope_level="project", scope_target="proj-a")
    _activate(client, first.json()["result"])

    second = _author(client, topic="shared-topic", target_pillar="agent", scope_level="project", scope_target="proj-b")
    assert second.json()["outcome"] == "failed"
    assert "already targets" in second.json()["error"]


def test_author_gate3b_rejects_semantic_conflict(client, conn, monkeypatch):
    first = _author(client, topic="a")
    _activate(client, first.json()["result"])

    def fake_query(paths, prompt, **kw):
        if "ENFORCEABLE:" in prompt:
            return "ENFORCEABLE: yes"
        if "CONFLICT:" in prompt and "existing" in prompt.lower():
            return "CONFLICT: a - directly contradicts"
        if "pytest test script" in prompt:
            return "def test_x(): pass"
        return "EARS_OK: shall never x"

    monkeypatch.setattr(app_module.agent_client, "query", fake_query)
    second = _author(client, topic="b")
    assert second.json()["outcome"] == "failed"
    assert "Gate 3b" in second.json()["error"]


def test_author_editing_excludes_its_own_prior_version_from_gate3a(client, conn):
    first = _author(client)
    activated = _activate(client, first.json()["result"])
    policy_id = activated.json()["result"]["id"]

    edit_draft = _author(client, policy_id=policy_id)
    assert edit_draft.json()["outcome"] == "succeeded"  # not rejected by its own prior version


# -- policy.activate (Gate 4) ---------------------------------------------------


def test_activate_requires_approved_true(client, conn):
    draft = _author(client).json()["result"]
    response = client.post("/v1/jobs/policy.activate", json={"job_id": "j", "params": draft})
    assert response.json()["outcome"] == "failed"
    assert "Gate 4" in response.json()["error"]
    assert db.list_policies(conn) == []


def test_activate_saves_a_real_policy(client, conn):
    draft = _author(client).json()["result"]
    response = _activate(client, draft)
    assert response.json()["outcome"] == "succeeded"
    saved = db.get_policy(conn, response.json()["result"]["id"])
    assert saved is not None
    assert saved.ears_text == draft["ears_text"]


def test_activate_forces_framework_scope_to_all_projects(client, conn, monkeypatch):
    def fake_query(paths, prompt, **kw):
        if "ENFORCEABLE:" in prompt:
            return "ENFORCEABLE: yes"
        if "NO_CONFLICT" in prompt or "CONFLICT" in prompt:
            return "NO_CONFLICT"
        if "pytest test script" in prompt:
            return "def test_x(): pass"
        return "EARS_OK: shall x"

    monkeypatch.setattr(app_module.agent_client, "query", fake_query)
    draft = _author(client, target_pillar="policy", created_by="framework", scope_level="project", scope_target="proj-a").json()["result"]
    response = _activate(client, draft)
    assert response.json()["result"]["scope_level"] == "all_projects"
    assert response.json()["result"]["scope_target"] is None


def test_activate_edits_an_existing_policy_in_place(client, conn):
    draft = _author(client).json()["result"]
    first = _activate(client, draft)
    policy_id = first.json()["result"]["id"]

    edited_draft = dict(draft)
    edited_draft["ears_text"] = "WHEN x, the system shall z instead."
    second = _activate(client, edited_draft, policy_id=policy_id)
    assert second.json()["outcome"] == "succeeded"
    assert second.json()["result"]["id"] == policy_id
    assert len(db.list_policies(conn)) == 1
    assert db.get_policy(conn, policy_id).ears_text == "WHEN x, the system shall z instead."


def test_activate_edit_of_unknown_policy_id_fails_cleanly(client):
    draft = _author(client).json()["result"]
    response = _activate(client, draft, policy_id="ghost")
    assert response.json()["outcome"] == "failed"
    assert "not found" in response.json()["error"]


# -- policy.remove --------------------------------------------------------------


def test_remove_deletes_the_policy(client, conn):
    draft = _author(client).json()["result"]
    policy_id = _activate(client, draft).json()["result"]["id"]

    response = client.post("/v1/jobs/policy.remove", json={"job_id": "j", "params": {"policy_id": policy_id}})
    assert response.json()["outcome"] == "succeeded"
    assert db.get_policy(conn, policy_id) is None


def test_remove_unknown_id_fails_cleanly(client):
    response = client.post("/v1/jobs/policy.remove", json={"job_id": "j", "params": {"policy_id": "ghost"}})
    assert response.json()["outcome"] == "failed"


# -- policy.check_project --------------------------------------------------------


def _seed_policy(client, conn, **overrides):
    draft = _author(client, **overrides).json()["result"]
    return _activate(client, draft).json()["result"]


def _wait_for_check_result(conn, *, policy_id, project_id, timeout=5.0):
    """A real (non-skipped) check dispatches to a background thread and
    returns `accepted` immediately — tests poll the recorded result the
    same way a real caller (Orchestration, via the completion callback)
    would observe it land."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = db.latest_check_result(conn, policy_id=policy_id, project_id=project_id)
        if result is not None:
            return result
        time.sleep(0.02)
    raise AssertionError(f"no check result for policy={policy_id!r} project={project_id!r} within {timeout}s")


def test_check_project_records_a_pass(client, conn, monkeypatch):
    policy = _seed_policy(client, conn)
    monkeypatch.setattr(
        app_module, "resolve_project", lambda paths, project_id, **kw: {"path": "/tmp/whatever", "name": project_id}
    )
    monkeypatch.setattr(app_module.sandbox, "run_policy_check", lambda *a, **kw: SandboxResult(CheckOutcome.PASS, "ok"))

    response = client.post("/v1/jobs/policy.check_project", json={
        "job_id": "j", "project_id": "proj-a", "params": {"policy_id": policy["id"]},
    })
    assert response.json()["outcome"] == "accepted", response.json()  # dispatched to a background thread, not blocking
    result = _wait_for_check_result(conn, policy_id=policy["id"], project_id="proj-a")
    assert result.outcome == CheckOutcome.PASS


def test_check_project_records_a_fail_but_job_still_succeeds(client, conn, monkeypatch):
    policy = _seed_policy(client, conn)
    monkeypatch.setattr(app_module, "resolve_project", lambda paths, project_id, **kw: {"path": "/tmp/x", "name": project_id})
    monkeypatch.setattr(app_module.sandbox, "run_policy_check", lambda *a, **kw: SandboxResult(CheckOutcome.FAIL, "broke"))

    response = client.post("/v1/jobs/policy.check_project", json={
        "job_id": "j", "project_id": "proj-a", "params": {"policy_id": policy["id"]},
    })
    assert response.json()["outcome"] == "accepted"
    result = _wait_for_check_result(conn, policy_id=policy["id"], project_id="proj-a")
    assert result.outcome == CheckOutcome.FAIL  # a determined fail is still a completed check, not a Job failure


def test_check_project_reports_failed_to_orchestration_when_the_sandbox_never_completes(client, conn, monkeypatch):
    import json as json_module

    import httpx as httpx_module

    policy = _seed_policy(client, conn)
    monkeypatch.setattr(app_module, "resolve_project", lambda paths, project_id, **kw: {"path": "/tmp/x", "name": project_id})

    def boom(*a, **kw):
        raise SandboxUnavailable("timed out")

    monkeypatch.setattr(app_module.sandbox, "run_policy_check", boom)

    completions = []

    def handler(request):
        completions.append(request)
        return httpx_module.Response(200, json={"ok": True})

    monkeypatch.setattr(app_module, "resolve_pillar_socket", lambda paths, pillar, **kw: "fake-socket")
    monkeypatch.setattr(
        app_module, "client_for_socket",
        lambda socket_path, *, timeout=5.0: httpx_module.Client(transport=httpx_module.MockTransport(handler), base_url="http://pillar"),
    )

    response = client.post("/v1/jobs/policy.check_project", json={
        "job_id": "j", "project_id": "proj-a", "params": {"policy_id": policy["id"]},
    })
    assert response.json()["outcome"] == "accepted"  # dispatch itself always succeeds; the worker reports its own failure

    deadline = time.time() + 5.0
    while time.time() < deadline and not completions:
        time.sleep(0.02)
    assert len(completions) == 1
    payload = json_module.loads(completions[0].content)
    assert payload["succeeded"] is False
    assert "did not complete" in payload["error"]
    assert db.latest_check_result(conn, policy_id=policy["id"], project_id="proj-a") is None


def test_check_project_posts_succeeded_completion_to_orchestration(client, conn, monkeypatch):
    import json as json_module

    import httpx as httpx_module

    policy = _seed_policy(client, conn)
    monkeypatch.setattr(app_module, "resolve_project", lambda paths, project_id, **kw: {"path": "/tmp/x", "name": project_id})
    monkeypatch.setattr(app_module.sandbox, "run_policy_check", lambda *a, **kw: SandboxResult(CheckOutcome.PASS, "ok"))

    completions = []

    def handler(request):
        completions.append(request)
        return httpx_module.Response(200, json={"ok": True})

    monkeypatch.setattr(app_module, "resolve_pillar_socket", lambda paths, pillar, **kw: "fake-socket")
    monkeypatch.setattr(
        app_module, "client_for_socket",
        lambda socket_path, *, timeout=5.0: httpx_module.Client(transport=httpx_module.MockTransport(handler), base_url="http://pillar"),
    )

    response = client.post("/v1/jobs/policy.check_project", json={
        "job_id": "the-job-id", "project_id": "proj-a", "params": {"policy_id": policy["id"]},
    })
    assert response.json()["outcome"] == "accepted"

    deadline = time.time() + 5.0
    while time.time() < deadline and not completions:
        time.sleep(0.02)
    assert len(completions) == 1
    assert completions[0].url.path == "/v1/jobs/the-job-id/complete"
    payload = json_module.loads(completions[0].content)
    assert payload["succeeded"] is True
    assert payload["result"]["outcome"] == "pass"


def test_check_project_completion_is_a_noop_when_orchestration_unavailable(client, conn, monkeypatch):
    # No Core running at all -- resolve_pillar_socket returns None -- the
    # worker's best-effort completion callback must not raise, and the
    # check result itself (written before the callback) must still land.
    policy = _seed_policy(client, conn)
    monkeypatch.setattr(app_module, "resolve_project", lambda paths, project_id, **kw: {"path": "/tmp/x", "name": project_id})
    monkeypatch.setattr(app_module.sandbox, "run_policy_check", lambda *a, **kw: SandboxResult(CheckOutcome.PASS, "ok"))
    monkeypatch.setattr(app_module, "resolve_pillar_socket", lambda paths, pillar, **kw: None)

    response = client.post("/v1/jobs/policy.check_project", json={
        "job_id": "j", "project_id": "proj-a", "params": {"policy_id": policy["id"]},
    })
    assert response.json()["outcome"] == "accepted"
    result = _wait_for_check_result(conn, policy_id=policy["id"], project_id="proj-a")
    assert result.outcome == CheckOutcome.PASS


def test_check_project_fails_cleanly_for_unknown_policy(client):
    response = client.post("/v1/jobs/policy.check_project", json={
        "job_id": "j", "project_id": "proj-a", "params": {"policy_id": "ghost"},
    })
    assert response.json()["outcome"] == "failed"


def test_check_project_requires_project_id(client, conn):
    policy = _seed_policy(client, conn)
    response = client.post("/v1/jobs/policy.check_project", json={"job_id": "j", "params": {"policy_id": policy["id"]}})
    assert response.json()["outcome"] == "failed"


def _init_git_repo(repo_dir):
    import subprocess
    repo_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo_dir, check=True)
    (repo_dir / "README.md").write_text("hi\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo_dir, check=True)


def _wait_for_calls(calls, n, *, timeout=5.0):
    """A real (non-skipped) check now dispatches to a background thread —
    a caller issuing a second request right after the first must wait for
    that thread's own `run_policy_check` call to actually land before the
    fingerprint it wrote is there to be skipped against."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if len(calls) >= n:
            return
        time.sleep(0.02)
    raise AssertionError(f"expected {n} sandbox call(s), only saw {len(calls)} within {timeout}s")


def test_check_project_skips_the_sandbox_when_nothing_changed(client, conn, monkeypatch, tmp_path):
    repo_dir = tmp_path / "proj"
    _init_git_repo(repo_dir)
    policy = _seed_policy(client, conn)
    monkeypatch.setattr(app_module, "resolve_project", lambda paths, project_id, **kw: {"path": str(repo_dir), "name": project_id})
    calls = []
    monkeypatch.setattr(
        app_module.sandbox, "run_policy_check",
        lambda *a, **kw: calls.append(1) or SandboxResult(CheckOutcome.PASS, "ok"),
    )

    body = {"job_id": "j", "project_id": "proj-a", "params": {"policy_id": policy["id"]}}
    first = client.post("/v1/jobs/policy.check_project", json=body)
    assert first.json()["outcome"] == "accepted"
    _wait_for_check_result(conn, policy_id=policy["id"], project_id="proj-a")
    second = client.post("/v1/jobs/policy.check_project", json=body)

    assert len(calls) == 1  # the sandbox only actually ran once
    assert second.json()["outcome"] == "succeeded"  # skipped — resolved synchronously, no thread involved
    assert second.json()["result"]["outcome"] == "pass"
    assert second.json().get("skipped") == "unchanged"
    # both runs are still recorded, for history/job-linkage purposes
    assert len(db.list_check_results(conn, policy_id=policy["id"], project_id="proj-a")) == 2


def test_check_project_re_runs_after_a_new_commit(client, conn, monkeypatch, tmp_path):
    import subprocess
    repo_dir = tmp_path / "proj"
    _init_git_repo(repo_dir)
    policy = _seed_policy(client, conn)
    monkeypatch.setattr(app_module, "resolve_project", lambda paths, project_id, **kw: {"path": str(repo_dir), "name": project_id})
    calls = []
    monkeypatch.setattr(
        app_module.sandbox, "run_policy_check",
        lambda *a, **kw: calls.append(1) or SandboxResult(CheckOutcome.PASS, "ok"),
    )

    body = {"job_id": "j", "project_id": "proj-a", "params": {"policy_id": policy["id"]}}
    client.post("/v1/jobs/policy.check_project", json=body)
    _wait_for_calls(calls, 1)
    (repo_dir / "README.md").write_text("changed\n", encoding="utf-8")
    subprocess.run(["git", "commit", "-aq", "-m", "change"], cwd=repo_dir, check=True)
    client.post("/v1/jobs/policy.check_project", json=body)
    _wait_for_calls(calls, 2)

    assert len(calls) == 2  # a new HEAD commit is a real change — re-checked


def test_check_project_re_runs_after_the_policy_itself_is_edited(client, conn, monkeypatch, tmp_path):
    repo_dir = tmp_path / "proj"
    _init_git_repo(repo_dir)
    policy = _seed_policy(client, conn)
    monkeypatch.setattr(app_module, "resolve_project", lambda paths, project_id, **kw: {"path": str(repo_dir), "name": project_id})
    calls = []
    monkeypatch.setattr(
        app_module.sandbox, "run_policy_check",
        lambda *a, **kw: calls.append(1) or SandboxResult(CheckOutcome.PASS, "ok"),
    )

    body = {"job_id": "j", "project_id": "proj-a", "params": {"policy_id": policy["id"]}}
    client.post("/v1/jobs/policy.check_project", json=body)
    _wait_for_calls(calls, 1)
    _activate(client, {**policy, "ears_text": "when z happens, do w"}, policy_id=policy["id"])
    client.post("/v1/jobs/policy.check_project", json=body)
    _wait_for_calls(calls, 2)

    assert len(calls) == 2  # editing the policy is a real change too — re-checked


def test_check_project_force_bypasses_the_unchanged_skip(client, conn, monkeypatch, tmp_path):
    repo_dir = tmp_path / "proj"
    _init_git_repo(repo_dir)
    policy = _seed_policy(client, conn)
    monkeypatch.setattr(app_module, "resolve_project", lambda paths, project_id, **kw: {"path": str(repo_dir), "name": project_id})
    calls = []
    monkeypatch.setattr(
        app_module.sandbox, "run_policy_check",
        lambda *a, **kw: calls.append(1) or SandboxResult(CheckOutcome.PASS, "ok"),
    )

    client.post("/v1/jobs/policy.check_project", json={
        "job_id": "j", "project_id": "proj-a", "params": {"policy_id": policy["id"]},
    })
    _wait_for_calls(calls, 1)
    client.post("/v1/jobs/policy.check_project", json={
        "job_id": "j", "project_id": "proj-a", "params": {"policy_id": policy["id"], "force": True},
    })
    _wait_for_calls(calls, 2)

    assert len(calls) == 2  # force always re-runs, regardless of the fingerprint


# -- reads ----------------------------------------------------------------


def test_list_and_get_policy(client, conn):
    policy = _seed_policy(client, conn)
    listed = client.get("/v1/policies").json()
    assert [p["id"] for p in listed] == [policy["id"]]

    fetched = client.get(f"/v1/policies/{policy['id']}").json()
    assert fetched["id"] == policy["id"]


def test_get_policy_404s_for_unknown_id(client):
    assert client.get("/v1/policies/ghost").status_code == 404


def test_winning_policy_endpoint(client, conn, monkeypatch):
    monkeypatch.setattr(app_module.registry_client, "project_sets_containing", lambda paths, project_id, **kw: [])
    policy = _seed_policy(client, conn)
    response = client.get("/v1/policies/winning", params={"topic": "t", "project_id": "proj-a"})
    assert response.json()["policy"]["id"] == policy["id"]


def test_winning_policy_endpoint_none_when_nothing_governs(client, monkeypatch):
    monkeypatch.setattr(app_module.registry_client, "project_sets_containing", lambda paths, project_id, **kw: [])
    response = client.get("/v1/policies/winning", params={"topic": "ghost-topic", "project_id": "proj-a"})
    assert response.json()["policy"] is None


def test_check_results_list_and_latest(client, conn, monkeypatch):
    policy = _seed_policy(client, conn)
    monkeypatch.setattr(app_module, "resolve_project", lambda paths, project_id, **kw: {"path": "/tmp/x", "name": project_id})
    monkeypatch.setattr(app_module.sandbox, "run_policy_check", lambda *a, **kw: SandboxResult(CheckOutcome.PASS, "ok"))
    client.post("/v1/jobs/policy.check_project", json={"job_id": "j", "project_id": "proj-a", "params": {"policy_id": policy["id"]}})
    _wait_for_check_result(conn, policy_id=policy["id"], project_id="proj-a")

    results = client.get("/v1/policy_check_results", params={"policy_id": policy["id"]}).json()
    assert len(results) == 1

    latest = client.get("/v1/policy_check_results/latest", params={"policy_id": policy["id"], "project_id": "proj-a"}).json()
    assert latest["result"]["outcome"] == "pass"


def test_latest_check_result_is_none_before_any_check(client, conn):
    policy = _seed_policy(client, conn)
    latest = client.get("/v1/policy_check_results/latest", params={"policy_id": policy["id"], "project_id": "proj-a"}).json()
    assert latest["result"] is None


# -- gate_points at authoring time ------------------------------------------


def test_author_passes_through_requested_gate_points(client):
    response = _author(client, gate_points=["server_launch", "release"])
    assert response.json()["result"]["gate_points"] == ["server_launch", "release"]


def test_author_defaults_gate_points_to_empty(client):
    response = _author(client)
    assert response.json()["result"]["gate_points"] == []


def test_author_rejects_an_invalid_gate_point(client):
    response = _author(client, gate_points=["not-a-real-category"])
    assert response.json()["outcome"] == "failed"
    assert "gate_point" in response.json()["error"]


def test_activate_saves_the_requested_gate_points(client, conn):
    draft = _author(client, gate_points=["build_install"]).json()["result"]
    response = _activate(client, draft)
    assert response.json()["result"]["gate_points"] == ["build_install"]
    assert db.get_policy(conn, response.json()["result"]["id"]).gate_points == [GatePoint.BUILD_INSTALL]


def test_activate_edit_without_gate_points_preserves_the_existing_ones(client, conn):
    draft = _author(client, gate_points=["release"]).json()["result"]
    policy_id = _activate(client, draft).json()["result"]["id"]

    edited = dict(draft)
    edited.pop("gate_points")
    edited["ears_text"] = "WHEN x, the system shall z instead."
    response = _activate(client, edited, policy_id=policy_id)
    assert response.json()["result"]["gate_points"] == ["release"]


def test_activate_edit_can_explicitly_change_gate_points(client, conn):
    draft = _author(client, gate_points=["release"]).json()["result"]
    policy_id = _activate(client, draft).json()["result"]["id"]

    edited = dict(draft)
    edited["gate_points"] = ["branch_finish"]
    response = _activate(client, edited, policy_id=policy_id)
    assert response.json()["result"]["gate_points"] == ["branch_finish"]


# -- project_registration is framework-bound only (OQ-15, resolved 2026-09-17) --


def test_author_rejects_project_registration_on_a_project_bound_target(client):
    response = _author(client, target_pillar="registry", gate_points=["project_registration"])
    assert response.json()["outcome"] == "failed"
    assert "project_registration" in response.json()["error"]
    assert "framework-bound" in response.json()["error"]


def test_author_accepts_project_registration_when_framework_bound(client):
    response = _author(
        client, target_pillar="policy", created_by="framework", gate_points=["project_registration"],
    )
    assert response.json()["outcome"] == "succeeded"
    assert response.json()["result"]["gate_points"] == ["project_registration"]


def test_activate_rejects_project_registration_on_a_project_bound_target(client):
    draft = _author(client, target_pillar="registry", gate_points=[]).json()["result"]
    draft["gate_points"] = ["project_registration"]
    response = _activate(client, draft)
    assert response.json()["outcome"] == "failed"
    assert "project_registration" in response.json()["error"]


def test_configure_rejects_project_registration_on_a_project_bound_policy(client, conn):
    draft = _author(client, target_pillar="registry", gate_points=[]).json()["result"]
    policy_id = _activate(client, draft).json()["result"]["id"]

    response = client.post(
        "/v1/jobs/policy.configure",
        json={"job_id": "j", "params": {"policy_id": policy_id, "gate_points": ["project_registration"]}},
    )
    assert response.json()["outcome"] == "failed"
    assert "project_registration" in response.json()["error"]
    # never partially applied
    assert db.get_policy(conn, policy_id).gate_points == []


def test_configure_accepts_project_registration_on_a_framework_bound_policy(client, conn):
    draft = _author(client, target_pillar="policy", created_by="framework", gate_points=[]).json()["result"]
    policy_id = _activate(client, draft).json()["result"]["id"]

    response = client.post(
        "/v1/jobs/policy.configure",
        json={"job_id": "j", "params": {"policy_id": policy_id, "gate_points": ["project_registration"]}},
    )
    assert response.json()["outcome"] == "succeeded"
    assert db.get_policy(conn, policy_id).gate_points == [GatePoint.PROJECT_REGISTRATION]


# -- policy.configure: enable/disable and gate_points, no re-authoring --------


def test_configure_toggles_enabled(client, conn):
    policy = _seed_policy(client, conn)
    response = client.post("/v1/jobs/policy.configure", json={"job_id": "j", "params": {"policy_id": policy["id"], "enabled": False}})
    assert response.json()["outcome"] == "succeeded"
    assert response.json()["result"]["enabled"] is False


def test_configure_sets_gate_points(client, conn):
    policy = _seed_policy(client, conn)
    response = client.post("/v1/jobs/policy.configure", json={
        "job_id": "j", "params": {"policy_id": policy["id"], "gate_points": ["server_launch", "project_removal"]},
    })
    assert response.json()["outcome"] == "succeeded"
    assert set(response.json()["result"]["gate_points"]) == {"server_launch", "project_removal"}


def test_configure_never_touches_ears_text_or_script(client, conn):
    policy = _seed_policy(client, conn)
    client.post("/v1/jobs/policy.configure", json={"job_id": "j", "params": {"policy_id": policy["id"], "enabled": False}})
    fetched = db.get_policy(conn, policy["id"])
    assert fetched.ears_text == policy["ears_text"]
    assert fetched.test_script == policy["test_script"]


def test_configure_rejects_an_invalid_gate_point(client, conn):
    policy = _seed_policy(client, conn)
    response = client.post("/v1/jobs/policy.configure", json={
        "job_id": "j", "params": {"policy_id": policy["id"], "gate_points": ["not-real"]},
    })
    assert response.json()["outcome"] == "failed"


def test_configure_fails_cleanly_for_unknown_policy(client):
    response = client.post("/v1/jobs/policy.configure", json={"job_id": "j", "params": {"policy_id": "ghost", "enabled": False}})
    assert response.json()["outcome"] == "failed"


def test_disabled_policy_never_wins_precedence(client, conn, monkeypatch):
    monkeypatch.setattr(app_module.registry_client, "project_sets_containing", lambda paths, project_id, **kw: [])
    policy = _seed_policy(client, conn)
    client.post("/v1/jobs/policy.configure", json={"job_id": "j", "params": {"policy_id": policy["id"], "enabled": False}})

    winning = client.get("/v1/policies/winning", params={"topic": policy["topic"], "project_id": "proj-a"}).json()
    assert winning["policy"] is None


# -- project selection ---------------------------------------------------


def _set_projects(client, policy_id, projects):
    return client.post("/v1/jobs/policy.set_projects", json={"job_id": "j", "params": {"policy_id": policy_id, "projects": projects}})


def test_activating_seeds_the_selection_from_the_authoring_scope(client, conn):
    all_projects = _seed_policy(client, conn, topic="a")
    assert {p["project_id"] for p in all_projects["projects"]} == {"proj-a", "proj-b"}
    assert all(p["gating_enabled"] for p in all_projects["projects"])

    single = _seed_policy(client, conn, topic="b", scope_level="project", scope_target="proj-b")
    assert [p["project_id"] for p in single["projects"]] == ["proj-b"]

    in_set = _seed_policy(client, conn, topic="c", scope_level="project_set", scope_target="household")
    assert [p["project_id"] for p in in_set["projects"]] == ["proj-a"]


def test_a_policy_is_left_unseeded_while_the_registry_is_unreachable_and_seeded_later(client, conn, monkeypatch):
    from policy.registry_client import RegistryReadError

    def down(paths, **kw):
        raise RegistryReadError("registry down")

    monkeypatch.setattr(app_module.registry_client, "list_active_project_ids", down)
    policy = _seed_policy(client, conn)
    assert policy["projects"] == []

    monkeypatch.setattr(app_module.registry_client, "list_active_project_ids", lambda paths, **kw: ["proj-a"])
    client.get("/v1/policies/gating-for-job", params={"project_id": "proj-a", "categories": "release"})
    assert [p["project_id"] for p in client.get(f"/v1/policies/{policy['id']}").json()["projects"]] == ["proj-a"]


def test_set_projects_replaces_the_selection_and_records_per_project_gating(client, conn):
    policy = _seed_policy(client, conn)
    response = _set_projects(client, policy["id"], [
        {"project_id": "proj-b", "gating_enabled": False}, {"project_id": "proj-c"}, "proj-d",
    ])
    assert response.json()["outcome"] == "succeeded"
    projects = {p["project_id"]: p["gating_enabled"] for p in response.json()["result"]["projects"]}
    assert projects == {"proj-b": False, "proj-c": True, "proj-d": True}
    assert "proj-a" not in projects


def test_set_projects_can_deselect_everything_and_it_sticks(client, conn):
    policy = _seed_policy(client, conn)
    assert _set_projects(client, policy["id"], []).json()["result"]["projects"] == []
    assert client.get(f"/v1/policies/{policy['id']}").json()["projects"] == []


def test_a_deselected_selection_survives_a_restart_of_the_pillar(client, conn, paths):
    policy = _seed_policy(client, conn)
    _set_projects(client, policy["id"], [])
    restarted = TestClient(build_app(conn, paths))  # a fresh app on the same database re-runs startup seeding
    assert restarted.get(f"/v1/policies/{policy['id']}").json()["projects"] == []


def test_set_projects_validates_its_input(client, conn):
    policy = _seed_policy(client, conn)
    assert "missing 'policy_id'" in client.post("/v1/jobs/policy.set_projects", json={"job_id": "j", "params": {"projects": []}}).json()["error"]
    assert "must be a list" in _set_projects(client, policy["id"], "proj-a").json()["error"]
    assert "invalid project entry" in _set_projects(client, policy["id"], [{"gating_enabled": True}]).json()["error"]
    assert "invalid project entry" in _set_projects(client, policy["id"], [42]).json()["error"]
    assert "not found" in _set_projects(client, "ghost", []).json()["error"]


def test_list_policy_projects_filters_by_policy_and_project(client, conn):
    policy = _seed_policy(client, conn)
    _set_projects(client, policy["id"], [{"project_id": "proj-a"}, {"project_id": "proj-b", "gating_enabled": False}])
    listed = client.get("/v1/policy_projects", params={"policy_id": policy["id"]}).json()
    assert {(r["project_id"], r["gating_enabled"]) for r in listed} == {("proj-a", True), ("proj-b", False)}
    only_b = client.get("/v1/policy_projects", params={"project_id": "proj-b"}).json()
    assert [r["policy_id"] for r in only_b] == [policy["id"]]


def test_check_all_fans_out_over_the_selected_projects_only(client, conn, monkeypatch):
    policy = _seed_policy(client, conn)
    _set_projects(client, policy["id"], [{"project_id": "proj-b"}])
    monkeypatch.setattr("common.pillar_socket.resolve_pillar_socket", lambda paths, name, timeout=5.0: None)
    result = client.post("/v1/jobs/policy.check_all", json={"job_id": "j", "params": {"policy_id": policy["id"]}}).json()["result"]
    assert result["project_ids"] == ["proj-b"]


# -- gating-for-job -------------------------------------------------------


def test_gating_for_job_returns_winning_policies_matching_the_requested_categories(client, conn, monkeypatch):
    monkeypatch.setattr(app_module.registry_client, "project_sets_containing", lambda paths, project_id, **kw: [])
    draft = _author(client, topic="a", gate_points=["server_launch"]).json()["result"]
    policy_a = _activate(client, draft).json()["result"]

    draft_b = _author(client, topic="b", gate_points=["release"]).json()["result"]
    policy_b = _activate(client, draft_b).json()["result"]

    matching = client.get("/v1/policies/gating-for-job", params={"project_id": "proj-a", "categories": "server_launch"}).json()
    assert [p["id"] for p in matching] == [policy_a["id"]]

    both = client.get("/v1/policies/gating-for-job", params={"project_id": "proj-a", "categories": "server_launch,release"}).json()
    assert {p["id"] for p in both} == {policy_a["id"], policy_b["id"]}


def test_gating_for_job_excludes_a_policy_that_does_not_gate_that_category(client, conn):
    _seed_policy(client, conn)  # gate_points defaults to []
    matching = client.get("/v1/policies/gating-for-job", params={"project_id": "proj-a", "categories": "server_launch"}).json()
    assert matching == []


def test_gating_for_job_excludes_a_project_the_policy_is_not_selected_for(client, conn):
    draft = _author(client, gate_points=["server_launch"]).json()["result"]
    policy = _activate(client, draft).json()["result"]
    _set_projects(client, policy["id"], [{"project_id": "proj-b"}])

    assert client.get("/v1/policies/gating-for-job", params={"project_id": "proj-a", "categories": "server_launch"}).json() == []
    elsewhere = client.get("/v1/policies/gating-for-job", params={"project_id": "proj-b", "categories": "server_launch"}).json()
    assert [p["id"] for p in elsewhere] == [policy["id"]]


def test_gating_for_job_still_returns_a_policy_whose_gating_is_off_so_it_runs_for_analysis(client, conn):
    draft = _author(client, gate_points=["server_launch"]).json()["result"]
    policy = _activate(client, draft).json()["result"]
    _set_projects(client, policy["id"], [{"project_id": "proj-a", "gating_enabled": False}, {"project_id": "proj-b"}])

    a = client.get("/v1/policies/gating-for-job", params={"project_id": "proj-a", "categories": "server_launch"}).json()
    b = client.get("/v1/policies/gating-for-job", params={"project_id": "proj-b", "categories": "server_launch"}).json()
    assert [(p["id"], p["gating"]) for p in a] == [(policy["id"], False)]
    assert [(p["id"], p["gating"]) for p in b] == [(policy["id"], True)]


# -- drafts: review and approval ------------------------------------------------


def _draft(**overrides):
    base = {
        "topic": "version-format", "target_pillar": "version", "ears_text": "The VERSION file shall be semver.",
        "ears_refined": True, "test_script": "def test_x():\n    assert True\n", "scope_level": "all_projects",
        "created_by": "framework", "gate_points": [], "source": "little-versions", "notes": "Straightforward.", "concerns": False,
    }
    base.update(overrides)
    return base


def _import(client, *drafts):
    return client.post("/v1/jobs/policy.draft_import", json={"job_id": "j", "params": {"drafts": list(drafts)}})


def _approve(client, draft_id, **params):
    return client.post("/v1/jobs/policy.approve_draft", json={"job_id": "j", "params": {"draft_id": draft_id, **params}})


def _pending(client):
    return client.get("/v1/policy_drafts", params={"status": "pending"}).json()


def test_import_stores_pending_drafts_that_enforce_nothing(client, conn):
    response = _import(client, _draft(), _draft(topic="production-tag-format", concerns=True, notes="audits every tag"))
    assert response.json()["result"] == {"created": ["version-format", "production-tag-format"], "updated": [], "skipped": []}

    drafts = _pending(client)
    assert [d["topic"] for d in drafts] == ["production-tag-format", "version-format"] or {d["topic"] for d in drafts} == {"version-format", "production-tag-format"}
    by_topic = {d["topic"]: d for d in drafts}
    assert by_topic["version-format"]["status"] == "pending" and by_topic["version-format"]["source"] == "little-versions"
    assert by_topic["production-tag-format"]["concerns"] is True
    assert client.get("/v1/policies").json() == []  # a draft is not a policy


def test_reimporting_refreshes_a_pending_draft_instead_of_duplicating_it(client, conn):
    _import(client, _draft())
    first_id = _pending(client)[0]["id"]
    again = _import(client, _draft(ears_text="Reworded.", notes="new notes")).json()["result"]
    assert again == {"created": [], "updated": ["version-format"], "skipped": []}
    drafts = _pending(client)
    assert len(drafts) == 1 and drafts[0]["id"] == first_id and drafts[0]["ears_text"] == "Reworded."


def test_import_skips_a_topic_that_already_has_a_policy(client, conn):
    _seed_policy(client, conn, topic="version-format")
    assert _import(client, _draft()).json()["result"]["skipped"] == [
        {"topic": "version-format", "reason": "a policy already exists for this topic"}
    ]
    assert _pending(client) == []


def test_import_validates_its_input(client):
    assert "non-empty list" in client.post("/v1/jobs/policy.draft_import", json={"job_id": "j", "params": {}}).json()["error"]
    assert "invalid draft entry" in _import(client, "nope").json()["error"]
    assert "missing 'test_script'" in _import(client, {k: v for k, v in _draft().items() if k != "test_script"}).json()["error"]
    assert "invalid gate_point" in _import(client, _draft(gate_points=["nowhere"])).json()["error"]


def test_approving_needs_the_reviewers_explicit_confirmation(client, conn):
    _import(client, _draft())
    draft_id = _pending(client)[0]["id"]
    assert "explicit confirmation" in _approve(client, draft_id, projects=[]).json()["error"]
    assert "explicit confirmation" in _approve(client, draft_id, reviewed="yes", projects=[]).json()["error"]
    assert client.get("/v1/policies").json() == []
    assert _pending(client)[0]["status"] == "pending"


def test_approving_creates_a_real_policy_with_the_chosen_gate_points_and_projects(client, conn):
    _import(client, _draft())
    draft_id = _pending(client)[0]["id"]
    response = _approve(
        client, draft_id, reviewed=True, gate_points=["branch_finish"],
        projects=[{"project_id": "proj-a"}, {"project_id": "proj-b", "gating_enabled": False}],
    )
    policy = response.json()["result"]
    assert response.json()["outcome"] == "succeeded" and policy["draft_id"] == draft_id
    assert policy["topic"] == "version-format" and policy["enabled"] is True
    assert policy["gate_points"] == ["branch_finish"] and policy["created_by"] == "framework"
    assert {(p["project_id"], p["gating_enabled"]) for p in policy["projects"]} == {("proj-a", True), ("proj-b", False)}

    listed = client.get("/v1/policies").json()
    assert [p["topic"] for p in listed] == ["version-format"]
    assert listed[0]["test_script"] == "def test_x():\n    assert True\n"

    decided = client.get(f"/v1/policy_drafts/{draft_id}").json()
    assert decided["status"] == "approved" and decided["policy_id"] == policy["id"] and decided["decided_at"]
    assert _pending(client) == []


def test_approving_keeps_the_drafts_own_gate_points_when_none_are_chosen(client, conn):
    _import(client, _draft(gate_points=["release"]))
    draft_id = _pending(client)[0]["id"]
    assert _approve(client, draft_id, reviewed=True, projects=[]).json()["result"]["gate_points"] == ["release"]


def test_approving_with_no_projects_is_allowed_and_leaves_the_policy_applying_to_nothing(client, conn):
    _import(client, _draft())
    result = _approve(client, _pending(client)[0]["id"], reviewed=True, projects=[]).json()["result"]
    assert result["projects"] == []


def test_approval_runs_the_same_gate_4_checks_as_activation(client, conn):
    _import(client, _draft(topic="registration-rule", target_pillar="deployment"))
    draft_id = _pending(client)[0]["id"]
    error = _approve(client, draft_id, reviewed=True, gate_points=["project_registration"], projects=[]).json()["error"]
    assert "project_registration" in error and "framework-bound" in error
    assert _pending(client)[0]["status"] == "pending" and client.get("/v1/policies").json() == []


def test_approving_fails_on_a_duplicate_scope_and_topic_and_leaves_the_draft_pending(client, conn):
    _import(client, _draft(topic="taken", created_by="framework"))
    draft_id = _pending(client)[0]["id"]
    # a policy for the same topic appears after the draft was imported
    _seed_policy(client, conn, topic="taken", created_by="framework", scope_level="all_projects")
    result = _approve(client, draft_id, reviewed=True, projects=[]).json()
    assert result["outcome"] == "failed" and "Gate 3a" in result["error"]
    assert client.get(f"/v1/policy_drafts/{draft_id}").json()["status"] == "pending"


def test_approve_validates_ids_state_and_projects(client, conn):
    assert "missing 'draft_id'" in client.post("/v1/jobs/policy.approve_draft", json={"job_id": "j", "params": {}}).json()["error"]
    assert "not found" in _approve(client, "ghost", reviewed=True, projects=[]).json()["error"]
    _import(client, _draft())
    draft_id = _pending(client)[0]["id"]
    assert "must be a list" in _approve(client, draft_id, reviewed=True, projects="proj-a").json()["error"]
    assert "invalid gate_point" in _approve(client, draft_id, reviewed=True, gate_points=["x"], projects=[]).json()["error"]
    _approve(client, draft_id, reviewed=True, projects=[])
    assert "not pending" in _approve(client, draft_id, reviewed=True, projects=[]).json()["error"]  # cannot approve twice


def test_reject_and_restore_a_draft(client, conn):
    _import(client, _draft())
    draft_id = _pending(client)[0]["id"]
    rejected = client.post("/v1/jobs/policy.reject_draft", json={"job_id": "j", "params": {"draft_id": draft_id, "reason": "  too vague  "}}).json()
    assert rejected["result"]["status"] == "rejected" and rejected["result"]["reject_reason"] == "too vague"
    assert _pending(client) == []
    assert [d["id"] for d in client.get("/v1/policy_drafts", params={"status": "rejected"}).json()] == [draft_id]
    assert "not pending" in client.post("/v1/jobs/policy.reject_draft", json={"job_id": "j", "params": {"draft_id": draft_id}}).json()["error"]
    assert "not pending" in _approve(client, draft_id, reviewed=True, projects=[]).json()["error"]

    restored = client.post("/v1/jobs/policy.restore_draft", json={"job_id": "j", "params": {"draft_id": draft_id}}).json()
    assert restored["result"]["status"] == "pending" and restored["result"]["decided_at"] is None
    assert "only a rejected draft" in client.post("/v1/jobs/policy.restore_draft", json={"job_id": "j", "params": {"draft_id": draft_id}}).json()["error"]


def test_reject_and_restore_validate_their_input(client):
    for job in ("policy.reject_draft", "policy.restore_draft"):
        assert "missing 'draft_id'" in client.post(f"/v1/jobs/{job}", json={"job_id": "j", "params": {}}).json()["error"]
        assert "not found" in client.post(f"/v1/jobs/{job}", json={"job_id": "j", "params": {"draft_id": "ghost"}}).json()["error"]


def test_draft_reads(client):
    _import(client, _draft())
    draft_id = _pending(client)[0]["id"]
    assert client.get(f"/v1/policy_drafts/{draft_id}").json()["topic"] == "version-format"
    assert client.get("/v1/policy_drafts/ghost").status_code == 404
    assert client.get("/v1/policy_drafts", params={"status": "bogus"}).status_code == 400
    assert len(client.get("/v1/policy_drafts").json()) == 1


def test_author_can_save_its_result_as_a_draft_for_review(client, conn):
    response = _author(client, topic="authored-topic", save_draft=True, source="a person")
    result = response.json()["result"]
    assert result["draft_id"]
    drafts = _pending(client)
    assert [d["topic"] for d in drafts] == ["authored-topic"] and drafts[0]["source"] == "a person"
    assert drafts[0]["test_script"].startswith("def test_generated")
    assert client.get("/v1/policies").json() == []  # still nothing enforced

    # authoring the same topic again refreshes the open draft
    again = _author(client, topic="authored-topic", save_draft=True).json()["result"]
    assert again["draft_id"] == result["draft_id"] and len(_pending(client)) == 1


def test_author_without_save_draft_still_never_saves_anything(client, conn):
    assert "draft_id" not in _author(client).json()["result"]
    assert _pending(client) == []


def test_import_stores_a_drafts_own_recommended_project_selection(client, conn):
    _import(client, _draft(projects=[{"project_id": "little-home", "gating_enabled": True}, {"project_id": "little-spectrum", "gating_enabled": False}]))
    draft = _pending(client)[0]
    assert {(p["project_id"], p["gating_enabled"]) for p in draft["projects"]} == {("little-home", True), ("little-spectrum", False)}


def test_a_draft_with_no_projects_key_has_an_empty_selection(client, conn):
    _import(client, _draft())
    assert _pending(client)[0]["projects"] == []


def test_reimporting_replaces_the_project_selection(client, conn):
    _import(client, _draft(projects=[{"project_id": "little-home"}]))
    _import(client, _draft(projects=[{"project_id": "little-meals", "gating_enabled": False}]))
    draft = _pending(client)[0]
    assert [(p["project_id"], p["gating_enabled"]) for p in draft["projects"]] == [("little-meals", False)]


def test_import_rejects_an_invalid_project_entry(client):
    assert "invalid project entry" in _import(client, _draft(projects=["", {"no_project_id": 1}])).json()["error"]
    assert "invalid project entry" in _import(client, _draft(projects=[{"no_project_id": 1}])).json()["error"]
