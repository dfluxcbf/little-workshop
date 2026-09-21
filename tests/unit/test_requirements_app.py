import pytest
from fastapi.testclient import TestClient

from common.paths import Paths
from requirements import db
from requirements.app import build_app


@pytest.fixture
def paths(tmp_path):
    p = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    p.ensure_layout()
    return p


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "requirements.db")
    yield connection
    connection.close()


@pytest.fixture
def client(conn, paths):
    return TestClient(build_app(conn, paths))


def _create_suite(client, project_id="p1", name="core"):
    return client.post(
        "/v1/jobs/requirements.create_test_suite",
        json={"job_id": "j", "project_id": project_id, "params": {"name": name}},
    ).json()["result"]


def _create_requirement(client, project_id="p1", key="REQ-1", title="thing works", **extra):
    params = {"key": key, "title": title, "description": "The system shall work.", **extra}
    return client.post(
        "/v1/jobs/requirements.create_requirement",
        json={"job_id": "j", "project_id": project_id, "params": params},
    ).json()["result"]


def test_health_endpoint(client):
    response = client.get("/v1/health")
    assert response.status_code == 200
    assert response.json()["ok"] is True


# -- test suites -------------------------------------------------------------


def test_create_and_get_test_suite(client):
    suite = _create_suite(client)
    response = client.get(f"/v1/test_suites/{suite['id']}")
    assert response.status_code == 200
    assert response.json()["name"] == "core"


def test_create_test_suite_requires_a_project_id(client):
    response = client.post(
        "/v1/jobs/requirements.create_test_suite", json={"job_id": "j", "params": {"name": "core"}}
    )
    assert response.json()["outcome"] == "failed"


def test_get_test_suite_404s_for_unknown_id(client):
    response = client.get("/v1/test_suites/ghost")
    assert response.status_code == 404


def test_list_test_suites_filters_by_project(client):
    _create_suite(client, project_id="p1", name="a")
    _create_suite(client, project_id="p2", name="b")

    response = client.get("/v1/test_suites", params={"project_id": "p1"})
    assert [s["name"] for s in response.json()] == ["a"]


def test_delete_test_suite_removes_it_and_its_mappings(client):
    suite = _create_suite(client)
    requirement = _create_requirement(client)
    client.post("/v1/jobs/requirements.link_requirement_to_suite", json={
        "job_id": "j", "project_id": "p1", "params": {"requirement_id": requirement["id"], "suite_id": suite["id"]},
    })

    response = client.post("/v1/jobs/requirements.delete_test_suite", json={
        "job_id": "j", "project_id": "p1", "params": {"suite_id": suite["id"]},
    })
    assert response.json()["outcome"] == "succeeded"
    assert client.get(f"/v1/test_suites/{suite['id']}").status_code == 404
    assert client.get(f"/v1/requirements/{requirement['id']}").json()["suite_ids"] == []


def test_delete_test_suite_on_unknown_id_fails_cleanly(client):
    response = client.post("/v1/jobs/requirements.delete_test_suite", json={
        "job_id": "j", "project_id": "p1", "params": {"suite_id": "ghost"},
    })
    assert response.json()["outcome"] == "failed"


# -- use cases / milestones ---------------------------------------------------


def test_create_use_case_and_list_it(client):
    response = client.post("/v1/jobs/requirements.create_use_case", json={
        "job_id": "j", "project_id": "p1", "params": {"name": "checkout flow", "description": "d"},
    })
    assert response.json()["outcome"] == "succeeded"

    listed = client.get("/v1/use_cases", params={"project_id": "p1"}).json()
    assert [u["name"] for u in listed] == ["checkout flow"]


def test_create_milestone_and_list_it(client):
    response = client.post("/v1/jobs/requirements.create_milestone", json={
        "job_id": "j", "project_id": "p1", "params": {"name": "beta"},
    })
    assert response.json()["outcome"] == "succeeded"

    listed = client.get("/v1/milestones", params={"project_id": "p1"}).json()
    assert [m["name"] for m in listed] == ["beta"]


