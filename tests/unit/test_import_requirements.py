"""tools/import_requirements.py, run against the real Requirements pillar app."""

from __future__ import annotations

import hashlib
import importlib.util
import sqlite3
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


def sha(node_id):
    return hashlib.sha256(node_id.encode()).hexdigest()


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


def make_lreq(tmp_path, *, statuses=("published", "draft")):
    """A tiny old-style database: 2 requirements in 2 suites; tests a, b -> REQ-1, test c -> REQ-2, test d unlinked."""
    path = tmp_path / "lreq.db"
    path.unlink(missing_ok=True)
    c = sqlite3.connect(path)
    c.executescript("""
        CREATE TABLE requirements_table (requirement_id TEXT, requirement_name TEXT, description TEXT, lifecycle_status TEXT);
        CREATE TABLE suites_table (suite_id TEXT, suite_name TEXT, project_id TEXT);
        CREATE TABLE suite_requirements_table (suite_id TEXT, requirement_id TEXT);
        CREATE TABLE requirement_test_table (requirement_id TEXT, test_case_id TEXT);
        CREATE TABLE test_cases_table (test_case_id TEXT, verdict INTEGER, last_tested_version TEXT);
    """)
    c.executemany("INSERT INTO requirements_table VALUES (?,?,?,?)", [
        ("REQ-000000001", "First", "the first", statuses[0]), ("REQ-000000002", "Second", "the second", statuses[1]),
    ])
    c.executemany("INSERT INTO suites_table VALUES (?,?,?)", [("s1", "Suite One", "p"), ("s2", "Suite Two", "p")])
    c.executemany("INSERT INTO suite_requirements_table VALUES (?,?)", [("s1", "REQ-000000001"), ("s2", "REQ-000000002")])
    c.executemany("INSERT INTO requirement_test_table VALUES (?,?)", [
        ("REQ-000000001", sha("tests/test_x.py::test_a")), ("REQ-000000001", sha("tests/test_x.py::TestC::test_b")),
        ("REQ-000000002", sha("tests/test_y.py::test_c")),
    ])
    c.executemany("INSERT INTO test_cases_table VALUES (?,?,?)", [(sha(n), 1, "0.1.0") for n in (
        "tests/test_x.py::test_a", "tests/test_x.py::TestC::test_b", "tests/test_y.py::test_c", "tests/test_gone.py::test_old",
    )])
    c.commit(); c.close()
    return path


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    (root / "tests").mkdir(parents=True)
    for f in ("test_x.py", "test_y.py", "test_z.py"):
        (root / "tests" / f).write_text("", encoding="utf-8")
    return root


CASES = (
    ("tests.test_x", "test_a", "passed"), ("tests.test_x.TestC", "test_b", "passed"),
    ("tests.test_y", "test_c", "passed"), ("tests.test_z", "test_d", "passed"),
)


def report(client, project="p"):
    return {r["key"]: r for r in client.get("/v1/report", params={"project_id": project}).json()["requirements"]}


# -- parsing helpers ---------------------------------------------------------------------


def test_pytest_node_ids_are_rebuilt_from_junit_classnames(repo):
    Case = imp.Case
    assert imp.pytest_node_id(repo, Case("tests.test_x", "test_a", None)) == "tests/test_x.py::test_a"
    assert imp.pytest_node_id(repo, Case("tests.test_x.TestC", "test_b[1]", None)) == "tests/test_x.py::TestC::test_b[1]"
    assert imp.pytest_node_id(repo, Case("tests.nope", "t", None)) is None
    assert imp.pytest_node_id(None, Case("tests.test_x", "t", None)) is None


def test_auto_style_picks_whichever_reproduces_the_old_ids(repo):
    cases = [imp.Case(t.get("classname"), t.get("name"), t) for t in junit(*CASES).iter("testcase")]
    known = {sha("tests/test_x.py::test_a")}
    by_hash, style = imp.hash_cases(cases, known, repo, "auto")
    assert style == "pytest" and sha("tests/test_x.py::test_a") in by_hash
    generic = {sha("tests.test_x::test_a")}
    _, style = imp.hash_cases(cases, generic, repo, "auto")
    assert style == "generic"


def test_register_parsing_and_titles():
    text = "## M1 — Core\n\n| REQ-000000001 | `x` loads. More detail follows here. | done |\n| REQ-000000002 | Later thing | not done |\n## M2\n| REQ-000000003 | Third | done |\nnot a row\n"
    rows = imp.parse_register(text)
    assert [(r["key"], r["done"], r["section"]) for r in rows] == [("REQ-000000001", True, "M1 — Core"), ("REQ-000000002", False, "M1 — Core"), ("REQ-000000003", True, "M2")]
    assert imp.shorten("`x` loads. More detail follows here.") == "x loads."
    assert len(imp.shorten("word " * 100)) <= 110 and imp.shorten("word " * 100).endswith("…")


# -- the lreq import ---------------------------------------------------------------------


