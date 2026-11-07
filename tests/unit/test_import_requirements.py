"""tools/import_requirements.py, run against the real Requirements pillar app."""

from __future__ import annotations

import importlib.util
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from common.paths import Paths
from requirements import db
from requirements.app import build_app

_spec = importlib.util.spec_from_file_location("import_requirements", Path(__file__).resolve().parents[2] / "tools" / "import_requirements.py")
imp = importlib.util.module_from_spec(_spec)
sys.modules["import_requirements"] = imp
_spec.loader.exec_module(imp)


@pytest.fixture
def pillar_client(tmp_path):
    paths = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    paths.ensure_layout()
    conn = db.connect(tmp_path / "requirements.db")
    yield TestClient(build_app(conn, paths))
    conn.close()


def junit(*cases):
    """cases: (classname, name, outcome) with outcome in passed|failed|skipped."""
    suite = ET.Element("testsuite")
    for classname, name, outcome in cases:
        case = ET.SubElement(suite, "testcase", {"classname": classname, "name": name})
        if outcome == "failed":
            ET.SubElement(case, "failure", {"message": "boom"})
        elif outcome == "skipped":
            ET.SubElement(case, "skipped", {"message": "no"})
    return suite


def write_junit(tmp_path, *cases):
    path = tmp_path / "report.xml"
    ET.ElementTree(junit(*cases)).write(path)
    return path


CASES = (
    ("tests.test_x", "test_a", "passed"), ("tests.test_x.TestC", "test_b", "passed"),
    ("tests.test_y", "test_c", "passed"), ("tests.test_z", "test_d", "passed"),
)


# -- parsing helpers ---------------------------------------------------------------------


def test_register_parsing_and_titles():
    text = "## M1 — Core\n\n| REQ-000000001 | `x` loads. More detail follows here. | done |\n| REQ-000000002 | Later thing | not done |\n## M2\n| REQ-000000003 | Third | done |\nnot a row\n"
    rows = imp.parse_register(text)
    assert [(r["key"], r["done"], r["section"]) for r in rows] == [("REQ-000000001", True, "M1 — Core"), ("REQ-000000002", False, "M1 — Core"), ("REQ-000000003", True, "M2")]
    assert imp.shorten("`x` loads. More detail follows here.") == "x loads."
    assert len(imp.shorten("word " * 100)) <= 110 and imp.shorten("word " * 100).endswith("…")


def test_a_pillar_refusal_is_reported_not_swallowed(pillar_client):
    with pytest.raises(imp.ImportFailed, match="create_requirement failed"):
        imp.Pillar(pillar_client).job("requirements.create_requirement", "p", key="", title="")


# -- the register import -----------------------------------------------------------------


REGISTER = (
    "## M1 — Core\n| REQ-000000001 | Loads config. Details. | done |\n| REQ-000000002 | Not yet | not done |\n"
    "## M2 — More\n| REQ-000000003 | Another | done |\n"
)


def run_register(client, cases):
    junit_cases = [imp.Case(t.get("classname"), t.get("name"), t) for t in junit(*cases).iter("testcase")]
    return imp.import_register(imp.Pillar(client), "w", imp.parse_register(REGISTER), junit_cases)


def test_register_import_creates_milestones_requirements_and_one_unit_suite(pillar_client):
    cases = (("tests.unit.test_a", "test_1", "passed"), ("tests.unit.test_b", "test_2", "passed"))
    summary = run_register(pillar_client, cases)
    assert summary["requirements_created"] == 3 and summary["requirements_published"] == 2 and summary["links_created"] == 2
    assert summary["milestones_created"] == 2 and summary["results_imported"] == 2
    rows = {r["key"]: r for r in pillar_client.get("/v1/report", params={"project_id": "w"}).json()["requirements"]}
    assert rows["REQ-000000001"]["done"] is True and rows["REQ-000000003"]["done"] is True
    assert rows["REQ-000000002"]["status"] == "draft" and rows["REQ-000000002"]["suite_ids"] == []  # not done: never linked
    reqs = pillar_client.get("/v1/requirements", params={"project_id": "w"}).json()
    assert next(r for r in reqs if r["key"] == "REQ-000000001")["title"] == "Loads config."
    assert all(r["milestone_id"] for r in reqs)


def test_register_import_is_idempotent_and_a_failure_turns_everything_not_done(pillar_client):
    run_register(pillar_client, (("tests.unit.test_a", "test_1", "passed"),))
    again = run_register(pillar_client, (("tests.unit.test_a", "test_1", "failed"),))
    assert (again["requirements_created"], again["suites_created"], again["links_created"], again["milestones_created"] if "milestones_created" in again else 0) == (0, 0, 0, 0)
    assert pillar_client.get("/v1/report", params={"project_id": "w"}).json()["done_count"] == 0


# -- module suites: real per-test-file grouping for the Report page's Tests tab ----------


def test_module_name_drops_a_trailing_class_and_keeps_the_module():
    assert imp.module_name(imp.Case("tests.unit.test_common_shell", "test_a", None)) == "test_common_shell"
    assert imp.module_name(imp.Case("tests.unit.test_common_shell.TestFoo", "test_a", None)) == "test_common_shell"
    assert imp.module_name(imp.Case("", "test_a", None)) == "test_a"


def test_import_module_suites_creates_one_suite_per_module_and_records_results(pillar_client, tmp_path):
    path = write_junit(tmp_path, *CASES)
    cases = imp.read_junit(path)
    pillar = imp.Pillar(pillar_client)
    summary = imp.start_summary("p", "module-suites")
    imp.import_module_suites(pillar, "p", cases, summary)

    suites = {s["name"]: s for s in pillar_client.get("/v1/test_suites", params={"project_id": "p"}).json()}
    assert set(suites) == {"test_x", "test_y", "test_z"}  # TestC's tests.test_x.TestC case folds into test_x
    results = pillar_client.get("/v1/test_results", params={"project_id": "p", "suite_id": suites["test_x"]["id"]}).json()
    assert {r["test_name"] for r in results} == {"tests.test_x::test_a", "tests.test_x.TestC::test_b"}
    assert summary["suites_created"] == 3 and summary["results_imported"] == 4


def test_import_module_suites_never_links_a_requirement(pillar_client, tmp_path):
    path = write_junit(tmp_path, *CASES)
    cases = imp.read_junit(path)
    pillar = imp.Pillar(pillar_client)
    req = pillar_client.post("/v1/jobs/requirements.create_requirement", json={
        "job_id": "j", "project_id": "p", "params": {"key": "REQ-1", "title": "t", "description": "The system shall work."}}).json()["result"]
    imp.import_module_suites(pillar, "p", cases, imp.start_summary("p", "module-suites"))
    assert pillar_client.get("/v1/report", params={"project_id": "p"}).json()["requirements"][0]["suite_ids"] == []
    assert req["id"]  # sanity: the requirement really was created


def test_import_module_suites_is_idempotent(pillar_client, tmp_path):
    path = write_junit(tmp_path, *CASES)
    cases = imp.read_junit(path)
    pillar = imp.Pillar(pillar_client)
    imp.import_module_suites(pillar, "p", cases, imp.start_summary("p", "module-suites"))
    imp.import_module_suites(pillar, "p", cases, imp.start_summary("p", "module-suites"))
    suites = pillar_client.get("/v1/test_suites", params={"project_id": "p"}).json()
    assert len(suites) == 3