def test_create_use_case_requires_a_name(client):
    response = client.post("/v1/jobs/requirements.create_use_case", json={
        "job_id": "j", "project_id": "p1", "params": {},
    })
    assert response.json()["outcome"] == "failed"


# -- requirements --------------------------------------------------------------


def test_create_requirement_defaults_to_draft(client):
    requirement = _create_requirement(client)
    assert requirement["status"] == "draft"


def test_create_requirement_requires_key_and_title(client):
    response = client.post("/v1/jobs/requirements.create_requirement", json={
        "job_id": "j", "project_id": "p1", "params": {"key": "REQ-1"},
    })
    assert response.json()["outcome"] == "failed"


def test_update_requirement_while_draft_succeeds(client):
    requirement = _create_requirement(client)
    response = client.post("/v1/jobs/requirements.update_requirement", json={
        "job_id": "j", "project_id": "p1", "params": {"requirement_id": requirement["id"], "title": "new title"},
    })
    assert response.json()["outcome"] == "succeeded"
    assert response.json()["result"]["title"] == "new title"


def test_update_requirement_after_publish_is_refused(client):
    requirement = _create_requirement(client)
    client.post("/v1/jobs/requirements.publish_requirement", json={
        "job_id": "j", "project_id": "p1", "params": {"requirement_id": requirement["id"]},
    })
    response = client.post("/v1/jobs/requirements.update_requirement", json={
        "job_id": "j", "project_id": "p1", "params": {"requirement_id": requirement["id"], "title": "new title"},
    })
    assert response.json()["outcome"] == "failed"
    assert "draft" in response.json()["error"]


def test_update_requirement_on_unknown_id_fails_cleanly(client):
    response = client.post("/v1/jobs/requirements.update_requirement", json={
        "job_id": "j", "project_id": "p1", "params": {"requirement_id": "ghost", "title": "x"},
    })
    assert response.json()["outcome"] == "failed"


def test_publish_requirement_locks_status(client):
    requirement = _create_requirement(client)
    response = client.post("/v1/jobs/requirements.publish_requirement", json={
        "job_id": "j", "project_id": "p1", "params": {"requirement_id": requirement["id"]},
    })
    assert response.json()["result"]["status"] == "published"


def test_delete_requirement_removes_it(client):
    requirement = _create_requirement(client)
    response = client.post("/v1/jobs/requirements.delete_requirement", json={
        "job_id": "j", "project_id": "p1", "params": {"requirement_id": requirement["id"]},
    })
    assert response.json()["outcome"] == "succeeded"
    assert client.get(f"/v1/requirements/{requirement['id']}").status_code == 404


def test_link_and_unlink_requirement_to_suite(client):
    suite = _create_suite(client)
    requirement = _create_requirement(client)

    link = client.post("/v1/jobs/requirements.link_requirement_to_suite", json={
        "job_id": "j", "project_id": "p1", "params": {"requirement_id": requirement["id"], "suite_id": suite["id"]},
    })
    assert link.json()["outcome"] == "succeeded"
    assert client.get(f"/v1/requirements/{requirement['id']}").json()["suite_ids"] == [suite["id"]]

    unlink = client.post("/v1/jobs/requirements.unlink_requirement_from_suite", json={
        "job_id": "j", "project_id": "p1", "params": {"requirement_id": requirement["id"], "suite_id": suite["id"]},
    })
    assert unlink.json()["outcome"] == "succeeded"
    assert client.get(f"/v1/requirements/{requirement['id']}").json()["suite_ids"] == []


def test_link_requirement_to_suite_fails_cleanly_for_unknown_requirement_or_suite(client):
    suite = _create_suite(client)
    requirement = _create_requirement(client)

    missing_req = client.post("/v1/jobs/requirements.link_requirement_to_suite", json={
        "job_id": "j", "project_id": "p1", "params": {"requirement_id": "ghost", "suite_id": suite["id"]},
    })
    assert missing_req.json()["outcome"] == "failed"

    missing_suite = client.post("/v1/jobs/requirements.link_requirement_to_suite", json={
        "job_id": "j", "project_id": "p1", "params": {"requirement_id": requirement["id"], "suite_id": "ghost"},
    })
    assert missing_suite.json()["outcome"] == "failed"


