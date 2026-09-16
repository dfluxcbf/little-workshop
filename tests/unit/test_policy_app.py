import pytest
from fastapi.testclient import TestClient

from common.paths import Paths
from policy import app as app_module
from policy import db
from policy.app import build_app
from policy.sandbox import SandboxResult, SandboxUnavailable
from policy.models import CheckOutcome


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


def test_check_project_records_a_pass(client, conn, monkeypatch):
    policy = _seed_policy(client, conn)
    monkeypatch.setattr(
        app_module, "resolve_project", lambda paths, project_id, **kw: {"path": "/tmp/whatever", "name": project_id}
    )
    monkeypatch.setattr(app_module.sandbox, "run_policy_check", lambda *a, **kw: SandboxResult(CheckOutcome.PASS, "ok"))

    response = client.post("/v1/jobs/policy.check_project", json={
        "job_id": "j", "project_id": "proj-a", "params": {"policy_id": policy["id"]},
    })
    assert response.json()["outcome"] == "succeeded"
    assert response.json()["result"]["outcome"] == "pass"
    assert db.latest_check_result(conn, policy_id=policy["id"], project_id="proj-a").outcome == CheckOutcome.PASS


def test_check_project_records_a_fail_but_job_still_succeeds(client, conn, monkeypatch):
    policy = _seed_policy(client, conn)
    monkeypatch.setattr(app_module, "resolve_project", lambda paths, project_id, **kw: {"path": "/tmp/x", "name": project_id})
    monkeypatch.setattr(app_module.sandbox, "run_policy_check", lambda *a, **kw: SandboxResult(CheckOutcome.FAIL, "broke"))

    response = client.post("/v1/jobs/policy.check_project", json={
        "job_id": "j", "project_id": "proj-a", "params": {"policy_id": policy["id"]},
    })
    assert response.json()["outcome"] == "succeeded"  # the Job itself succeeded — a fail is still a determined verdict
    assert response.json()["result"]["outcome"] == "fail"


def test_check_project_fails_the_job_when_the_sandbox_never_completes(client, conn, monkeypatch):
    policy = _seed_policy(client, conn)
    monkeypatch.setattr(app_module, "resolve_project", lambda paths, project_id, **kw: {"path": "/tmp/x", "name": project_id})

    def boom(*a, **kw):
        raise SandboxUnavailable("timed out")

    monkeypatch.setattr(app_module.sandbox, "run_policy_check", boom)
    response = client.post("/v1/jobs/policy.check_project", json={
        "job_id": "j", "project_id": "proj-a", "params": {"policy_id": policy["id"]},
    })
    assert response.json()["outcome"] == "failed"
    assert "did not complete" in response.json()["error"]
    assert db.latest_check_result(conn, policy_id=policy["id"], project_id="proj-a") is None


def test_check_project_fails_cleanly_for_unknown_policy(client):
    response = client.post("/v1/jobs/policy.check_project", json={
        "job_id": "j", "project_id": "proj-a", "params": {"policy_id": "ghost"},
    })
    assert response.json()["outcome"] == "failed"


def test_check_project_requires_project_id(client, conn):
    policy = _seed_policy(client, conn)
    response = client.post("/v1/jobs/policy.check_project", json={"job_id": "j", "params": {"policy_id": policy["id"]}})
    assert response.json()["outcome"] == "failed"


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

    results = client.get("/v1/policy_check_results", params={"policy_id": policy["id"]}).json()
    assert len(results) == 1

    latest = client.get("/v1/policy_check_results/latest", params={"policy_id": policy["id"], "project_id": "proj-a"}).json()
    assert latest["result"]["outcome"] == "pass"


def test_latest_check_result_is_none_before_any_check(client, conn):
    policy = _seed_policy(client, conn)
    latest = client.get("/v1/policy_check_results/latest", params={"policy_id": policy["id"], "project_id": "proj-a"}).json()
    assert latest["result"] is None
