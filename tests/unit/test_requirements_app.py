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


@pytest.mark.parametrize("suite_results, expected", [
    ([], "no tests"),                                   # no suite linked
    ([None], "no tests"),                               # linked, never run
    ([["passed", "passed"]], "passed"),
    ([["passed", "skipped"]], "skipped"),
    ([["passed"], None], "no tests"),                   # one of two suites never ran
    ([["passed", "failed"], None], "failed"),           # a failure outranks an unrun suite
    ([["failed"], ["error"]], "error"),
])
def test_report_gives_each_requirement_the_worst_verdict_of_its_linked_suites(client, suite_results, expected):
    requirement = _create_requirement(client)
    for n, results in enumerate(suite_results):
        suite = _create_suite(client, name=f"s{n}")
        client.post("/v1/jobs/requirements.link_requirement_to_suite", json={
            "job_id": "j", "project_id": "p1", "params": {"requirement_id": requirement["id"], "suite_id": suite["id"]},
        })
        for t, verdict in enumerate(results or []):
            client.post("/v1/jobs/requirements.record_test_result", json={
                "job_id": "j", "project_id": "p1",
                "params": {"suite_id": suite["id"], "test_name": f"t{t}", "verdict": verdict, "executed_at": "2026-09-23T10:00:00+00:00"},
            })
    report = client.get("/v1/report", params={"project_id": "p1"}).json()
    assert report["requirements"][0]["verdict"] == expected


# -- discover_tests / run_tests by suite -------------------------------------------------


def _record(client, suite_id, test_name, verdict="passed"):
    client.post("/v1/jobs/requirements.record_test_result", json={
        "job_id": "j", "project_id": "p1", "params": {"suite_id": suite_id, "test_name": test_name, "verdict": verdict},
    })


def test_discover_tests_reports_each_suites_collected_tests(client, monkeypatch):
    from requirements import app as app_module

    module_suite = _create_suite(client, name="test_agent_app")
    feature = _create_suite(client, name="Cook Along")
    _record(client, feature["id"], "tests.test_cook_along::test_start")
    _create_suite(client, name="Audio Pipeline")
    seen = {}

    def fake_collect(paths, project_id, commitish, test_command=None):
        seen.update(project_id=project_id, commitish=commitish)
        return ["tests/unit/test_agent_app.py::test_a", "tests/test_cook_along.py::test_start",
                "tests/test_cook_along.py::test_new_one", "tests/test_other.py::test_x"]

    monkeypatch.setattr(app_module, "collect_sandboxed", fake_collect)
    body = client.post("/v1/jobs/requirements.discover_tests", json={"job_id": "j", "project_id": "p1", "params": {}}).json()
    assert body["outcome"] == "succeeded" and seen == {"project_id": "p1", "commitish": "HEAD"}
    tests = {s["name"]: s["tests"] for s in body["result"]["suites"]}
    assert tests["test_agent_app"] == ["tests/unit/test_agent_app.py::test_a"]
    assert tests["Cook Along"] == ["tests/test_cook_along.py::test_start", "tests/test_cook_along.py::test_new_one"]  # found, never run
    assert tests["Audio Pipeline"] == []
    assert body["result"]["total"] == 4 and body["result"]["unassigned"] == 1
    assert {s["id"] for s in body["result"]["suites"]} >= {module_suite["id"], feature["id"]}


def test_run_tests_by_suite_records_each_result_under_its_suite(client, monkeypatch):
    from requirements import app as app_module
    from requirements.junit import ParsedResult
    from requirements.models import TestVerdict

    a = _create_suite(client, name="test_a")
    b = _create_suite(client, name="test_b")
    got = {}

    def fake_run(paths, project_id, commitish, scopes, test_command=None):
        got.update(scopes=scopes, commitish=commitish)
        return {
            a["id"]: [ParsedResult("tests.test_a::t1", TestVerdict.PASSED, ""), ParsedResult("tests.test_a::t2", TestVerdict.ERROR, "x")],
            b["id"]: [ParsedResult("tests.test_b::t1", TestVerdict.SKIPPED, "")],
        }

    monkeypatch.setattr(app_module, "run_suites_sandboxed", fake_run)
    body = client.post("/v1/jobs/requirements.run_tests", json={
        "job_id": "j", "project_id": "p1", "params": {"suite_ids": [a["id"], b["id"]]}}).json()
    assert body["outcome"] == "succeeded"
    assert got["scopes"] == {a["id"]: {"test_a"}, b["id"]: {"test_b"}} and got["commitish"] == "HEAD"
    assert {s["name"]: s["verdicts"] for s in body["result"]["suites"]} == {"test_a": {"passed": 1, "error": 1}, "test_b": {"skipped": 1}}
    recorded = client.get("/v1/test_results", params={"project_id": "p1", "suite_id": a["id"]}).json()
    assert sorted((r["test_name"], r["verdict"]) for r in recorded) == [("tests.test_a::t1", "passed"), ("tests.test_a::t2", "error")]


@pytest.mark.parametrize("suite_ids, error", [
    ([], "non-empty list"),
    ("abc", "non-empty list"),
    (["nope"], "test suite 'nope' not found in p1"),
])
def test_run_tests_by_suite_refuses_bad_suite_ids(client, suite_ids, error):
    body = client.post("/v1/jobs/requirements.run_tests", json={"job_id": "j", "project_id": "p1", "params": {"suite_ids": suite_ids}}).json()
    assert body["outcome"] == "failed" and error in body["error"]