def test_list_requirements_filters_by_status(client):
    a = _create_requirement(client, key="REQ-1", title="a")
    client.post("/v1/jobs/requirements.publish_requirement", json={
        "job_id": "j", "project_id": "p1", "params": {"requirement_id": a["id"]},
    })
    _create_requirement(client, key="REQ-2", title="b")

    published = client.get("/v1/requirements", params={"project_id": "p1", "status": "published"}).json()
    assert [r["key"] for r in published] == ["REQ-1"]


def test_get_requirement_404s_for_unknown_id(client):
    assert client.get("/v1/requirements/ghost").status_code == 404


# -- test results --------------------------------------------------------------


def test_record_test_result_manual(client):
    suite = _create_suite(client)
    response = client.post("/v1/jobs/requirements.record_test_result", json={
        "job_id": "j", "project_id": "p1",
        "params": {"suite_id": suite["id"], "test_name": "t1", "verdict": "passed"},
    })
    assert response.json()["outcome"] == "succeeded"
    assert response.json()["result"]["source"] == "manual"


def test_record_test_result_rejects_invalid_verdict(client):
    suite = _create_suite(client)
    response = client.post("/v1/jobs/requirements.record_test_result", json={
        "job_id": "j", "project_id": "p1",
        "params": {"suite_id": suite["id"], "test_name": "t1", "verdict": "not-a-verdict"},
    })
    assert response.json()["outcome"] == "failed"


def test_record_test_result_fails_cleanly_for_unknown_suite(client):
    response = client.post("/v1/jobs/requirements.record_test_result", json={
        "job_id": "j", "project_id": "p1",
        "params": {"suite_id": "ghost", "test_name": "t1", "verdict": "passed"},
    })
    assert response.json()["outcome"] == "failed"


def test_list_test_results_filters_by_suite(client):
    suite_a = _create_suite(client, name="a")
    suite_b = _create_suite(client, name="b")
    client.post("/v1/jobs/requirements.record_test_result", json={
        "job_id": "j", "project_id": "p1", "params": {"suite_id": suite_a["id"], "test_name": "t1", "verdict": "passed"},
    })
    client.post("/v1/jobs/requirements.record_test_result", json={
        "job_id": "j", "project_id": "p1", "params": {"suite_id": suite_b["id"], "test_name": "t2", "verdict": "failed"},
    })

    results = client.get("/v1/test_results", params={"suite_id": suite_a["id"]}).json()
    assert [r["test_name"] for r in results] == ["t1"]


def test_import_junit_records_every_test_case(client):
    suite = _create_suite(client)
    xml = '<testsuite><testcase classname="t" name="a" /><testcase classname="t" name="b"><failure message="boom"/></testcase></testsuite>'
    response = client.post("/v1/jobs/requirements.import_junit", json={
        "job_id": "j", "project_id": "p1", "params": {"suite_id": suite["id"], "xml": xml},
    })
    assert response.json()["outcome"] == "succeeded"
    assert response.json()["result"]["imported"] == 2

    results = client.get("/v1/test_results", params={"suite_id": suite["id"]}).json()
    assert {r["source"] for r in results} == {"junit"}


def test_import_junit_fails_cleanly_on_malformed_xml(client):
    suite = _create_suite(client)
    response = client.post("/v1/jobs/requirements.import_junit", json={
        "job_id": "j", "project_id": "p1", "params": {"suite_id": suite["id"], "xml": "not xml <<<"},
    })
    assert response.json()["outcome"] == "failed"