def run_lreq(client, tmp_path, repo, cases=CASES, dry=False, **kw):
    lreq = imp.load_lreq(make_lreq(tmp_path, **kw))
    junit_cases = [imp.Case(t.get("classname"), t.get("name"), t) for t in junit(*cases).iter("testcase")]
    return imp.import_lreq(imp.Pillar(client, dry_run=dry), "p", lreq, junit_cases, repo)


def test_lreq_import_recreates_requirements_suites_links_and_results(pillar_client, tmp_path, repo):
    summary = run_lreq(pillar_client, tmp_path, repo)
    assert summary["requirements_created"] == 2 and summary["requirements_published"] == 1
    assert summary["suites_created"] == 3 and summary["links_created"] == 2
    assert summary["id_style"] == "pytest" and summary["tests_matched_to_lreq"] == 3 and summary["unlinked_tests"] == 1
    assert summary["warnings"] and "1 test(s) known to the old database were not in this run" in summary["warnings"][0]

    suites = {s["name"]: s["id"] for s in pillar_client.get("/v1/test_suites", params={"project_id": "p"}).json()}
    assert set(suites) == {"Suite One", "Suite Two", imp.UNLINKED_SUITE}
    counts = {name: len(pillar_client.get("/v1/test_results", params={"project_id": "p", "suite_id": sid}).json()) for name, sid in suites.items()}
    assert counts == {"Suite One": 2, "Suite Two": 1, imp.UNLINKED_SUITE: 1}

    rows = report(pillar_client)
    assert rows["REQ-000000001"]["done"] is True  # published, linked, its suite's latest run passed
    assert rows["REQ-000000002"]["status"] == "draft" and rows["REQ-000000002"]["done"] is False  # draft stays draft


def test_requirement_titles_and_descriptions_come_from_the_old_database(pillar_client, tmp_path, repo):
    run_lreq(pillar_client, tmp_path, repo)
    first = next(r for r in pillar_client.get("/v1/requirements", params={"project_id": "p"}).json() if r["key"] == "REQ-000000001")
    assert first["title"] == "First" and first["description"] == "the first"


def test_a_failing_test_keeps_its_requirement_from_being_done(pillar_client, tmp_path, repo):
    cases = (("tests.test_x", "test_a", "failed"), ("tests.test_x.TestC", "test_b", "passed"), ("tests.test_y", "test_c", "passed"))
    run_lreq(pillar_client, tmp_path, repo, cases=cases)
    assert report(pillar_client)["REQ-000000001"]["done"] is False


def test_a_skipped_test_also_blocks_done(pillar_client, tmp_path, repo):
    cases = (("tests.test_x", "test_a", "skipped"), ("tests.test_x.TestC", "test_b", "passed"), ("tests.test_y", "test_c", "passed"))
    run_lreq(pillar_client, tmp_path, repo, cases=cases)
    assert report(pillar_client)["REQ-000000001"]["done"] is False


def test_rerunning_creates_nothing_new_but_records_a_fresh_run(pillar_client, tmp_path, repo):
    run_lreq(pillar_client, tmp_path, repo)
    second = run_lreq(pillar_client, tmp_path, repo)
    assert (second["requirements_created"], second["requirements_published"], second["suites_created"], second["links_created"]) == (0, 0, 0, 0)
    assert second["results_imported"] == 4
    assert len(pillar_client.get("/v1/requirements", params={"project_id": "p"}).json()) == 2
    assert len(pillar_client.get("/v1/test_suites", params={"project_id": "p"}).json()) == 3


def test_a_later_run_that_fails_turns_a_done_requirement_back(pillar_client, tmp_path, repo):
    run_lreq(pillar_client, tmp_path, repo)
    assert report(pillar_client)["REQ-000000001"]["done"] is True
    bad = (("tests.test_x", "test_a", "failed"), ("tests.test_x.TestC", "test_b", "passed"), ("tests.test_y", "test_c", "passed"))
    run_lreq(pillar_client, tmp_path, repo, cases=bad)
    assert report(pillar_client)["REQ-000000001"]["done"] is False


def test_a_requirement_published_in_the_old_database_is_published_on_a_rerun(pillar_client, tmp_path, repo):
    run_lreq(pillar_client, tmp_path, repo, statuses=("draft", "draft"))
    assert report(pillar_client)["REQ-000000001"]["status"] == "draft"
    again = run_lreq(pillar_client, tmp_path, repo, statuses=("published", "draft"))
    assert again["requirements_published"] == 1 and report(pillar_client)["REQ-000000001"]["status"] == "published"


def test_dry_run_reports_the_plan_and_writes_nothing(pillar_client, tmp_path, repo):
    summary = run_lreq(pillar_client, tmp_path, repo, dry=True)
    assert summary["requirements_created"] == 2 and summary["suites_created"] == 3 and summary["results_imported"] == 4
    assert pillar_client.get("/v1/requirements", params={"project_id": "p"}).json() == []
    assert pillar_client.get("/v1/test_suites", params={"project_id": "p"}).json() == []


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