# --- REQ-000000448: superseding a requirement is one call ---------------------------------------

REGISTER = "| ID | Description | Status |\n| --- | --- | --- |\n| REQ-000000001 | **Thing works.** The system shall work. | M3 |\n| REQ-000000009 | **Other.** x | done |\n"


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    root = tmp_path / "checkout"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "requirements.md").write_text(REGISTER)
    (root / "tests").mkdir()
    (root / "tests" / "test_thing.py").write_text("# " + "--- REQ-000000001: thing works ---\ndef test_x(): pass\n")
    monkeypatch.setattr("requirements.app.resolve_project_path", lambda paths, project_id: root)
    return root


def _supersede(client, requirement_id, key="REQ-000000002", description="The system shall work blue.", **extra):
    return client.post(
        "/v1/jobs/requirements.supersede_requirement",
        json={"job_id": "j", "project_id": "p1", "params": {"requirement_id": requirement_id, "key": key,
                                                              "description": description, **extra}},
    ).json()


def test_supersede_replaces_a_published_requirement_in_one_call(client, checkout):
    suite = _create_suite(client)
    old = _create_requirement(client, key="REQ-000000001", category="look")
    client.post("/v1/jobs/requirements.link_requirement_to_suite",
                json={"job_id": "j", "project_id": "p1", "params": {"requirement_id": old["id"], "suite_id": suite["id"]}})
    client.post("/v1/jobs/requirements.publish_requirement", json={"job_id": "j", "project_id": "p1", "params": {"requirement_id": old["id"]}})

    body = _supersede(client, old["id"])
    assert body["outcome"] == "succeeded"
    result = body["result"]
    assert result["superseded"]["status"] == "superseded" and result["superseded"]["superseded_by"] == "REQ-000000002"
    new = result["requirement"]
    assert (new["key"], new["status"], new["title"], new["category"]) == ("REQ-000000002", "draft", "thing works", "look")
    assert client.get(f"/v1/requirements/{new['id']}").json()["suite_ids"] == [suite["id"]]
    assert result["register_updated"] is True and result["tests_still_tagged"] == ["tests/test_thing.py"]
    lines = (checkout / "docs" / "requirements.md").read_text().splitlines()
    assert lines[2] == "| REQ-000000001 | **Thing works.** The system shall work. | M3, superseded by REQ-000000002 |"
    assert lines[3] == "| REQ-000000002 | **thing works.** The system shall work blue. | draft |"
    assert lines[4].startswith("| REQ-000000009 |")

    report = client.get("/v1/report", params={"project_id": "p1"}).json()
    assert [r["key"] for r in report["requirements"]] == ["REQ-000000002"]
    refused = client.post("/v1/jobs/requirements.publish_requirement",
                          json={"job_id": "j", "project_id": "p1", "params": {"requirement_id": old["id"]}}).json()
    assert refused["outcome"] == "failed" and "superseded by REQ-000000002" in refused["error"]


def test_supersede_refuses_a_superseded_requirement_and_a_taken_key(client, checkout):
    old = _create_requirement(client)
    _create_requirement(client, key="REQ-000000009", title="other")
    assert "already taken" in _supersede(client, old["id"], key="REQ-000000009")["error"]
    assert _supersede(client, old["id"])["outcome"] == "succeeded"
    assert "already superseded by REQ-000000002" in _supersede(client, old["id"], key="REQ-000000003")["error"]


def test_supersede_without_a_register_row_or_a_checkout_still_replaces_the_requirement(client, monkeypatch):
    from common.registry_client import ProjectResolutionError

    def unresolvable(paths, project_id):
        raise ProjectResolutionError("no checkout")

    monkeypatch.setattr("requirements.app.resolve_project_path", unresolvable)
    old = _create_requirement(client)
    result = _supersede(client, old["id"])["result"]
    assert result["register_updated"] is False and result["tests_still_tagged"] is None
    assert result["superseded"]["status"] == "superseded"


# --- REQ-000000450: the EARS form is advice, never a refusal ------------------------------------

def test_a_description_not_in_ears_form_is_kept_with_a_warning(client, checkout):
    created = client.post("/v1/jobs/requirements.create_requirement", json={"job_id": "j", "project_id": "p1", "params": {
        "key": "REQ-000000005", "title": "seasons", "description": "When a game starts, new or loaded, it shall add the designs."}}).json()
    assert created["outcome"] == "succeeded" and created["result"]["description"].startswith("When a game starts")
    assert "EARS" in created["result"]["warnings"][0]
    assert _create_requirement(client, key="REQ-000000006")["warnings"] == []
    updated = client.post("/v1/jobs/requirements.update_requirement", json={"job_id": "j", "project_id": "p1", "params": {
        "requirement_id": created["result"]["id"], "description": "make it blue"}}).json()
    assert updated["outcome"] == "succeeded" and updated["result"]["warnings"]
    superseded = _supersede(client, created["result"]["id"], key="REQ-000000007", description="make it green")
    assert superseded["outcome"] == "succeeded" and superseded["result"]["warnings"]