def test_import_junit_fails_cleanly_for_unknown_suite(client):
    response = client.post("/v1/jobs/requirements.import_junit", json={
        "job_id": "j", "project_id": "p1", "params": {"suite_id": "ghost", "xml": "<testsuite/>"},
    })
    assert response.json()["outcome"] == "failed"


def test_run_tests_fails_cleanly_for_unknown_suite(client):
    response = client.post("/v1/jobs/requirements.run_tests", json={
        "job_id": "j", "project_id": "p1", "params": {"suite_id": "ghost", "commitish": "HEAD"},
    })
    assert response.json()["outcome"] == "failed"


def test_run_tests_surfaces_a_worktree_unavailable_error_cleanly(client, monkeypatch):
    suite = _create_suite(client)
    from requirements import app as app_module
    from common.worktree_client import WorktreeUnavailable

    def _boom(*a, **kw):
        raise WorktreeUnavailable("deployment daemon unreachable")

    monkeypatch.setattr(app_module, "run_sandboxed", _boom)
    response = client.post("/v1/jobs/requirements.run_tests", json={
        "job_id": "j", "project_id": "p1", "params": {"suite_id": suite["id"], "commitish": "HEAD"},
    })
    assert response.json()["outcome"] == "failed"
    assert "unreachable" in response.json()["error"]


# -- definition-of-done report -------------------------------------------------


def test_report_marks_requirement_done_only_when_published_tested_and_passing(client):
    suite = _create_suite(client)
    requirement = _create_requirement(client)
    client.post("/v1/jobs/requirements.link_requirement_to_suite", json={
        "job_id": "j", "project_id": "p1", "params": {"requirement_id": requirement["id"], "suite_id": suite["id"]},
    })

    # Not yet tested or published.
    report = client.get("/v1/report", params={"project_id": "p1"}).json()
    assert report["requirements"][0]["done"] is False
    assert report["done_count"] == 0

    client.post("/v1/jobs/requirements.record_test_result", json={
        "job_id": "j", "project_id": "p1", "params": {"suite_id": suite["id"], "test_name": "t1", "verdict": "passed"},
    })
    # Tested and passing, but still draft.
    report = client.get("/v1/report", params={"project_id": "p1"}).json()
    assert report["requirements"][0]["tested"] is True
    assert report["requirements"][0]["passing"] is True
    assert report["requirements"][0]["done"] is False

    client.post("/v1/jobs/requirements.publish_requirement", json={
        "job_id": "j", "project_id": "p1", "params": {"requirement_id": requirement["id"]},
    })
    report = client.get("/v1/report", params={"project_id": "p1"}).json()
    assert report["requirements"][0]["done"] is True
    assert report["done_count"] == 1


def test_report_is_not_done_when_a_linked_suites_latest_run_failed(client):
    suite = _create_suite(client)
    requirement = _create_requirement(client)
    client.post("/v1/jobs/requirements.link_requirement_to_suite", json={
        "job_id": "j", "project_id": "p1", "params": {"requirement_id": requirement["id"], "suite_id": suite["id"]},
    })
    client.post("/v1/jobs/requirements.publish_requirement", json={
        "job_id": "j", "project_id": "p1", "params": {"requirement_id": requirement["id"]},
    })
    client.post("/v1/jobs/requirements.record_test_result", json={
        "job_id": "j", "project_id": "p1", "params": {"suite_id": suite["id"], "test_name": "t1", "verdict": "failed"},
    })

    report = client.get("/v1/report", params={"project_id": "p1"}).json()
    assert report["requirements"][0]["passing"] is False
    assert report["requirements"][0]["done"] is False


def test_report_requirement_with_no_linked_suite_is_never_tested_or_done(client):
    requirement = _create_requirement(client)
    client.post("/v1/jobs/requirements.publish_requirement", json={
        "job_id": "j", "project_id": "p1", "params": {"requirement_id": requirement["id"]},
    })

    report = client.get("/v1/report", params={"project_id": "p1"}).json()
    assert report["requirements"][0]["tested"] is False
    assert report["requirements"][0]["done"] is False
